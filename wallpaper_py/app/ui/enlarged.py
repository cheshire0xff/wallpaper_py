"""
The Library's enlarged view (docs/plan.md, Phase 5, "Enlarged view"): one
image over the whole page, on the panel tone, opened by a click on a tile.
← / → (or the arrows in its bar) go to the image before / after in the
order the grid showed; Esc, *Back* or the mouse's back button go back to
the grid.

    [←] name.jpg  12 of 312          [‹] [›]  [★] [🗑]  [Show on] [konachan ▾]
    the image, on the panel tone, and over its bottom left the info card:
        D:/pictures/KONACHAN/name.jpg
        3840×2160 · 4.2 MB                 Copy path

`ImageCanvas` shows the image fitted at first (never enlarged past its own
pixels). The wheel zooms at the pointer, up to 5x the fit (and always as
far as the image's own pixels); a double-click toggles between the fit and
twice it (at least the image's own pixels); dragging pans; a click (not a
drag, not a double-click) hides or shows the info card. The file is decoded
at full size off the main thread (`read_full`, through a `LatestRead`, so
going quickly through images decodes only the last one); until then the
grid's thumbnail stands in, a 16:9 crop fitted to the view. Shown smaller
than its own pixels, the image is drawn from a smooth copy at that size
(drawing it scaled samples it, which shimmers on fine lines); larger,
directly.

The info card: the full path (selectable), the dimensions and file size
(or why it can't be shown), and *Copy path*. The toggles, favourite and to
remove, go by path (`fav` / `unfav`, `remove` / `unremove` with an
`image`), and show the lists as the page last read them, never the click
(as on Home). *Show on <playlist>* sends `pick`; with several playlists a
drop-down chooses which (at first the page's). A command's error shows
under the bar.

The images are the grid's when it was opened (a snapshot): one taken off
the grid meanwhile (listed to remove, with *Hide removed* on) can still be
gone back to.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import override

from PySide6.QtCore import QPointF, QRectF, QSize, QSizeF, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (
    QGuiApplication,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPixmap,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ...core.imaging import load_full
from ...core.model import ImageLoadError
from ...core.settings import PlaylistId
from ...protocol import ErrorReply, MarkCommand, MarkRequest, PickRequest, Reply
from ...texts import format_size
from ..client import Client, ReplyTo
from . import theme
from .controls import Button
from .entries import Entry
from .grid import Marks, draw_name
from .home import ElidedLabel
from .jobs import LatestRead
from .library_stats import count
from .settings_form import combo, select
from .thumbnails import ImageInfo, header_info
from .tiles import TILE, TileLoader

MAX_ZOOM = 5.0  # of the fit
ZOOM_STEP = 1.25  # per notch of the wheel
DOUBLE_CLICK_ZOOM = 2.0  # of the fit
CARD_INSET = theme.space(2)  # the info card from the image area's corner
CARD_ALPHA = 0.9


# -- reading the image ------------------------------------------------------------


@dataclass(frozen=True)
class Full:
    """An image decoded at its own size (`read_full`), or why it wasn't."""

    image: QImage  # RGB32 (drawn as is); null if not decoded
    info: ImageInfo | None  # its size and the file's, if known
    missing: bool = False  # the file is gone
    error: str | None = None  # it can't be read or decoded


def read_full(path: Path) -> Full:
    """The image at its own size, off the main thread (QImage is safe there)."""
    try:
        file_size = path.stat().st_size
    except FileNotFoundError:
        return Full(QImage(), None, missing=True)
    except OSError as err:
        return Full(QImage(), None, error=err.strerror or str(err))
    try:
        image = load_full(path)
    except ImageLoadError as err:
        why = str(err.__cause__ or err)
        return Full(QImage(), header_info(path, file_size), error=why)
    width, height = image.size
    data = image.tobytes("raw", "RGB")
    view = QImage(data, width, height, 3 * width, QImage.Format.Format_RGB888)
    converted = view.convertToFormat(QImage.Format.Format_RGB32)  # pixels of its own
    return Full(converted, ImageInfo(width, height, file_size))


def full_failed(error: Exception) -> Full:
    """`read_full` raised (`LatestRead`): why, in place of the image."""
    return Full(QImage(), None, error=str(error))


def details(full: Full) -> str:
    """The info card's second line: "3840×2160 · 4.2 MB", or why not."""
    if full.missing:
        return "Missing: the file is gone"
    parts: list[str] = []
    if full.info is not None:
        parts += [f"{full.info.width}×{full.info.height}"]
        parts += [format_size(full.info.file_size)]
    if full.error is not None:
        parts.append(f"can't open: {full.error}")
    return " · ".join(parts)


# -- zoom and pan, as numbers ------------------------------------------------------


def fit_scale(image: QSizeF, view: QSizeF, ratio: float) -> float:
    """
    Logical px per image px that fit `image` in `view`, never past its own
    pixels (one image px per device px at the display scale `ratio`).
    """
    own = 1.0 / ratio
    if image.isEmpty() or view.isEmpty():
        return own
    return min(view.width() / image.width(), view.height() / image.height(), own)


def max_scale(fit: float, ratio: float) -> float:
    """The most the wheel zooms: 5x the fit, and at least the image's own pixels."""
    return max(MAX_ZOOM * fit, 1.0 / ratio)


def placed(offset: QPointF, image: QSizeF, scale: float, view: QSizeF) -> QPointF:
    """
    The image's top left kept in the view: centred along a side where it is
    smaller than the view, else with no gap at either end.
    """

    def along(at: float, length: float, room: float) -> float:
        if length <= room:
            return (room - length) / 2
        return min(0.0, max(room - length, at))

    return QPointF(
        along(offset.x(), image.width() * scale, view.width()),
        along(offset.y(), image.height() * scale, view.height()),
    )


def zoomed_at(offset: QPointF, scale: float, new: float, point: QPointF) -> QPointF:
    """The top left at scale `new` that keeps the image under `point` there."""
    return point - (point - offset) * (new / scale)


# -- the image --------------------------------------------------------------------


class ImageCanvas(QWidget):
    """The image, fitted, zoomed and panned (see the module docstring)."""

    # pylint: disable=too-many-public-methods

    # pylint: disable=too-many-instance-attributes

    clicked = Signal()  # once, not a drag or a double-click: the card

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self._smooth: QImage | None = None  # _image at the size shown (smaller)
        self._stand_in: QPixmap | None = None  # until the image is read
        self._lines: tuple[str, str] | None = None  # no image: the name, why
        self._scale = 1.0  # logical px per image px
        self._offset = QPointF()  # the image's top left
        self._fitted = True
        self._press: QPointF | None = None  # where the left button went down
        self._last = QPointF()  # while dragging: the pointer before
        self._dragging = False
        self._quiet_until = 0.0  # time.monotonic(): clicks ignored before this
        self._click = QTimer(self)  # a click is one once no second one came
        self._click.setSingleShot(True)
        self._click.setInterval(QApplication.doubleClickInterval())
        self._click.timeout.connect(self._clicked_once)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # its keys go to the view
        self.setAccessibleName("Image")

    # -- what it shows ---------------------------------------------------------------

    def show_loading(self, stand_in: QPixmap | None) -> None:
        """Another image, being read: `stand_in` (the grid's tile) until then."""
        self._image = self._smooth = None
        self._stand_in = stand_in
        self._lines = None
        self._fitted = True
        self._click.stop()
        self._refit()

    def set_stand_in(self, stand_in: QPixmap | None) -> None:
        if self._image is None:
            self._stand_in = stand_in
            self.update()

    def set_image(self, image: QImage) -> None:
        """The image, read: fitted."""
        self._image = image
        self._smooth = None
        self._fitted = True
        self._refit()

    def set_lines(self, name: str, why: str) -> None:
        """No image (it can't be read): its name, and why, in the middle."""
        self._image = self._smooth = self._stand_in = None
        self._lines = (name, why)
        self._refit()

    def image(self) -> QImage | None:
        return self._image

    def stand_in(self) -> QPixmap | None:
        return self._stand_in if self._image is None else None

    def lines(self) -> tuple[str, str] | None:
        return self._lines

    def quiet(self) -> None:
        """
        Just opened by a click: a second click of a double-click on the
        tile mustn't zoom or hide the card.
        """
        interval = QApplication.doubleClickInterval() / 1000
        self._quiet_until = time.monotonic() + interval

    # -- zoom and pan ----------------------------------------------------------------

    def scale(self) -> float:
        return self._scale

    def offset(self) -> QPointF:
        return QPointF(self._offset)

    def fitted(self) -> bool:
        return self._fitted

    def fit(self) -> float:
        return fit_scale(self._size(), QSizeF(self.size()), self.devicePixelRatioF())

    def zoom_to(self, scale: float, at: QPointF) -> None:
        """Zoom to `scale` (kept between the fit and the most), `at` staying put."""
        if self._image is None:
            return
        fit = self.fit()
        new = min(max_scale(fit, self.devicePixelRatioF()), max(fit, scale))
        self._offset = zoomed_at(self._offset, self._scale, new, at)
        self._scale = new
        self._fitted = math.isclose(new, fit)
        self._place()

    def toggle_zoom(self, at: QPointF) -> None:
        """Back to the fit if zoomed, else twice it (at least its own pixels)."""
        if not self._fitted:
            self._fitted = True
            self._refit()
            return
        own = 1.0 / self.devicePixelRatioF()
        self.zoom_to(max(DOUBLE_CLICK_ZOOM * self.fit(), own), at)

    def pan_by(self, delta: QPointF) -> None:
        self._offset += delta
        self._place()

    def _size(self) -> QSizeF:
        return QSizeF(self._image.size()) if self._image is not None else QSizeF()

    def _refit(self) -> None:
        self._scale = self.fit()
        self._offset = QPointF()
        self._place()

    def _place(self) -> None:
        self._offset = placed(
            self._offset, self._size(), self._scale, QSizeF(self.size())
        )
        self._set_cursor()
        self.update()

    def _set_cursor(self) -> None:
        if self._dragging:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif not self._fitted:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()

    # -- the pointer -------------------------------------------------------------------

    def _quiet(self) -> bool:
        return time.monotonic() < self._quiet_until

    @override
    def wheelEvent(self, event: QWheelEvent) -> None:  # pylint: disable=invalid-name
        notches = event.angleDelta().y() / 120
        if self._image is None or not notches:
            event.ignore()
            return
        self.zoom_to(self._scale * ZOOM_STEP**notches, event.position())

    @override
    def mousePressEvent(  # pylint: disable=invalid-name
        self, event: QMouseEvent
    ) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()  # to the view: the back button
            return
        if not self._quiet():
            self._press = self._last = event.position()

    @override
    def mouseMoveEvent(  # pylint: disable=invalid-name
        self, event: QMouseEvent
    ) -> None:
        if self._press is None:
            return
        point = event.position()
        if not self._dragging:
            moved = (point - self._press).manhattanLength()
            if moved < QApplication.startDragDistance():
                return
            self._dragging = True
        self.pan_by(point - self._last)
        self._last = point

    @override
    def mouseReleaseEvent(  # pylint: disable=invalid-name
        self, event: QMouseEvent
    ) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        if self._press is not None and not self._dragging:
            self._click.start()
        self._press = None
        self._dragging = False
        self._set_cursor()

    @override
    def mouseDoubleClickEvent(  # pylint: disable=invalid-name
        self, event: QMouseEvent
    ) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._press = None  # its release is no click
        if self._quiet():
            return
        self._click.stop()  # the first click was half of this
        self.toggle_zoom(event.position())

    @Slot()
    def _clicked_once(self) -> None:
        self.clicked.emit()

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:  # pylint: disable=invalid-name
        super().resizeEvent(event)
        if self._fitted or self._scale < self.fit():
            self._fitted = True
            self._refit()
        else:  # kept within the new limits, the middle staying put
            old = event.oldSize()
            if old.isValid():
                grown = event.size() - old
                self._offset += QPointF(grown.width(), grown.height()) / 2
            self.zoom_to(self._scale, QRectF(self.rect()).center())

    # -- painting ----------------------------------------------------------------------

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        painter = QPainter(self)
        area = QRectF(self.rect())
        painter.fillRect(area, theme.tones().panel)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._image is not None:
            self._draw_image(painter)
        elif self._stand_in is not None:
            source = QRectF(self._stand_in.rect())
            painter.drawPixmap(self._stand_in_rect(), self._stand_in, source)
        elif self._lines is not None:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            draw_name(painter, area, *self._lines)
        painter.end()

    def _draw_image(self, painter: QPainter) -> None:
        assert self._image is not None
        shown = QSizeF(self._image.size()) * self._scale
        target = QRectF(self._offset, shown)
        ratio = self.devicePixelRatioF()
        if self._scale * ratio < 1:  # smaller than its own pixels: a smooth copy
            pixels = QSize(round(shown.width() * ratio), round(shown.height() * ratio))
            if self._smooth is None or self._smooth.size() != pixels:
                self._smooth = self._image.scaled(
                    pixels,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            painter.drawImage(target, self._smooth)
        else:
            painter.drawImage(target, self._image)

    def _stand_in_rect(self) -> QRectF:
        """The tile's 16:9 fitted in the middle."""
        tile = QSizeF(TILE)
        view = QSizeF(self.size())
        scale = min(view.width() / tile.width(), view.height() / tile.height())
        shown = tile * scale
        corner = QPointF(
            (view.width() - shown.width()) / 2, (view.height() - shown.height()) / 2
        )
        return QRectF(corner, shown)


# -- the info card ---------------------------------------------------------------


class InfoCard(QWidget):
    """
    The path (selectable), the details and *Copy path*, on the page tone,
    over the image's bottom left (inset from the corner). The path takes
    the width of its text, up to the room the view gives (`set_room`), and
    wraps beyond it.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._room = 0  # px it may take across (0: not known yet)
        self.path = QLabel(self)
        self.path.setWordWrap(True)
        self.path.setTextFormat(Qt.TextFormat.PlainText)
        self.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path.setCursor(Qt.CursorShape.IBeamCursor)
        self.details = theme.muted("", self)
        self.copy_button = theme.TextButton("Copy path", self)
        self.copy_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)  # see _build
        self.copy_button.clicked.connect(self._copy)

        below = QHBoxLayout()
        below.setSpacing(theme.space(2))
        below.addWidget(self.details, 1)
        below.addWidget(self.copy_button)
        layout = QVBoxLayout(self)
        padding = theme.space(1.5)
        layout.setContentsMargins(
            CARD_INSET + padding, padding, padding, CARD_INSET + padding
        )
        layout.setSpacing(theme.space(0.5))
        layout.addWidget(self.path)
        layout.addLayout(below)
        self._layout = layout

    def show_info(self, path: Path, details_line: str) -> None:
        self.path.setText(str(path))
        self.details.setText(details_line)
        self._fit()

    def set_room(self, width: int) -> None:
        """The most it may take across, inset included."""
        self._room = width
        self._fit()

    def _fit(self) -> None:
        margins = self._layout.contentsMargins()
        room = self._room - margins.left() - margins.right()
        text = self.path.fontMetrics().horizontalAdvance(self.path.text()) + 1
        self.path.setFixedWidth(max(0, min(text, room)) if room > 0 else text)

    @Slot()
    def _copy(self) -> None:
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(self.path.text())

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect()).adjusted(CARD_INSET, 0, 0, -CARD_INSET)
        path = QPainterPath()
        path.addRoundedRect(box, theme.RADIUS, theme.RADIUS)
        painter.fillPath(path, theme.with_alpha(theme.tones().page, CARD_ALPHA))
        painter.end()


# -- the view ----------------------------------------------------------------------


class EnlargedView(QWidget):
    """See the module docstring. `shutdown()` before the owner goes away."""

    # pylint: disable=too-many-instance-attributes

    closed = Signal()  # back to the grid

    def __init__(
        self, client: Client, tiles: TileLoader, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._tiles = tiles
        self._entries: list[Entry] = []
        self._row = 0
        self._marks: Marks | None = None  # None: the lists aren't read yet
        self._playlists: list[tuple[PlaylistId, str]] | None = None  # not set yet
        self._full: Full | None = None  # the image shown, once read

        self.back_button = theme.IconButton(
            "arrow-left", "Back to the grid (Esc)", self
        )
        self.name = ElidedLabel(self)
        self.name.setFont(theme.scaled_font(self.name.font(), 1.15, bold=True))
        self.position = theme.muted("", self)
        self.previous_button = theme.IconButton(
            "chevron-left", "Previous image (←)", self
        )
        self.next_button = theme.IconButton("chevron-right", "Next image (→)", self)
        self.fav_button = theme.IconButton("star", "Favourite", self)
        self.remove_button = theme.IconButton("trash-2", "List to remove", self)
        self.show_button = Button("Show on", self)
        self.show_combo = combo(self, "Show on playlist")
        self.banner = theme.Banner(self)
        self.canvas = ImageCanvas(self)
        self.card = InfoCard(self)
        self.full_read = LatestRead(self)
        self._build()

        self.back_button.clicked.connect(self.leave)
        self.previous_button.clicked.connect(self.show_previous)
        self.next_button.clicked.connect(self.show_next)
        self.fav_button.clicked.connect(self._toggle_favourite)
        self.remove_button.clicked.connect(self._toggle_removal)
        self.show_button.clicked.connect(self._show_on)
        self.canvas.clicked.connect(self._toggle_card)
        self.full_read.done.connect(self._full_read)
        tiles.loaded.connect(self._tile_loaded)
        self.set_playlists([])

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:  # pylint: disable=invalid-name
        super().resizeEvent(event)
        self.card.set_room(event.size().width() - CARD_INSET)

    def _build(self) -> None:
        top = QHBoxLayout()
        top.setContentsMargins(
            theme.space(2), theme.PAGE_TOP, theme.PAGE_MARGIN, theme.space(1.5)
        )
        top.setSpacing(theme.space())
        top.addWidget(self.back_button)
        top.addWidget(self.name, 1)
        top.addWidget(self.position)
        top.addSpacing(theme.space(2))
        for button in (self.previous_button, self.next_button):
            top.addWidget(button)
        top.addSpacing(theme.space(2))
        for button in (self.fav_button, self.remove_button):
            top.addWidget(button)
        top.addSpacing(theme.space(2))
        top.addWidget(self.show_button)
        top.addWidget(self.show_combo)
        # A click leaves the focus on the image, so ← / → still go through
        # the images (a focused button takes the arrows to move the focus).
        buttons = (
            self.back_button,
            self.previous_button,
            self.next_button,
            self.fav_button,
            self.remove_button,
            self.show_button,
        )
        for widget in buttons:
            widget.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        banner = QHBoxLayout()
        banner.setContentsMargins(
            theme.PAGE_MARGIN, 0, theme.PAGE_MARGIN, theme.space()
        )
        banner.addWidget(self.banner)

        stage = QGridLayout()  # the card over the image's bottom left
        stage.setContentsMargins(0, 0, 0, 0)
        stage.addWidget(self.canvas, 0, 0)
        corner = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
        stage.addWidget(self.card, 0, 0, corner)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(top)
        layout.addLayout(banner)
        layout.addLayout(stage, 1)

    # -- what the page sets ------------------------------------------------------------

    def open_entries(
        self, entries: Sequence[Entry], row: int, playlist: PlaylistId | None
    ) -> None:
        """
        Show `entries[row]`, with `entries` to go through (the grid's, as
        shown); *Show on* names `playlist` at first.
        """
        self._entries = list(entries)
        if playlist is not None:
            select(self.show_combo, playlist)
        self._show_row(row)
        self.canvas.quiet()
        self.canvas.setFocus()

    def set_marks(self, marks: Marks | None) -> None:
        """The lists as read (None: not yet): what the toggles show."""
        self._marks = marks
        self._show_marks()

    def set_playlists(self, playlists: Sequence[tuple[PlaylistId, str]]) -> None:
        """The playlists *Show on* offers (id, name), in the settings' order."""
        if list(playlists) == self._playlists:
            return  # a new overview, the same playlists: the choice stays
        chosen = self.show_combo.currentData()
        self._playlists = list(playlists)
        box = self.show_combo
        box.blockSignals(True)
        box.clear()
        for playlist_id, name in self._playlists:
            box.addItem(name, playlist_id)
        box.setCurrentIndex(max(0, box.findData(chosen)))
        box.blockSignals(False)
        several = len(self._playlists) > 1
        self.show_combo.setVisible(several)
        self.show_button.setVisible(bool(self._playlists))
        one = self._playlists[0][1] if len(self._playlists) == 1 else None
        self.show_button.set_label(f"Show on {one}" if one else "Show on")

    def current(self) -> Entry | None:
        """The image shown (None: none was opened)."""
        if not 0 <= self._row < len(self._entries):
            return None
        return self._entries[self._row]

    def row(self) -> int:
        """Its place in the grid's order (as when opened)."""
        return self._row

    def full(self) -> Full | None:
        """The image shown, as read (None: not yet)."""
        return self._full

    def shutdown(self) -> None:
        """The window closes: stop the reader (waits for it)."""
        self.full_read.close()

    # -- going through the images --------------------------------------------------

    @Slot()
    def show_previous(self) -> None:
        if self._row > 0:
            self._show_row(self._row - 1)

    @Slot()
    def show_next(self) -> None:
        if self._row + 1 < len(self._entries):
            self._show_row(self._row + 1)

    @Slot()
    def leave(self) -> None:
        """Back to the grid, which scrolls to `current()`; then nothing is kept."""
        self.closed.emit()
        self._entries = []
        self._full = None
        self.canvas.show_loading(None)  # a late read finds no image to show

    def _show_row(self, row: int) -> None:
        self._row = min(max(row, 0), len(self._entries) - 1)
        entry = self.current()
        if entry is None:
            return
        path = entry.path
        self._full = None
        self.banner.set_message(None)
        self.name.setText(path.name)
        self.name.setToolTip(path.name)
        self.position.setText(f"{count(self._row + 1)} of {count(len(self._entries))}")
        self.previous_button.setEnabled(self._row > 0)
        self.next_button.setEnabled(self._row + 1 < len(self._entries))
        tile = self._tiles.get(path)
        if tile is None:
            self._tiles.set_wanted((path,))  # the images passed: not any more
            self._tiles.request(path)
        self.canvas.show_loading(tile.pixmap if tile else None)
        self.card.show_info(path, "")
        self._show_marks()
        self.full_read.read(path, partial(read_full, path), full_failed)

    @Slot(object)
    def _tile_loaded(self, path: Path) -> None:
        entry = self.current()
        if self._full is None and entry is not None and entry.path == path:
            tile = self._tiles.get(path)
            self.canvas.set_stand_in(tile.pixmap if tile else None)

    @Slot(object, object)
    def _full_read(self, path: Path, full: Full) -> None:
        entry = self.current()
        if entry is None or entry.path != path:
            return
        self._full = full
        if not full.image.isNull():
            self.canvas.set_image(full.image)
        else:
            self.canvas.set_lines(
                path.name, "Missing" if full.missing else "Can't open"
            )
        self.card.show_info(path, details(full))

    @Slot()
    def _toggle_card(self) -> None:
        self.card.setVisible(self.card.isHidden())

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:  # pylint: disable=invalid-name
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.leave()
        elif key == Qt.Key.Key_Left:
            self.show_previous()
        elif key == Qt.Key.Key_Right:
            self.show_next()
        else:
            super().keyPressEvent(event)

    @override
    def mousePressEvent(  # pylint: disable=invalid-name
        self, event: QMouseEvent
    ) -> None:
        if event.button() == Qt.MouseButton.BackButton:
            self.leave()
        else:
            super().mousePressEvent(event)

    # -- the commands ----------------------------------------------------------------

    def _show_marks(self) -> None:
        """The toggles: the lists as read, for the image shown."""
        entry, marks = self.current(), self._marks
        known = favourite = listed = False
        if entry is not None and marks is not None:
            known = True
            favourite = entry.key in marks.favourites
            listed = entry.key in marks.to_remove
        self.fav_button.setEnabled(known)
        self.fav_button.set_on(favourite)
        self.fav_button.set_label(
            "Remove from favourites" if favourite else "Favourite"
        )
        self.remove_button.setEnabled(known)
        self.remove_button.set_on(listed)
        self.remove_button.set_label(
            "Take off the to-remove list" if listed else "List to remove"
        )

    def _mark(self, command: MarkCommand) -> None:
        entry = self.current()
        if entry is not None and self._sendable(entry):
            self._send(MarkRequest(command=command, image=entry.path), entry, "")

    @Slot()
    def _toggle_favourite(self) -> None:
        entry, marks = self.current(), self._marks
        if entry is not None and marks is not None:
            self._mark("unfav" if entry.key in marks.favourites else "fav")

    @Slot()
    def _toggle_removal(self) -> None:
        entry, marks = self.current(), self._marks
        if entry is not None and marks is not None:
            self._mark("unremove" if entry.key in marks.to_remove else "remove")

    @Slot()
    def _show_on(self) -> None:
        entry = self.current()
        index = self.show_combo.currentIndex()
        playlists = self._playlists or []
        if entry is None or not 0 <= index < len(playlists):
            return
        if not self._sendable(entry):
            return
        playlist_id, name = playlists[index]
        request = PickRequest(target=playlist_id, image=entry.path)
        self._send(request, entry, f"Shown on {name}.")

    def _sendable(self, entry: Entry) -> bool:
        """
        Whether the daemon can be told of the image: a list's line may not
        be a full path (written by hand, or from another system), and the
        daemon has no directory to read it against.
        """
        if entry.path.is_absolute():
            return True
        self.banner.set_message(
            f"Error: not a full path, edit the list by hand: {entry.path}"
        )
        return False

    def _send(
        self, request: MarkRequest | PickRequest, entry: Entry, done: str
    ) -> None:
        self.banner.set_message(None)
        reply_to = ReplyTo(self, EnlargedView.replied, entry.path, done)
        self._client.run(request, reply_to)

    def replied(self, path: Path, done: str, reply: Reply) -> None:
        """
        A command's reply for the image at `path`: its error under the bar,
        else `done` if any. Not once another image is shown.
        """
        entry = self.current()
        if entry is None or entry.path != path:
            return
        if isinstance(reply, ErrorReply):
            self.banner.set_message(f"Error: {reply.error.message}")
        else:
            self.banner.set_message(done or None)
