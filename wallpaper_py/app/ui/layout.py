"""
The layout window: the monitors to scale, then one card per playlist with
its current wallpaper (docs/plan.md, Phase 4, "Look").

Everything it shows comes from the app through `Client`: it redraws from
`overview_changed` (never polls), and after each new overview asks once
for `status` for the schedule (order, interval, time left), which it counts
down locally until the next overview. Buttons send the playlist commands;
their toggles (favourite, hold) show the app's state from the overview,
never the click. A command's error shows in its card, not as a dialog.

Image details (dimensions, file size) and thumbnails are read from the
files by `ThumbnailLoader`, off the UI thread.
"""

from __future__ import annotations

import time
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import cast

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (
    QCloseEvent,
    QEnterEvent,
    QFont,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import Shiboken

from ...protocol import (
    Command,
    ErrorReply,
    MonitorView,
    Overview,
    PlaylistDetails,
    PlaylistView,
    PrevResult,
    RemoveResult,
    Reply,
    Request,
    StatusResult,
)
from . import theme
from .arrangement import MonitorsView
from .client import Client
from .thumbnails import ImageInfo, Thumbnail, ThumbnailLoader
from .window import Window

THUMBNAIL_BOX = QSize(480, 270)  # logical px; one decode serves card and monitor
CARD_MIN_WIDTH = 300
COUNTDOWN_MS = 1000

Clock = Callable[[], float]


# -- text ------------------------------------------------------------------------


def format_size(size: int) -> str:
    """Bytes for people: 980 B, 12 KB, 4.2 MB, 1.1 GB."""
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1000:
            return f"{value:.0f} {unit}" if unit != "MB" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} GB"


def format_duration(seconds: float) -> str:
    """< 1 min, 12 min, 1 h, 1 h 05 min, 2 d, 2 d 3 h (to the nearest minute)."""
    if seconds < 60:
        return "< 1 min"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h" if not minutes else f"{hours} h {minutes:02d} min"
    days, hours = divmod(hours, 24)
    return f"{days} d" if not hours else f"{days} d {hours} h"


def members_text(view: PlaylistView) -> str:
    """The playlist's monitors: "AW3225QF + PLX2783H (off)"."""
    return " + ".join(_member(m) for m in view.monitors)


def _member(monitor: MonitorView) -> str:
    if monitor.attached is None:
        return f"{monitor.name} (not found)"
    return monitor.name if monitor.attached else f"{monitor.name} (off)"


def chip_text(view: PlaylistView) -> str | None:
    if view.manual:
        return "Manual"
    return "Held" if view.held else None


def counting(view: PlaylistView, paused: str | None) -> bool:
    """Whether its time left runs down right now (as the app counts it)."""
    return not view.manual and not view.held and view.attached and paused is None


def schedule_text(
    view: PlaylistView, details: PlaylistDetails | None, left: float | None
) -> str | None:
    """
    "shuffle, 30 min · next in 12 min"; "… · 12 min left once resumed"
    (held); "shuffle · manual". `left`: seconds of active time left.
    """
    if details is None:
        return None
    order = details.order.value
    if view.manual or details.interval is None:
        return f"{order} · manual"
    text = f"{order}, {format_duration(details.interval)}"
    if left is None:
        return text
    remaining = format_duration(max(left, 0.0))
    if view.held:
        return f"{text} · {remaining} left once resumed"
    if not view.attached:
        return f"{text} · {remaining} left once a monitor is on"
    return f"{text} · next in {remaining}"


def details_text(
    view: PlaylistView,
    details: PlaylistDetails | None,
    thumbnail: Thumbnail | None,
    left: float | None,
) -> str:
    """The card's details line: dimensions · file size · order, interval · next."""
    parts: list[str] = []
    if view.current is not None and thumbnail is not None:
        info: ImageInfo | None = thumbnail.info
        if info is not None:
            parts += [f"{info.width}×{info.height}", format_size(info.file_size)]
        else:
            parts.append(f"can't read: {thumbnail.error or 'unknown error'}")
    if (schedule := schedule_text(view, details, left)) is not None:
        parts.append(schedule)
    return " · ".join(parts)


# -- the card --------------------------------------------------------------------


class ElidedLabel(QLabel):
    """One line, cut with "…" when narrower than its text (full text in the tooltip)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideMiddle, rect.width()
        )
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        painter.drawText(rect, flags, text)
        painter.end()


class PlaylistCard(QFrame):
    """
    One playlist: thumbnail, name, monitors, chip, file name, details, and
    the buttons previous, next | favourite, remove, hold. `requested` asks
    the window to send a command; the card itself holds no client.
    """

    # pylint: disable=too-many-instance-attributes

    requested = Signal(str, str)  # playlist, command
    hovered = Signal(object)  # this playlist when the pointer enters, None on leave

    def __init__(self, view: PlaylistView, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view = view
        self._highlighted = False
        self._details: PlaylistDetails | None = None
        self._thumbnail: Thumbnail | None = None
        self._left: float | None = None
        self.setMinimumWidth(CARD_MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        self.image = theme.ImageView(parent=self)
        self.title = QLabel(self)
        bold = QFont(self.title.font())
        bold.setBold(True)
        self.title.setFont(bold)
        self.link = QLabel(self)
        self.link.setToolTip("Mirrored group: the same image on every monitor")
        self.monitors = ElidedLabel(self)
        self.chip = theme.Chip(self)
        self.file = ElidedLabel(self)
        self.details = QLabel(self)
        self.details.setWordWrap(True)
        self.details.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self.message_icon = QLabel(self)
        self.message = QLabel(self)
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        self.prev_button = theme.IconButton("skip-back", "Previous", self)
        self.next_button = theme.IconButton("skip-forward", "Next", self)
        self.fav_button = theme.IconButton("star", "Favourite", self)
        self.remove_button = theme.IconButton(
            "trash-2", "Remove (list for removal and move on)", self
        )
        self.hold_button = theme.IconButton("pin", "Hold", self)
        self.prev_button.clicked.connect(self._prev)
        self.next_button.clicked.connect(self._next)
        self.fav_button.clicked.connect(self._fav)
        self.remove_button.clicked.connect(self._remove)
        self.hold_button.clicked.connect(self._hold)

        self._build()
        self._tint()
        self.show_view(view)
        self.show_message(None)

    def _build(self) -> None:
        heading = QHBoxLayout()
        heading.setSpacing(theme.space())
        heading.addWidget(self.title)
        heading.addWidget(self.link)
        heading.addWidget(self.monitors, 1)
        heading.addWidget(self.chip)

        buttons = QHBoxLayout()
        buttons.setSpacing(theme.space(0.5))
        buttons.addWidget(self.prev_button)
        buttons.addWidget(self.next_button)
        divider = QFrame(self)
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        buttons.addSpacing(theme.space(0.5))
        buttons.addWidget(divider)
        buttons.addSpacing(theme.space(0.5))
        buttons.addWidget(self.fav_button)
        buttons.addWidget(self.remove_button)
        buttons.addWidget(self.hold_button)
        buttons.addStretch(1)

        message = QHBoxLayout()
        message.setSpacing(theme.space())
        message.addWidget(self.message_icon, 0, Qt.AlignmentFlag.AlignTop)
        message.addWidget(self.message, 1)

        layout = QVBoxLayout(self)
        margin = theme.space(1.5)
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(theme.space())
        layout.addWidget(self.image)
        layout.addLayout(heading)
        layout.addWidget(self.file)
        layout.addWidget(self.details)
        layout.addLayout(buttons)
        layout.addLayout(message)

    # -- state -------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._view.name

    def view(self) -> PlaylistView:
        return self._view

    def show_view(self, view: PlaylistView) -> None:
        """Draw the playlist as the overview has it (toggles included)."""
        if view.current != self._view.current:
            self._thumbnail = None
        self._view = view
        self.title.setText(view.name)
        self.monitors.setText(members_text(view))
        self.monitors.setToolTip(members_text(view))
        mirrored = len(view.monitors) > 1
        self.link.setVisible(mirrored)
        chip = chip_text(view)
        self.chip.setText(chip or "")
        self.chip.setVisible(chip is not None)
        current = view.current
        self.file.setText(current.name if current else "Nothing shown yet")
        self.file.setToolTip(str(current) if current else "")
        shown = current is not None
        self.fav_button.setEnabled(shown)
        self.remove_button.setEnabled(shown)
        self.fav_button.set_on(view.favourite)
        self.fav_button.set_label(
            "Remove from favourites" if view.favourite else "Favourite"
        )
        self.hold_button.setVisible(not view.manual)
        self.hold_button.set_on(view.held)
        self.hold_button.set_label(
            "Resume (rotate again)" if view.held else "Hold (stop rotating)"
        )
        self.image.setAccessibleName(
            f"{view.name}: {current.name}" if current else f"{view.name}: no image"
        )
        self._show_image()
        self._show_details()

    def set_details(self, details: PlaylistDetails | None, left: float | None) -> None:
        """The schedule from `status`; `left`: seconds of active time left now."""
        self._details = details
        self._left = left
        self._show_details()

    def set_thumbnail(self, thumbnail: Thumbnail | None) -> None:
        self._thumbnail = thumbnail
        self._show_image()
        self._show_details()

    def thumbnail(self) -> Thumbnail | None:
        return self._thumbnail

    def show_message(self, text: str | None, error: bool = False) -> None:
        """A note under the buttons (e.g. a command's error); None hides it."""
        self.message.setText(text or "")
        self.message.setVisible(bool(text))
        self.message_icon.setVisible(bool(text) and error)

    def _show_image(self) -> None:
        if self._view.current is None:
            self.image.set_pixmap(None, "Nothing shown yet")
        elif self._thumbnail is None:
            self.image.set_pixmap(None, "Loading…")
        elif self._thumbnail.pixmap is None:
            self.image.set_pixmap(None, "Can't show this image")
        else:
            self.image.set_pixmap(self._thumbnail.pixmap)

    def _show_details(self) -> None:
        text = details_text(self._view, self._details, self._thumbnail, self._left)
        self.details.setText(text)
        self.details.setVisible(bool(text))

    def _tint(self) -> None:
        """The small icons in the text, from the palette (buttons do their own)."""
        color = self.palette().color(QPalette.ColorRole.WindowText)
        ratio = self.devicePixelRatioF()
        self.link.setPixmap(theme.tinted_pixmap("link", color, 16, ratio))
        self.message_icon.setPixmap(
            theme.tinted_pixmap("circle-alert", color, 16, ratio)
        )

    # -- painting ----------------------------------------------------------------

    def set_highlighted(self, highlighted: bool) -> None:
        """An accent edge while this playlist is pointed at (here or a monitor)."""
        if highlighted != self._highlighted:
            self._highlighted = highlighted
            self.update()

    def highlighted(self) -> bool:
        return self._highlighted

    def enterEvent(self, event: QEnterEvent) -> None:  # pylint: disable=invalid-name
        super().enterEvent(event)
        self.hovered.emit(self._view.name)

    def leaveEvent(self, event: QEvent) -> None:  # pylint: disable=invalid-name
        super().leaveEvent(event)
        self.hovered.emit(None)

    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._highlighted:
            painter.setPen(QPen(theme.accent(self.palette()), 2))
            rect = self.rect().toRectF().adjusted(1, 1, -1, -1)
        else:
            painter.setPen(self.palette().color(QPalette.ColorRole.Mid))
            rect = self.rect().toRectF().adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(self.palette().color(QPalette.ColorRole.Base))
        painter.drawRoundedRect(rect, theme.RADIUS, theme.RADIUS)
        painter.end()

    def changeEvent(self, event: QEvent) -> None:  # pylint: disable=invalid-name
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self._tint()

    # -- buttons -----------------------------------------------------------------

    def _send(self, command: Command) -> None:
        self.requested.emit(self._view.name, command)

    @Slot()
    def _prev(self) -> None:
        self._send("prev")

    @Slot()
    def _next(self) -> None:
        self._send("next")

    @Slot()
    def _fav(self) -> None:
        # The overview's state decides, not the button's.
        self._send("unfav" if self._view.favourite else "fav")

    @Slot()
    def _remove(self) -> None:
        self._send("remove")

    @Slot()
    def _hold(self) -> None:
        self._send("resume" if self._view.held else "hold")


# -- the window ------------------------------------------------------------------


class _ReplyTo:
    """
    A reply callback for `Client.run` that holds the window weakly: the
    window may close (and be deleted) before the reply arrives.
    """

    def __init__(self, window: LayoutWindow, playlist: str | None) -> None:
        self._window = weakref.ref(window)
        self._playlist = playlist

    def __call__(self, reply: Reply) -> None:
        window = self._window()
        if window is not None and Shiboken.isValid(window):
            window.replied(self._playlist, reply)


class LayoutWindow(Window):
    """See the module docstring."""

    # pylint: disable=too-many-instance-attributes

    def __init__(
        self,
        client: Client,
        loader: ThumbnailLoader | None = None,
        clock: Clock = time.monotonic,
    ) -> None:
        super().__init__()
        self.setWindowTitle("wallpaper_py · Layout")
        self.resize(980, 760)
        self._client = client
        self._clock = clock
        self._overview: Overview | None = None
        self._details: dict[str, PlaylistDetails] = {}
        self._details_at = 0.0
        self._status_running = False
        self._status_again = False
        self._closed = False
        self.cards: dict[str, PlaylistCard] = {}
        self._columns = 0

        if loader is None:
            ratio = self.devicePixelRatioF()
            loader = ThumbnailLoader(THUMBNAIL_BOX * ratio, parent=self)
        self.loader = loader
        self.loader.loaded.connect(self._thumbnail_loaded)

        self.banner = QLabel(self)
        self.banner.setWordWrap(True)
        self.banner.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.monitors_view = MonitorsView(self)
        self.monitors_view.hovered.connect(self._hover)
        self.empty = QLabel("No playlists in the config yet.", self)
        self.empty.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self._grid_widget = QWidget(self)
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(theme.space(2))
        self._build()

        self._countdown = QTimer(self)
        self._countdown.setInterval(COUNTDOWN_MS)
        self._countdown.timeout.connect(self.refresh_schedule)

        client.overview_changed.connect(self.show_overview)
        client.failed.connect(self.show_error)
        if client.overview is not None:
            self.show_overview(client.overview)
        else:
            self._set_banner("Connecting to wallpaper_py…")

    def _build(self) -> None:
        content = QWidget(self)
        layout = QVBoxLayout(content)
        margin = theme.space(2)
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(theme.space(1.5))
        layout.addWidget(self.banner)
        layout.addWidget(_heading("Monitors", content))
        layout.addWidget(self.monitors_view)
        layout.addSpacing(theme.space())
        layout.addWidget(_heading("Playlists", content))
        layout.addWidget(self.empty)
        layout.addWidget(self._grid_widget)
        layout.addStretch(1)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._scroll)

    # -- from the app ------------------------------------------------------------

    @Slot(object)
    def show_overview(self, overview: Overview) -> None:
        self._overview = overview
        if overview.error:
            self._set_banner(f"Error: {overview.error}")
        elif overview.paused:
            self._set_banner(f"Rotation paused: {overview.paused}")
        else:
            self._set_banner(None)
        self.monitors_view.show_overview(overview)
        self._update_cards(overview)
        current = [p.current for p in overview.playlists if p.current is not None]
        self.loader.want(current)
        for path in current:  # the cached ones now; the others once loaded
            self._give_thumbnail(path)
        self._refresh_details()
        self._ask_status()

    @Slot(str)
    def show_error(self, message: str) -> None:
        self._set_banner(f"Error: {message}")

    def replied(self, playlist: str | None, reply: Reply) -> None:
        """A command's reply (`playlist` None: the status request)."""
        if self._closed:
            return
        if playlist is None:
            self._status_replied(reply)
            return
        card = self.cards.get(playlist)
        if card is None:
            return
        if isinstance(reply, ErrorReply):
            card.show_message(reply.error.message, error=True)
        elif isinstance(reply, PrevResult) and reply.change is None:
            card.show_message("Nothing earlier in the history")
        elif isinstance(reply, RemoveResult) and reply.change is None:
            card.show_message(
                f"{reply.image.name} is listed for removal; the playlist "
                "moves on once a monitor is on"
            )
        else:
            card.show_message(None)

    # -- cards -------------------------------------------------------------------

    def _update_cards(self, overview: Overview) -> None:
        names = [p.name for p in overview.playlists]
        for name in [n for n in self.cards if n not in names]:
            gone = self.cards.pop(name)
            self._grid.removeWidget(gone)
            gone.deleteLater()
        for view in overview.playlists:
            card = self.cards.get(view.name)
            if card is None:
                new = PlaylistCard(view, self._grid_widget)
                new.requested.connect(self._send)
                new.hovered.connect(self._hover)
                self.cards[view.name] = new
            else:
                card.show_view(view)
        # In the overview's (the config's) order.
        self.cards = {name: self.cards[name] for name in names}
        self.empty.setVisible(not names and not overview.error)
        self._columns = 0  # lay out again
        self._reflow()

    def _reflow(self) -> None:
        width = self._scroll.viewport().width() - 2 * theme.space(2)
        spacing = self._grid.horizontalSpacing()
        columns = max(1, (width + spacing) // (CARD_MIN_WIDTH + spacing))
        columns = min(columns, max(1, len(self.cards)))
        if columns == self._columns:
            return
        self._columns = columns
        for card in self.cards.values():
            self._grid.removeWidget(card)
        for index, card in enumerate(self.cards.values()):
            self._grid.addWidget(
                card, index // columns, index % columns, Qt.AlignmentFlag.AlignTop
            )
        for column in range(self._grid.columnCount()):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)

    def columns(self) -> int:
        return self._columns

    @Slot(object)
    def _hover(self, playlist: str | None) -> None:
        """A card or a monitor is pointed at: highlight its playlist in both."""
        self.monitors_view.set_highlight(playlist)
        for name, card in self.cards.items():
            card.set_highlighted(name == playlist)

    @Slot(str, str)
    def _send(self, playlist: str, command: str) -> None:
        card = self.cards.get(playlist)
        if card is not None:
            card.show_message(None)
        request = Request(command=cast(Command, command), target=playlist)
        self._client.run(request, _ReplyTo(self, playlist))

    # -- thumbnails --------------------------------------------------------------

    @Slot(object)
    def _thumbnail_loaded(self, path: Path) -> None:
        self._give_thumbnail(path)

    def _give_thumbnail(self, path: Path) -> None:
        thumbnail = self.loader.get(path)
        if thumbnail is None:
            return
        for card in self.cards.values():
            if card.view().current == path:  # only where it is still current
                card.set_thumbnail(thumbnail)
        self.monitors_view.set_image(path, thumbnail.pixmap)

    # -- the schedule (status) ---------------------------------------------------

    def _ask_status(self) -> None:
        """One status request at a time; ask again if more came meanwhile."""
        if self._overview is None or self._overview.error:
            return
        if self._status_running:
            self._status_again = True
            return
        self._status_running = True
        self._client.run(Request(command="status"), _ReplyTo(self, None))

    def _status_replied(self, reply: Reply) -> None:
        self._status_running = False
        if self._status_again:  # the overview changed meanwhile: ask again
            self._status_again = False
            self._ask_status()
        if isinstance(reply, StatusResult):
            self._details = {p.name: p for p in reply.playlists}
            self._details_at = self._clock()
            self._refresh_details()
        elif isinstance(reply, ErrorReply) and reply.error.kind == "unreachable":
            self.show_error(reply.error.message)

    def _left(self, view: PlaylistView) -> float | None:
        details = self._details.get(view.name)
        if details is None or details.next_in_seconds is None:
            return None
        left = details.next_in_seconds
        paused = self._overview.paused if self._overview else None
        if counting(view, paused):
            left -= self._clock() - self._details_at
        return left

    def _refresh_details(self) -> None:
        paused = self._overview.paused if self._overview else None
        any_counting = False
        for card in self.cards.values():
            view = card.view()
            card.set_details(self._details.get(view.name), self._left(view))
            any_counting |= counting(view, paused) and view.name in self._details
        if any_counting and not self._countdown.isActive():
            self._countdown.start()
        elif not any_counting:
            self._countdown.stop()

    @Slot()
    def refresh_schedule(self) -> None:
        """Redraw the time left (the countdown timer calls it every second)."""
        self._refresh_details()

    # -- window ------------------------------------------------------------------

    def _set_banner(self, text: str | None) -> None:
        self.banner.setText(text or "")
        self.banner.setVisible(bool(text))

    def resizeEvent(self, event: QResizeEvent) -> None:  # pylint: disable=invalid-name
        super().resizeEvent(event)
        self._reflow()

    def closeEvent(self, event: QCloseEvent) -> None:  # pylint: disable=invalid-name
        super().closeEvent(event)
        if event.isAccepted():
            self._closed = True
            self._countdown.stop()
            self.loader.close()


def _heading(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    font = QFont(label.font())
    font.setBold(True)
    font.setPointSizeF(font.pointSizeF() * 1.15)
    label.setFont(font)
    label.setAccessibleName(text)
    return label
