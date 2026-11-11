"""
Phase 0 spike: can a PySide6 grid browse 10k-20k wallpapers smoothly with
bounded memory?

What it does:
- scans the folder(s) with os.scandir only (never opens images while listing)
- QListView (icon mode, uniform item sizes) over a QAbstractListModel:
  only visible tiles are ever painted or requested
- thumbnails are decoded in worker threads with Pillow (JPEG "draft" decodes
  directly at reduced scale), newest request first; requests for tiles that
  scrolled out of view are dropped before decoding
- in-memory LRU of thumbnails with a byte budget + JPEG disk cache

Usage:
    python spikes/grid_prototype.py D:/pictures/KONACHAN [--repeat 2]
        [--budget-mb 100] [--workers N] [--no-disk-cache]

--repeat 2 lists every file twice to simulate a 20k folder.
The status bar shows queue sizes, decode timings per file type and process
memory. "Drop caches" clears the in-memory thumbnails so you can see how much
memory is given back (what closing the library window would do).
"""

from __future__ import annotations

import argparse
import ctypes
import gc
import hashlib
import os
import platform
import sys
import threading
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPoint,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QListView,
    QMainWindow,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolBar,
)

THUMB_W, THUMB_H = 320, 180
SPACING = 6
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
CACHE_DIR = (
    Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache")
    / "wallpaper_py"
    / "spike-thumbs"
)


# --------------------------------------------------------------------- memory
def process_memory_mb() -> float:
    if platform.system() == "Windows":

        class PMC(ctypes.Structure):  # PROCESS_MEMORY_COUNTERS_EX
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        psapi = ctypes.windll.psapi  # type: ignore[attr-defined]
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_ulong,
        ]
        psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return pmc.PrivateUsage / 1e6
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1e3
    except OSError:
        pass
    return -1.0


# --------------------------------------------------------------------- decode
def cache_path(path: Path, mtime: float, size: int) -> Path:
    key = hashlib.sha1(f"{path}|{mtime}|{size}".encode()).hexdigest()
    return CACHE_DIR / key[:2] / f"{key}.jpg"


def decode_thumbnail(path: Path) -> Image.Image:
    """Center-crop ("fill") thumbnail, decoded as small as possible."""
    with Image.open(path) as img:
        img.draft("RGB", (THUMB_W, THUMB_H))  # JPEG: decode at 1/2..1/8 scale
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        src_ratio = img.width / img.height
        dst_ratio = THUMB_W / THUMB_H
        if src_ratio > dst_ratio:  # wider: crop sides
            w = img.height * dst_ratio
            box = ((img.width - w) / 2, 0, (img.width + w) / 2, img.height)
        else:  # taller: crop top/bottom
            h = img.width / dst_ratio
            box = (0, (img.height - h) / 2, img.width, (img.height + h) / 2)
        thumb = img.resize(
            (THUMB_W, THUMB_H), Image.Resampling.BILINEAR, box=box, reducing_gap=2.0
        )
        return thumb.convert("RGB")


def pil_to_qimage(img: Image.Image) -> QImage:
    data = img.tobytes("raw", "RGB")
    qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format.Format_RGB888)
    return qimg.copy()  # detach from the Python bytes buffer


# --------------------------------------------------------------------- loader
@dataclass
class Stats:
    decoded: dict[str, list[float]]
    disk_hits: int = 0
    dropped: int = 0
    errors: int = 0


class ThumbnailLoader(QObject):
    """LIFO request stack + worker threads; drops requests no longer visible."""

    loaded = Signal(int, QImage)  # row, image (emitted from worker threads)

    def __init__(self, files: list[Path], workers: int, disk_cache: bool) -> None:
        super().__init__()
        self._files = files
        self._disk_cache = disk_cache
        self._stack: list[int] = []
        self._pending: set[int] = set()
        self._cond = threading.Condition()
        self._visible = (0, -1)
        self._stop = False
        self.in_flight = 0
        self.stats = Stats(decoded=defaultdict(list))
        self._threads = [
            threading.Thread(target=self._run, daemon=True, name=f"thumb-{i}")
            for i in range(workers)
        ]
        for t in self._threads:
            t.start()

    def set_visible_range(self, first: int, last: int) -> None:
        self._visible = (first, last)

    def request(self, row: int) -> None:
        with self._cond:
            if row in self._pending:
                return
            self._pending.add(row)
            self._stack.append(row)
            self._cond.notify()

    def queued(self) -> int:
        return len(self._stack)

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()

    def _next(self) -> int | None:
        with self._cond:
            while True:
                if self._stop:
                    return None
                while self._stack:
                    row = self._stack.pop()  # newest first
                    first, last = self._visible
                    if first <= row <= last:
                        self.in_flight += 1
                        return row
                    self._pending.discard(row)  # scrolled away: drop
                    self.stats.dropped += 1
                self._cond.wait()

    def _run(self) -> None:
        while (row := self._next()) is not None:
            try:
                self.loaded.emit(row, self._load(self._files[row]))
            except Exception:  # pylint: disable=broad-except
                self.stats.errors += 1
                self.loaded.emit(row, QImage())
            finally:
                with self._cond:
                    self.in_flight -= 1
                    self._pending.discard(row)

    def _load(self, path: Path) -> QImage:
        st = path.stat()
        cached = cache_path(path, st.st_mtime, st.st_size)
        if self._disk_cache and cached.exists():
            self.stats.disk_hits += 1
            return QImage(str(cached))
        t0 = time.perf_counter()
        thumb = decode_thumbnail(path)
        self.stats.decoded[path.suffix.lower()].append(time.perf_counter() - t0)
        if self._disk_cache:
            cached.parent.mkdir(parents=True, exist_ok=True)
            thumb.save(cached, "JPEG", quality=85)
        return pil_to_qimage(thumb)


# ---------------------------------------------------------------------- model
class ThumbCache:
    """LRU of QPixmaps bounded by bytes."""

    def __init__(self, budget_bytes: int) -> None:
        self.budget = budget_bytes
        self.used = 0
        self._items: OrderedDict[int, QPixmap] = OrderedDict()

    def get(self, row: int) -> QPixmap | None:
        pix = self._items.get(row)
        if pix is not None:
            self._items.move_to_end(row)
        return pix

    def put(self, row: int, pix: QPixmap) -> None:
        self._items[row] = pix
        self.used += pix.width() * pix.height() * pix.depth() // 8
        while self.used > self.budget and len(self._items) > 1:
            _, old = self._items.popitem(last=False)
            self.used -= old.width() * old.height() * old.depth() // 8

    def clear(self) -> None:
        self._items.clear()
        self.used = 0

    def __len__(self) -> int:
        return len(self._items)


class WallpaperModel(QAbstractListModel):
    def __init__(self, files: list[Path], loader: ThumbnailLoader, cache: ThumbCache):
        super().__init__()
        self.files = files
        self.loader = loader
        self.cache = cache
        loader.loaded.connect(self._on_loaded)

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.files)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = 0) -> object:
        row = index.row()
        if role == Qt.ItemDataRole.DecorationRole:
            pix = self.cache.get(row)
            if pix is None:
                self.loader.request(row)
            return pix
        if role == Qt.ItemDataRole.ToolTipRole:
            return self.files[row].name
        return None

    def _on_loaded(self, row: int, image: QImage) -> None:
        if image.isNull():
            image = QImage(THUMB_W, THUMB_H, QImage.Format.Format_RGB32)
            image.fill(QColor("#802020"))
        self.cache.put(row, QPixmap.fromImage(image))
        idx = self.index(row)
        self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])


class TileDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:  # type: ignore[override]
        rect: QRect = option.rect  # type: ignore[attr-defined]
        pix = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(pix, QPixmap):
            painter.drawPixmap(rect, pix)
        else:
            painter.fillRect(rect, QColor("#2a2a2a"))
        if option.state & QStyle.StateFlag.State_Selected:  # type: ignore[attr-defined]
            painter.setPen(QColor("#4da3ff"))
            painter.drawRect(rect.adjusted(1, 1, -2, -2))

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # type: ignore[override]
        return QSize(THUMB_W, THUMB_H)


# --------------------------------------------------------------------- window
def scan(folders: list[Path]) -> list[Path]:
    files: list[Path] = []
    for folder in folders:
        with os.scandir(folder) as it:
            for entry in it:
                if entry.is_file() and Path(entry.name).suffix.lower() in IMAGE_EXTS:
                    files.append(Path(entry.path))
    files.sort(key=lambda p: p.name.lower())
    return files


class Window(QMainWindow):
    def __init__(self, files: list[Path], args: argparse.Namespace, scan_s: float):
        super().__init__()
        self.setWindowTitle(f"Grid prototype - {len(files)} images")
        self.loader = ThumbnailLoader(files, args.workers, not args.no_disk_cache)
        self.cache = ThumbCache(args.budget_mb * 1_000_000)
        self.model = WallpaperModel(files, self.loader, self.cache)
        self.scan_s = scan_s

        view = QListView()
        view.setViewMode(QListView.ViewMode.IconMode)
        view.setResizeMode(QListView.ResizeMode.Adjust)
        view.setMovement(QListView.Movement.Static)
        view.setUniformItemSizes(True)
        view.setSpacing(SPACING)
        view.setSelectionMode(QListView.SelectionMode.SingleSelection)
        view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        view.verticalScrollBar().setSingleStep(40)
        view.setItemDelegate(TileDelegate())
        view.setStyleSheet("QListView { background: #181818; }")
        t0 = time.perf_counter()
        view.setModel(self.model)
        self.layout_s = time.perf_counter() - t0
        self.view = view
        self.setCentralWidget(view)

        bar = QToolBar()
        bar.addAction("Drop caches", self.drop_caches)
        bar.addAction("Top", view.scrollToTop)
        bar.addAction("Bottom", view.scrollToBottom)
        self.addToolBar(bar)

        view.verticalScrollBar().valueChanged.connect(self.update_visible)
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.update_status)
        self.status_timer.start(500)
        self.resize(1400, 900)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        QTimer.singleShot(0, self.update_visible)

    def update_visible(self) -> None:
        vp = self.view.viewport().rect()
        pad = QPoint(SPACING + 1, SPACING + 1)
        first = self.view.indexAt(vp.topLeft() + pad)
        last = self.view.indexAt(vp.bottomRight() - pad)
        f = first.row() if first.isValid() else 0
        l = last.row() if last.isValid() else len(self.model.files) - 1
        per_row = max(1, vp.width() // (THUMB_W + 2 * SPACING))
        margin = per_row * 2  # keep one or two rows above/below
        self.loader.set_visible_range(max(0, f - margin), l + margin)

    def drop_caches(self) -> None:
        self.cache.clear()
        gc.collect()
        self.view.viewport().update()

    def status_text(self) -> str:
        return self.statusBar().currentMessage()

    def is_idle(self) -> bool:
        return self.loader.queued() == 0 and self.loader.in_flight == 0

    def run_auto(self) -> None:
        """Scripted benchmark: open, fast-scroll, jump around, drop caches."""
        sb = self.view.verticalScrollBar()
        steps: list[tuple[str, object]] = [
            ("initial screen", None),
            ("fast scroll 300 steps", lambda i: sb.setValue(sb.value() + 120)),
            ("jump to 50%", lambda: sb.setValue(sb.maximum() // 2)),
            ("jump to bottom", self.view.scrollToBottom),
            ("drag-like sweep top->bottom", lambda i: sb.setValue(sb.maximum() * i // 300)),
        ]
        results: list[str] = []

        def wait_idle(label: str, started: float, then) -> None:  # type: ignore[no-untyped-def]
            def check() -> None:
                if self.is_idle() and time.perf_counter() - started > 0.3:
                    self.update_status()
                    results.append(
                        f"[{label}] settled in {time.perf_counter() - started:.2f}s\n    "
                        + self.status_text()
                    )
                    then()
                else:
                    QTimer.singleShot(50, check)

            QTimer.singleShot(50, check)

        def run_step(i: int) -> None:
            if i == len(steps):
                self.drop_caches()
                QTimer.singleShot(500, finish)
                return
            label, action = steps[i]
            started = time.perf_counter()
            if action is None:
                wait_idle(label, started, lambda: run_step(i + 1))
            elif label.startswith(("fast", "drag")):
                def tick(n: int = 0) -> None:
                    if n < 300:
                        action(n)  # type: ignore[operator]
                        QTimer.singleShot(5, lambda: tick(n + 1))
                    else:
                        wait_idle(label, started, lambda: run_step(i + 1))

                tick()
            else:
                action()  # type: ignore[operator]
                wait_idle(label, started, lambda: run_step(i + 1))

        def finish() -> None:
            self.update_status()
            results.append("[after drop caches]\n    " + self.status_text())
            print("\n".join(results), flush=True)
            QApplication.quit()

        run_step(0)

    def update_status(self) -> None:
        s = self.loader.stats
        timings = "  ".join(
            f"{ext} {len(v)}× {sum(v) / len(v) * 1000:.0f}ms"
            for ext, v in sorted(s.decoded.items())
            if v
        )
        self.statusBar().showMessage(
            f"scan {self.scan_s * 1000:.0f}ms | setModel {self.layout_s * 1000:.0f}ms | "
            f"queued {self.loader.queued()} in-flight {self.loader.in_flight} "
            f"dropped {s.dropped} | decoded: {timings or '-'} | disk hits {s.disk_hits} "
            f"| errors {s.errors} | mem-cache {len(self.cache)} "
            f"({self.cache.used / 1e6:.0f}/{self.cache.budget / 1e6:.0f} MB) "
            f"| process {process_memory_mb():.0f} MB"
        )

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.loader.stop()
        super().closeEvent(event)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folders", nargs="+", type=Path)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--budget-mb", type=int, default=100)
    parser.add_argument("--workers", type=int, default=max(2, (os.cpu_count() or 4) - 2))
    parser.add_argument("--no-disk-cache", action="store_true")
    parser.add_argument("--auto", action="store_true", help="scripted benchmark, then quit")
    args = parser.parse_args()

    t0 = time.perf_counter()
    files = scan(args.folders) * args.repeat
    scan_s = time.perf_counter() - t0
    print(f"scanned {len(files)} entries in {scan_s * 1000:.0f} ms; cache: {CACHE_DIR}")

    app = QApplication(sys.argv)
    window = Window(files, args, scan_s)
    window.show()
    if args.auto:
        QTimer.singleShot(500, window.run_auto)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
