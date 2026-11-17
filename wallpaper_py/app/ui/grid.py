"""
The Library's image grid (docs/plan.md at 268a951, "Thumbnails" and "Grid
tabs"), built from the Phase 0 spike (spikes/grid_prototype.py): a
`QListView` in icon mode over a list model of entries (entries.py), every
tile one size (`tiles.TILE`, 16:9), so only the tiles on screen are painted
or asked for, even with 20k images. The number of columns follows the width.

A tile is the image filling it, cropped to the middle as Fill shows a
wallpaper, so every tile looks alike (as in the phone app); it fades in
from the panel tone when its thumbnail arrives. An image whose file is
gone is a "missing" tile with its name, one that can't be read says so;
neither is an error. Badges, as in docs/design/library.png: a star for a
favourite, a bin for one listed to remove, *On screen* for the current
image of any playlist (`Marks`, which the page keeps up to date), and in
History when it was shown ("2 h ago"). They sit on the image, so their
colours are fixed (white outline icons and text on dark discs and
pills), not the page's. With badges off (`set_badges`), only "2 h ago"
stays.

While the list is narrowed by name (`set_needle`), each tile has a caption
at its bottom: the file name around the match, the match in bold (so you
see why it is there). Not on a missing tile, which shows its name already.

Nothing is selected: a click opens the image enlarged (the grid tab's
`open_requested`; enlarged.py), and `reveal` scrolls to it again after.

The tiles come from a `TileLoader` (tiles.py): each tile painted without
its thumbnail asks for it, and the grid tells the loader which tiles are
on screen after every scroll, resize or new list, so requests for tiles
scrolled away are dropped.

The grid's own scroll bar stays hidden: `FastScroll`, beside it, is a slim
one painted here, whose handle can be dragged (or the track pressed) to
anywhere in the list at once.
"""

from __future__ import annotations

import bisect
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import override

from PySide6.QtCore import (
    QAbstractListModel,
    QCoreApplication,
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QFont,
    QFontMetrics,
    QHideEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QResizeEvent,
    QShowEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QListView,
    QScrollBar,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from ...core.settings import format_ago
from . import theme
from .entries import Entry
from .tiles import TILE, TileLoader

SPACING = 8  # px between tiles
FADE_S = 0.18  # a tile fading in from the panel tone
FADE_FRAME_MS = 16
MARGIN_ROWS = 1  # rows of tiles above and below the screen also asked for

# Badges (docs/design/mockup.dc.html, the Library page), on the image.
BADGE = 22  # a disc's diameter
BADGE_ICON = 13
BADGE_INSET = 6  # from the tile's edge
BADGE_GAP = 4
BADGE_BACK = QColor(0, 0, 0, 140)
PILL_BACK = QColor(0, 0, 0, 153)
PILL_TEXT = QColor("#ffffff")
PILL_PADDING = 8
PILL_FONT = 0.8  # of the body text
CAPTION_BACK = QColor(0, 0, 0, 166)  # the match caption's strip
CAPTION_TEXT = QColor("#ffffff")
CAPTION_PADDING = 8  # left and right of its text
CAPTION_HEIGHT = 6  # above and below its text, in all
BADGE_MARK = QColor("#ffffff")  # the star and the bin, outlined as on Home

# The fast-scroll bar: its width takes the pointer; the track is slimmer.
SCROLL_WIDTH = 16
SCROLL_TRACK = 6
SCROLL_TRACK_ACTIVE = 8  # hovered or dragged
SCROLL_HANDLE_MIN = 36

ENTRY_ROLE = Qt.ItemDataRole.UserRole

Index = QModelIndex | QPersistentModelIndex


@dataclass(frozen=True)
class Marks:
    """What the badges show: `library.path_key`s."""

    favourites: frozenset[str] = frozenset()
    to_remove: frozenset[str] = frozenset()
    on_screen: frozenset[str] = frozenset()  # the current image of any playlist


class GridModel(QAbstractListModel):
    """The grid's entries, in the order shown (a path may repeat: History)."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._entries: list[Entry] = []

    def set_entries(self, entries: Sequence[Entry]) -> None:
        self.beginResetModel()
        self._entries = list(entries)
        self.endResetModel()

    def entries(self) -> list[Entry]:
        return list(self._entries)

    def paths(self) -> list[Path]:
        return [e.path for e in self._entries]

    def path(self, row: int) -> Path:
        return self._entries[row].path

    @override
    def rowCount(  # pylint: disable=invalid-name
        self, parent: Index = QModelIndex()
    ) -> int:
        return 0 if parent.isValid() else len(self._entries)

    @override
    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid() or not 0 <= index.row() < len(self._entries):
            return None
        entry = self._entries[index.row()]
        if role == ENTRY_ROLE:
            return entry
        if role == Qt.ItemDataRole.DisplayRole:
            return entry.path.name
        if role == Qt.ItemDataRole.ToolTipRole:
            return str(entry.path)
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
        entry = index.data(ENTRY_ROLE)
        if not isinstance(entry, Entry):
            return
        path = entry.path
        rect = QRectF(option.rect)
        grid = self._grid
        tile = grid.loader.get(path)
        if tile is None:
            grid.loader.request(path)
        derived = theme.tones()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(rect, theme.RADIUS_SMALL, theme.RADIUS_SMALL)
        painter.setClipPath(clip)
        painter.fillRect(rect, derived.panel)
        bottom = rect.bottom()  # of what is free for the pills
        if tile is not None and tile.pixmap is not None:
            painter.setOpacity(grid.opacity(path))
            source = QRectF(tile.pixmap.rect())
            painter.drawPixmap(rect, tile.pixmap, source)  # already the tile's shape
            painter.setOpacity(1.0)
        elif tile is not None:
            draw_name(
                painter, rect, path.name, "Missing" if tile.missing else "Can't open"
            )
        if grid.needle and (tile is None or tile.pixmap is not None):
            bottom = _draw_caption(painter, rect, path.name, grid.needle, option.font)
        marks = grid.marks if grid.badges else Marks()
        _draw_badges(painter, rect, entry, marks, option, bottom)
        painter.restore()


def draw_name(painter: QPainter, rect: QRectF, name: str, why: str) -> None:
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


def split_match(name: str, needle: str) -> tuple[str, str, str]:
    """
    `name` as (before, match, after) around its first match of `needle`,
    ignoring case (no match: the name and two empty strings).
    """
    for fold in (str.casefold, str.lower):
        folded = fold(name)
        at = folded.find(fold(needle))
        if at >= 0 and len(folded) == len(name):  # the same positions in `name`
            end = at + len(fold(needle))
            return name[:at], name[at:end], name[end:]
    return name, "", ""


def fit_match(
    parts: tuple[str, str, str],
    plain: QFontMetrics,
    bold: QFontMetrics,
    width: int,
) -> tuple[str, str, str]:
    """
    `split_match`'s parts cut to fit `width` px, the match kept whole if it
    fits: "…before" and "after…", each given at least half the room left.
    """
    before, match, after = parts
    room = width - bold.horizontalAdvance(match)
    if plain.horizontalAdvance(before) + plain.horizontalAdvance(after) <= room:
        return parts
    if room <= 0:
        return "", bold.elidedText(match, Qt.TextElideMode.ElideRight, width), ""
    left_room = max(room // 2, room - plain.horizontalAdvance(after))
    left = plain.elidedText(before, Qt.TextElideMode.ElideLeft, left_room)
    right_room = room - plain.horizontalAdvance(left)
    return left, match, plain.elidedText(after, Qt.TextElideMode.ElideRight, right_room)


def _draw_caption(
    painter: QPainter, rect: QRectF, name: str, needle: str, font: QFont
) -> float:
    """
    The match caption on a dark strip at the tile's bottom; returns the
    strip's top (the pills go above it).
    """
    plain = theme.scaled_font(font, PILL_FONT)
    bold = theme.scaled_font(font, PILL_FONT, bold=True)
    plain_metrics, bold_metrics = QFontMetrics(plain), QFontMetrics(bold)
    height = max(plain_metrics.height(), bold_metrics.height()) + CAPTION_HEIGHT
    strip = QRectF(rect.left(), rect.bottom() - height, rect.width(), height)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(CAPTION_BACK)
    painter.drawRect(strip)
    width = int(strip.width()) - 2 * CAPTION_PADDING
    parts = fit_match(split_match(name, needle), plain_metrics, bold_metrics, width)
    _draw_runs(painter, strip, ((parts[0], plain), (parts[1], bold), (parts[2], plain)))
    return strip.top()


def _draw_runs(
    painter: QPainter, strip: QRectF, runs: Sequence[tuple[str, QFont]]
) -> None:
    """Texts in their fonts one after the other, centred in `strip`."""
    advances = [QFontMetrics(font).horizontalAdvance(text) for text, font in runs]
    x = strip.left() + (strip.width() - sum(advances)) / 2
    painter.setPen(CAPTION_TEXT)
    flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    for (text, font), advance in zip(runs, advances):
        if text:
            painter.setFont(font)
            room = QRectF(x, strip.top(), advance + 1, strip.height())
            painter.drawText(room, flags, text)
        x += advance


def _draw_badges(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    painter: QPainter,
    rect: QRectF,
    entry: Entry,
    marks: Marks,
    option: QStyleOptionViewItem,
    bottom: float,
) -> None:
    """The star and the bin at the top right, the pills above `bottom`."""
    listed = ((marks.favourites, "star"), (marks.to_remove, "trash-2"))
    _draw_discs(painter, rect, [name for keys, name in listed if entry.key in keys])
    font = theme.scaled_font(option.font, PILL_FONT, bold=True)
    below = rect.bottom() - bottom
    if entry.key in marks.on_screen:
        _draw_pill(painter, rect, "On screen", font, below, left=True)
    if entry.shown_at is not None:
        ago = format_ago(time.time() - entry.shown_at)
        _draw_pill(painter, rect, ago, font, below)


def _draw_discs(painter: QPainter, rect: QRectF, icons: Sequence[str]) -> None:
    """These icons on dark discs, from the top right leftwards."""
    ratio = painter.device().devicePixelRatioF()
    right = rect.right() - BADGE_INSET
    for name in icons:
        disc = QRectF(right - BADGE, rect.top() + BADGE_INSET, BADGE, BADGE)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(BADGE_BACK)
        painter.drawEllipse(disc)
        pixmap = theme.tinted_pixmap(name, BADGE_MARK, BADGE_ICON, ratio)
        corner = disc.center() - QPointF(BADGE_ICON / 2, BADGE_ICON / 2)
        painter.drawPixmap(corner, pixmap)
        right = disc.left() - BADGE_GAP


def _draw_pill(  # pylint: disable=too-many-arguments
    painter: QPainter,
    rect: QRectF,
    text: str,
    font: QFont,
    below: float,
    *,
    left: bool = False,
) -> None:
    """
    A short text on a dark pill at the bottom left (or right) of a tile,
    `below` px higher (above the caption).
    """
    painter.setFont(font)
    metrics = painter.fontMetrics()
    height = metrics.height() + 4
    width = metrics.horizontalAdvance(text) + 2 * PILL_PADDING
    x = rect.left() + BADGE_INSET if left else rect.right() - BADGE_INSET - width
    top = rect.bottom() - below - BADGE_INSET - height
    pill = QRectF(x, top, width, height)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(PILL_BACK)
    painter.drawRoundedRect(pill, height / 2, height / 2)
    painter.setPen(PILL_TEXT)
    painter.drawText(pill, int(Qt.AlignmentFlag.AlignCenter), text)


class ImageGrid(QListView):
    """See the module docstring."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, loader: TileLoader, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.loader = loader
        self.marks = Marks()
        self.badges = True  # the marks shown (History's "2 h ago" always)
        self.needle = ""  # the filter text: captions show the match
        self._fading: dict[Path, float] = {}  # arrived at (time.monotonic())
        self.list_model = GridModel(self)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setSpacing(SPACING)
        self.setWrapping(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.verticalScrollBar().setSingleStep(TILE.height() // 3)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.viewport().setBackgroundRole(QPalette.ColorRole.Window)
        self.setItemDelegate(TileDelegate(self))
        self.setModel(self.list_model)
        self.setAccessibleName("Images")
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)  # opens it

        self._wanted_timer = QTimer(self)  # once per burst of changes
        self._wanted_timer.setSingleShot(True)
        self._wanted_timer.setInterval(0)
        self._wanted_timer.timeout.connect(self._tell_wanted)
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(FADE_FRAME_MS)
        self._fade_timer.timeout.connect(self._fade_step)
        self.verticalScrollBar().valueChanged.connect(self._scrolled)
        loader.loaded.connect(self._tile_loaded)

    def set_entries(self, entries: Sequence[Entry], *, top: bool = True) -> None:
        """
        Show these, from the top (`top`), else where it was (the same list
        read again, with an image more or less). The same ones again: nothing
        changes.
        """
        if list(entries) == self.list_model.entries():
            if top:
                self.scrollToTop()
            return
        scroll = self.verticalScrollBar()
        kept = 0 if top else scroll.value()
        self._fading.clear()
        self.list_model.set_entries(entries)
        if kept:
            self.doItemsLayout()  # the new range, so the value isn't clipped to 0
            scroll.setValue(kept)
        else:
            self.scrollToTop()
        self._wanted_timer.start()

    def images(self) -> list[Path]:
        return self.list_model.paths()

    def set_marks(self, marks: Marks) -> None:
        if marks != self.marks:
            self.marks = marks
            self.viewport().update()

    def set_badges(self, on: bool) -> None:
        if on != self.badges:
            self.badges = on
            self.viewport().update()

    def set_needle(self, needle: str) -> None:
        """The filter text the list is narrowed by ("": none, no captions)."""
        if needle != self.needle:
            self.needle = needle
            self.viewport().update()

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

    def reveal(self, row: int, path: Path) -> None:
        """
        Scroll to the tile of `path`: at `row` if it is still there, else
        its first one (the list may have changed meanwhile).
        """
        model = self.list_model
        if not 0 <= row < model.rowCount() or model.path(row) != path:
            row = next((r for r, p in enumerate(model.paths()) if p == path), -1)
        if row >= 0:
            self.scrollTo(model.index(row), QAbstractItemView.ScrollHint.EnsureVisible)

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


# -- the fast-scroll bar ------------------------------------------------------------


class FastScroll(QWidget):
    """
    A slim scroll bar for a grid, painted here: it follows the grid's own
    (hidden) `bar` and moves it. Dragging the handle moves the grid with the
    pointer; pressing the track elsewhere jumps there first, so one drag
    reaches anywhere in 20k images. The wheel over it scrolls the grid. No
    handle while there is nothing to scroll.
    """

    def __init__(self, bar: QScrollBar, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bar = bar
        self._grab: float | None = None  # while dragging: the pointer in the handle
        self.setFixedWidth(SCROLL_WIDTH)
        self.setAccessibleName("Scroll position")
        bar.valueChanged.connect(self._moved)
        bar.rangeChanged.connect(self._range_changed)

    def handle(self) -> QRectF | None:
        """The handle, in the widget (None: nothing to scroll)."""
        scroll = self._bar
        span = scroll.maximum() - scroll.minimum()
        if span <= 0 or self.height() <= 0:
            return None
        height = float(self.height())
        page = max(1, scroll.pageStep())
        length = min(height, max(SCROLL_HANDLE_MIN, height * page / (span + page)))
        top = (height - length) * (scroll.value() - scroll.minimum()) / span
        return QRectF(0, top, self.width(), length)

    def dragging(self) -> bool:
        return self._grab is not None

    def _scroll_to(self, handle_top: float, length: float) -> None:
        """Moves the grid so the handle's top is at `handle_top`."""
        scroll = self._bar
        room = self.height() - length
        share = 0.0 if room <= 0 else min(1.0, max(0.0, handle_top / room))
        scroll.setValue(
            scroll.minimum() + round(share * (scroll.maximum() - scroll.minimum()))
        )

    @override
    def mousePressEvent(  # pylint: disable=invalid-name
        self, event: QMouseEvent
    ) -> None:
        handle = self.handle()
        if event.button() != Qt.MouseButton.LeftButton or handle is None:
            super().mousePressEvent(event)
            return
        y = event.position().y()
        if handle.top() <= y <= handle.bottom():
            self._grab = y - handle.top()
        else:  # jump there: the handle centred on the pointer
            self._grab = handle.height() / 2
            self._scroll_to(y - self._grab, handle.height())
        self.update()

    @override
    def mouseMoveEvent(
        self, event: QMouseEvent
    ) -> None:  # pylint: disable=invalid-name
        handle = self.handle()
        if self._grab is None or handle is None:
            return
        self._scroll_to(event.position().y() - self._grab, handle.height())

    @override
    def mouseReleaseEvent(  # pylint: disable=invalid-name
        self, event: QMouseEvent
    ) -> None:
        if event.button() == Qt.MouseButton.LeftButton:  # what started the drag
            self._grab = None
            self.update()

    @override
    def wheelEvent(self, event: QWheelEvent) -> None:  # pylint: disable=invalid-name
        QCoreApplication.sendEvent(self._bar, event)

    @override
    def enterEvent(self, event: QEnterEvent) -> None:  # pylint: disable=invalid-name
        super().enterEvent(event)
        self.update()

    @override
    def leaveEvent(self, event: QEvent) -> None:  # pylint: disable=invalid-name
        super().leaveEvent(event)
        self.update()

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        handle = self.handle()
        if handle is None:
            return
        tones = theme.tones()
        active = self.dragging() or self.underMouse()
        width = SCROLL_TRACK_ACTIVE if active else SCROLL_TRACK
        x = (self.width() - width) / 2
        radius = width / 2
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tones.field)
        painter.drawRoundedRect(QRectF(x, 0, width, self.height()), radius, radius)
        painter.setBrush(tones.text if active else tones.muted)
        painter.drawRoundedRect(
            QRectF(x, handle.top(), width, handle.height()), radius, radius
        )
        painter.end()

    @Slot(int)
    def _moved(self, value: int) -> None:
        del value
        self.update()

    @Slot(int, int)
    def _range_changed(self, low: int, high: int) -> None:
        del low, high
        self.update()
