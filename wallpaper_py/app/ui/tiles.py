"""
Thumbnails for the Library's grids (docs/plan.md, Phase 5, "Thumbnails"):
a second pipeline beside `ThumbnailLoader` (which stays for Home's few
images), made for thousands of tiles.

`TileLoader`: the grid asks for the tiles it paints (`request`) and says
which ones are on screen (`set_wanted`). Worker threads take the newest
request first (LIFO: what was just scrolled to), and a request for a tile
no longer on screen is dropped before it is decoded. Each worker reads
the thumbnail from the disk cache, or decodes the image (draft mode for
JPEGs) and saves it there (core.thumbnail_cache); the `QPixmap` is made on
the UI thread when the result arrives, kept in memory (an LRU with a byte
budget that never drops a tile on screen), and `loaded(path)` fires.

`Pregenerator`: while the window is open, one low-priority thread fills
the disk cache: the open playlist's folder first, then the other
playlists' and the lists' entries. It waits while the grid has tiles to
decode. Having seen every current image (every folder and list read), it
deletes the cache files none of them names (old tile sizes, deleted or
edited images), except those written since the pass started; a folder it
can't read (a drive not mounted) keeps its thumbnails, as no pass then
prunes. A change of what it should cover starts a new pass, which begins
once the old one has stopped at its next image (one thread).

Everything here lives only in the UI process: closing the window ends it
and gives the memory back; the app never makes thumbnails. The threads
are each object's own pool (`Jobs`, jobs.py, as `ThumbnailLoader`'s): the
tile workers loop until `close()`, which the owner calls before it goes
away.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from PIL import Image
from PySide6.QtCore import QObject, QSize, QThread, Signal, Slot
from PySide6.QtGui import QImage, QPixmap

from ...core.library import walk
from ...core.marks import MarkFile, MarkFileBusyError
from ...core.model import ConfigError, ImageLoadError
from ...core.thumbnail_cache import ThumbnailCache
from .jobs import Deliver, Jobs
from .thumbnails import to_qimage

log = logging.getLogger(__name__)

TILE = QSize(192, 108)  # logical px: a grid tile, 16:9
THREADS = 4
MEMORY_BUDGET = 1536 * 2**20  # bytes of pixmaps: ~10k tiles at 2x scale
IDLE_POLL_S = 0.25  # the pre-generation checks for `close` this often


def tile_pixels(ratio: float) -> tuple[int, int]:
    """A tile in device pixels, at a display scale of `ratio`."""
    return round(TILE.width() * ratio), round(TILE.height() * ratio)


# Runs on a worker thread: an image file -> its thumbnail. Raises OSError
# (FileNotFoundError: the file is gone) or ImageLoadError.
TileDecoder = Callable[[Path], Image.Image]


@dataclass(frozen=True)
class Tile:
    """A tile's image on the UI thread, or why there is none."""

    pixmap: QPixmap | None
    missing: bool = False  # the file is gone (a list's entry, say)
    error: str | None = None  # it can't be read or decoded

    def cost(self) -> int:
        """Bytes it holds."""
        if self.pixmap is None:
            return 0
        return self.pixmap.width() * self.pixmap.height() * self.pixmap.depth() // 8


@dataclass(frozen=True)
class _Decoded:
    """What a worker made (no QPixmap: those are UI-thread only)."""

    image: QImage  # null if not decoded
    missing: bool = False
    error: str | None = None


def _decode(decode: TileDecoder, path: Path) -> _Decoded:
    try:
        return _Decoded(to_qimage(decode(path)))
    except FileNotFoundError:
        return _Decoded(QImage(), missing=True)
    except (OSError, ImageLoadError) as err:
        return _Decoded(QImage(), error=str(err))
    except Exception as err:  # pylint: disable=broad-exception-caught
        log.exception("Making the thumbnail of %s failed", path)
        return _Decoded(QImage(), error=str(err))


class _Queue:
    """
    The requests, shared by the UI thread and the workers; every field is
    read and written under `lock`. A path is `queued` from its request
    until the UI thread has its result (or it is dropped).
    """

    def __init__(self) -> None:
        self.lock = threading.Condition()
        self.stack: list[Path] = []  # newest last
        self.queued: set[Path] = set()
        self.wanted: set[Path] = set()  # on screen (and just asked for)
        self.running = 0
        self.closed = False
        self.idle = threading.Event()  # nothing to decode (the pre-generation waits)
        self.idle.set()

    def take(self) -> Path | None:
        """The newest wanted request (others are dropped); None: closed."""
        with self.lock:
            while True:
                if self.closed:
                    return None
                while self.stack:
                    path = self.stack.pop()
                    if path in self.wanted:
                        self.running += 1
                        return path
                    self.queued.discard(path)  # scrolled away: dropped
                self.check_idle()
                self.lock.wait()

    def ran(self) -> None:
        """A worker is done with its path."""
        with self.lock:
            self.running -= 1
            self.check_idle()

    def check_idle(self) -> None:
        """Under `lock`: `idle` once nothing is left to decode."""
        if not self.stack and not self.running:
            self.idle.set()


class _TileWorker:
    """A worker's work: decodes requests until the queue closes."""

    def __init__(self, queue: _Queue, decode: TileDecoder) -> None:
        self._queue = queue
        self._decode = decode

    def __call__(self, deliver: Deliver) -> None:
        while (path := self._queue.take()) is not None:
            try:
                result = _decode(self._decode, path)
            finally:
                self._queue.ran()
            deliver(path, result)


class TileLoader(QObject):
    """See the module docstring. `close()` before the owner goes away."""

    # pylint: disable=too-many-instance-attributes

    loaded = Signal(object)  # Path: get(path) has it now

    def __init__(
        self,
        decode: TileDecoder,
        *,
        ratio: float = 1.0,
        threads: int = THREADS,
        budget: int = MEMORY_BUDGET,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._ratio = ratio  # the display scale the thumbnails are made for
        self._budget = budget
        self._memory: OrderedDict[Path, Tile] = OrderedDict()
        self._used = 0
        self._queue = _Queue()
        self._decode = decode
        self._threads = threads
        self._jobs = Jobs(threads, parent=self)
        self._jobs.done.connect(self._done)
        self._started = False  # at the first request: a grid never shown makes none

    @property
    def idle(self) -> threading.Event:
        """Set while nothing waits to be decoded."""
        return self._queue.idle

    def get(self, path: Path) -> Tile | None:
        """The tile, or None if it isn't loaded (yet)."""
        tile = self._memory.get(path)
        if tile is not None:
            self._memory.move_to_end(path)
        return tile

    def memory_used(self) -> int:
        return self._used

    def request(self, path: Path) -> None:
        """Load this tile (it is on screen), before older requests."""
        queue = self._queue
        if queue.closed or path in self._memory:
            return
        with queue.lock:
            queue.wanted.add(path)  # until the next `set_wanted`
            if path in queue.queued:
                return
            queue.queued.add(path)
            queue.stack.append(path)
            queue.idle.clear()
            queue.lock.notify()
        if not self._started:
            self._started = True
            for _ in range(self._threads):
                self._jobs.run_loop(_TileWorker(self._queue, self._decode))

    def set_wanted(self, paths: Iterable[Path]) -> None:
        """The tiles on screen now: requests for any other are dropped."""
        queue = self._queue
        with queue.lock:
            queue.wanted = set(paths)
            kept = [p for p in queue.stack if p in queue.wanted]
            for path in queue.stack:
                if path not in queue.wanted:
                    queue.queued.discard(path)
            queue.stack = kept
            queue.check_idle()

    def pending(self) -> set[Path]:
        """Requested and not loaded yet."""
        with self._queue.lock:
            return set(self._queue.queued)

    def close(self) -> None:
        """Drop the requests and wait for running decodes (each is short)."""
        queue = self._queue
        with queue.lock:
            queue.closed = True
            queue.stack.clear()
            queue.queued.clear()
            queue.idle.set()
            queue.lock.notify_all()
        self._jobs.close()

    @Slot(object, object)
    def _done(self, path: Path, result: _Decoded) -> None:
        queue = self._queue
        with queue.lock:
            if queue.closed or path not in queue.queued:
                return  # dropped meanwhile
            queue.queued.discard(path)
        pixmap = None
        if not result.image.isNull():
            pixmap = QPixmap.fromImage(result.image)
            pixmap.setDevicePixelRatio(self._ratio)
        tile = Tile(pixmap, result.missing, result.error)
        old = self._memory.pop(path, None)
        if old is not None:
            self._used -= old.cost()
        self._memory[path] = tile
        self._used += tile.cost()
        self._trim()
        self.loaded.emit(path)

    def _trim(self) -> None:
        """The least recently used tiles go beyond the budget, never one on screen."""
        if self._used <= self._budget:
            return
        wanted = self._queue.wanted
        for path in [p for p in self._memory if p not in wanted]:
            self._used -= self._memory.pop(path).cost()
            if self._used <= self._budget:
                return


# -- pre-generation ----------------------------------------------------------------


@dataclass(frozen=True)
class Sources:
    """What a pre-generation pass covers, in order."""

    folders: tuple[Path, ...]  # the open playlist's first
    lists: tuple[Path, ...]  # favourites.txt, to-remove.txt


@dataclass(frozen=True)
class PassResult:
    images: int  # images it went through
    deleted: int  # old cache files deleted
    complete: bool  # every folder and list was read, so it could prune


class _PassControl:
    """A pass's state shared with the UI thread (the job is the pool's)."""

    def __init__(self, sources: Sources) -> None:
        self.sources = sources
        self.stop = threading.Event()
        self.ended = threading.Event()


class _PregenerationPass:
    """One pass over `sources` (see the module docstring); None if stopped."""

    def __init__(
        self, cache: ThumbnailCache, control: _PassControl, idle: threading.Event
    ) -> None:
        self._cache = cache
        self._control = control
        self._idle = idle
        self._complete = True  # every source read so far (this thread's own)

    def __call__(self) -> PassResult | None:
        try:
            return self._run()
        finally:
            self._control.ended.set()

    def _run(self) -> PassResult | None:
        started = time.time()  # a file written since may be for a new image
        kept: list[Path] = []
        seen: set[Path] = set()
        for image in self._images():
            if image in seen:
                continue
            seen.add(image)
            if not self._wait_for_the_grid():
                return None
            file = self._cache.ensure(image)
            if file is not None:
                kept.append(file)
        if self._control.stop.is_set():
            return None
        complete = self._complete
        deleted = self._cache.prune(kept, started) if complete else 0
        log.info(
            "Thumbnails: %d images checked, %d old files deleted%s",
            len(seen),
            deleted,
            "" if complete else " (a folder or list couldn't be read)",
        )
        return PassResult(len(seen), deleted, complete)

    def _images(self) -> Iterator[Path]:
        """Every image, folders first; an unreadable source clears `_complete`."""
        sources = self._control.sources
        for folder in sources.folders:
            if not folder.is_dir():  # e.g. a drive not mounted yet
                self._complete = False
                continue
            yield from walk(folder)
        for file in sources.lists:
            try:
                yield from MarkFile(file).paths()
            except (ConfigError, MarkFileBusyError) as err:
                log.info("Thumbnails: %s not read: %s", file, err)
                self._complete = False

    def _wait_for_the_grid(self) -> bool:
        """Wait while the grid has tiles to decode; False once stopped."""
        stop = self._control.stop
        while not self._idle.wait(IDLE_POLL_S):
            if stop.is_set():
                return False
        return not stop.is_set()


class Pregenerator(QObject):
    """See the module docstring. `close()` before the owner goes away."""

    pass_done = Signal(object)  # PassResult

    def __init__(
        self,
        cache: ThumbnailCache,
        idle: threading.Event,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._cache = cache
        self._idle = idle
        # One thread: a new pass starts once the one it replaces has stopped.
        self._jobs = Jobs(1, priority=QThread.Priority.LowPriority, parent=self)
        self._jobs.done.connect(self._pass_done)
        self._passes: list[_PassControl] = []  # the last is current
        self._closed = False

    def sources(self) -> Sources | None:
        """What the current (or last) pass covers."""
        return self._passes[-1].sources if self._passes else None

    def start(self, sources: Sources) -> None:
        """A pass over `sources`, unless the current one covers just that."""
        if self._closed or sources == self.sources():
            return
        for control in self._passes:
            control.stop.set()  # it ends at its next image; not waited for
        control = _PassControl(sources)
        self._passes = [c for c in self._passes if not c.ended.is_set()] + [control]
        self._jobs.run(control, _PregenerationPass(self._cache, control, self._idle))

    def running(self) -> bool:
        return any(not c.ended.is_set() for c in self._passes)

    def close(self) -> None:
        """Stop the passes and wait for them (at most one image each)."""
        self._closed = True
        for control in self._passes:
            control.stop.set()
        self._jobs.close()  # a pass not started yet never starts
        for control in self._passes:
            control.ended.set()

    @Slot(object, object)
    def _pass_done(self, control: _PassControl, result: PassResult | None) -> None:
        del control
        if result is not None and not self._closed:
            self.pass_done.emit(result)
