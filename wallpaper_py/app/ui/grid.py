"""
The Library's image grid (docs/plan.md, Phase 5, "Thumbnails"), built
from the Phase 0 spike (spikes/grid_prototype.py): a `QListView` in icon
mode over a list model of paths, every tile one size (`tiles.TILE`, 16:9),
so only the tiles on screen are painted or asked for, even with 20k
images. The number of columns follows the width.

A tile is the image filling it, cropped to the middle as Fill shows a
wallpaper, so every tile looks alike (as in the phone app); it fades in
from the panel tone when its thumbnail arrives. An image whose file is
gone is a "missing" tile with its name, one that can't be read says so;
neither is an error.
The tiles come from a `TileLoader` (tiles.py): each tile painted without
its thumbnail asks for it, and the grid tells the loader which tiles are
on screen after every scroll, resize or new list, so requests for tiles
scrolled away are dropped.

`FolderScan` lists a folder's images off the main thread for the grid.
"""

from __future__ import annotations

import bisect
import time
from collections.abc import Sequence
from functools import partial
from pathlib import Path
from typing import override

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QFontMetrics,
    QHideEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from ...core.library import scan
from . import theme
from .jobs import Jobs
from .tiles import TILE, TileLoader

SPACING = 8  # px between tiles
FADE_S = 0.18  # a tile fading in from the panel tone
FADE_FRAME_MS = 16
MARGIN_ROWS = 1  # rows of tiles above and below the screen also asked for

PATH_ROLE = Qt.ItemDataRole.UserRole

Index = QModelIndex | QPersistentModelIndex


class GridModel(QAbstractListModel):
    """The grid's images, in the order shown (a path may repeat: History)."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._paths: list[Path] = []

    def set_paths(self, paths: Sequence[Path]) -> None:
        self.beginResetModel()
        self._paths = list(paths)
        self.endResetModel()

    def paths(self) -> list[Path]:
        return list(self._paths)

    def path(self, row: int) -> Path:
        return self._paths[row]

    @override
    def rowCount(  # pylint: disable=invalid-name
        self, parent: Index = QModelIndex()
    ) -> int:
        return 0 if parent.isValid() else len(self._paths)

    @override
    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or not 0 <= index.row() < len(self._paths):
            return None
        path = self._paths[index.row()]
        if role == PATH_ROLE:
            return path
        if role == Qt.ItemDataRole.DisplayRole:
            return path.name
        if role == Qt.ItemDataRole.ToolTipRole:
            return str(path)
        return None


class TileDelegate(QStyledItemDelegate):
    """Paints a tile; asks the grid's loader for a thumbnail it hasn't got."""

    def __init__(self, grid: ImageGrid) -> None:
        super().__init__(grid)
        self._grid = grid

    @override
    def sizeHint(  # pylint: disable=invalid-name
        self, option: QStyleOptionViewItem, index: Index
    ) -> QSize:
        del option, index
        return QSize(TILE)

    @override
    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: Index
    ) -> None:
        path = index.data(PATH_ROLE)
        if not isinstance(path, Path):
            return
        rect = QRectF(option.rect)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        tile = self._grid.loader.get(path)
        if tile is None:
            self._grid.loader.request(path)
        derived = theme.tones()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(rect, theme.RADIUS_SMALL, theme.RADIUS_SMALL)
        painter.setClipPath(clip)
        painter.fillRect(rect, derived.panel)
        if tile is not None and tile.pixmap is not None:
            painter.setOpacity(self._grid.opacity(path))
            source = QRectF(tile.pixmap.rect())
            painter.drawPixmap(rect, tile.pixmap, source)  # already the tile's shape
            painter.setOpacity(1.0)
        elif tile is not None:
            _draw_name(
                painter, rect, path.name, "Missing" if tile.missing else "Can't open"
            )
        if selected:
            pen = QPen(derived.accent, 2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            inner = rect.adjusted(1, 1, -1, -1)
            painter.drawRoundedRect(inner, theme.RADIUS_SMALL, theme.RADIUS_SMALL)
        painter.restore()


def _draw_name(painter: QPainter, rect: QRectF, name: str, why: str) -> None:
    """A tile without an image: its file name, and why below it."""
    derived = theme.tones()
    metrics = QFontMetrics(painter.font())
    inner = rect.adjusted(theme.space(), 0, -theme.space(), 0)
    elided = metrics.elidedText(name, Qt.TextElideMode.ElideMiddle, int(inner.width()))
    line = metrics.height()
    top = rect.center().y() - line
    centered = int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
    painter.setPen(derived.panel_text)
    painter.drawText(QRectF(inner.x(), top, inner.width(), line), centered, elided)
    painter.setPen(derived.panel_muted)
    painter.drawText(QRectF(inner.x(), top + line, inner.width(), line), centered, why)


class ImageGrid(QListView):
    """See the module docstring."""

    def __init__(self, loader: TileLoader, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.loader = loader
        self._fading: dict[Path, float] = {}  # arrived at (time.monotonic())
        self.list_model = GridModel(self)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setSpacing(SPACING)
        self.setWrapping(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(TILE.height() // 3)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.viewport().setBackgroundRole(QPalette.ColorRole.Window)
        self.setItemDelegate(TileDelegate(self))
        self.setModel(self.list_model)
        self.setAccessibleName("Images")

        self._wanted_timer = QTimer(self)  # once per burst of changes
        self._wanted_timer.setSingleShot(True)
        self._wanted_timer.setInterval(0)
        self._wanted_timer.timeout.connect(self._tell_wanted)
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(FADE_FRAME_MS)
        self._fade_timer.timeout.connect(self._fade_step)
        self.verticalScrollBar().valueChanged.connect(self._scrolled)
        loader.loaded.connect(self._tile_loaded)

    def set_images(self, paths: Sequence[Path]) -> None:
        """Show these, from the top (the same ones again: nothing changes)."""
        if list(paths) == self.list_model.paths():
            return
        self._fading.clear()
        self.list_model.set_paths(paths)
        self.scrollToTop()
        self._wanted_timer.start()

    def images(self) -> list[Path]:
        return self.list_model.paths()

    def visible_rows(self, margin: int = 0) -> range:
        """
        The rows on screen, and those within `margin` px above and below
        (found by bisection: the layout is row by row, top to bottom).
        """
        count = self.list_model.rowCount()
        if not count or not self.isVisible():
            return range(0)
        top, bottom = -margin, self.viewport().height() + margin
        rows = range(count)
        first = bisect.bisect_left(
            rows, True, key=lambda r: self._rect(r).bottom() >= top
        )
        end = bisect.bisect_left(rows, True, key=lambda r: self._rect(r).top() > bottom)
        return range(first, end)

    def opacity(self, path: Path) -> float:
        """A fading tile's opacity now (1: shown fully)."""
        arrived = self._fading.get(path)
        if arrived is None:
            return 1.0
        return min(1.0, (time.monotonic() - arrived) / FADE_S)

    def _rect(self, row: int) -> QRect:
        return self.visualRect(self.list_model.index(row))

    # -- what is on screen ---------------------------------------------------------

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:  # pylint: disable=invalid-name
        super().resizeEvent(event)
        self._wanted_timer.start()

    @override
    def showEvent(self, event: QShowEvent) -> None:  # pylint: disable=invalid-name
        super().showEvent(event)
        self._wanted_timer.start()

    @override
    def hideEvent(self, event: QHideEvent) -> None:  # pylint: disable=invalid-name
        super().hideEvent(event)
        self._wanted_timer.stop()
        self.loader.set_wanted(())  # nothing of this grid is on screen

    @Slot(int)
    def _scrolled(self, value: int) -> None:
        del value
        self._tell_wanted()  # at once: the paint that follows asks for tiles

    @Slot()
    def _tell_wanted(self) -> None:
        if not self.isVisible():
            return
        rows = self.visible_rows(MARGIN_ROWS * (TILE.height() + SPACING))
        self.loader.set_wanted(self.list_model.path(r) for r in rows)

    # -- tiles arriving ------------------------------------------------------------

    @Slot(object)
    def _tile_loaded(self, path: Path) -> None:
        rows = [r for r in self.visible_rows() if self.list_model.path(r) == path]
        if not rows:
            return
        self._fading[path] = time.monotonic()
        if not self._fade_timer.isActive():
            self._fade_timer.start()
        for row in rows:
            self.viewport().update(self._rect(row))

    @Slot()
    def _fade_step(self) -> None:
        now = time.monotonic()
        done = [p for p, t in self._fading.items() if now - t >= FADE_S]
        for path in done:
            del self._fading[path]
        if not self._fading:
            self._fade_timer.stop()
        self.viewport().update()


# -- listing a folder off the main thread -------------------------------------------


class FolderScan(QObject):
    """
    A folder's images (`library.scan`: recursive, natural order), listed
    on a thread of its own; `scanned(folder, paths)` for the latest asked
    for only. `close()` before the owner goes away (it waits for a scan).
    """

    scanned = Signal(object, object)  # Path, list[Path]

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._jobs = Jobs(parent=self)
        self._jobs.done.connect(self._done)
        self._asked: Path | None = None

    def asked(self) -> Path | None:
        """The folder being listed, if any."""
        return self._asked

    def scan(self, folder: Path) -> None:
        self._asked = folder
        self._jobs.run(folder, partial(scan, folder))

    def close(self) -> None:
        self._asked = None
        self._jobs.close()

    @Slot(object, object)
    def _done(self, folder: Path, paths: list[Path]) -> None:
        if folder == self._asked:
            self._asked = None
            self.scanned.emit(folder, paths)
