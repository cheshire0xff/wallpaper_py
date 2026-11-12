"""
Home, the main window's first page: the monitors to scale on a panel, then
one row per playlist with its current wallpaper (docs/plan.md, Phase 4,
"Look" and "One window"; docs/design/home.png).

Everything it shows comes from the app through `Client`: it redraws from
`overview_changed` (never polls), and after each new overview asks once
for `status` for the schedule (order, interval, time left), which it counts
down locally until the next overview. Buttons send the playlist commands;
their toggles (favourite, hold) show the app's state from the overview,
never the click. A command's error shows in its row, not as a dialog.
Pointing at a row (or at a monitor) highlights the playlist in both: a
faint fill on the row, an accent outline on its monitors.

Image details (dimensions, file size) and thumbnails are read from the
files by `ThumbnailLoader`, off the UI thread.
"""

from __future__ import annotations

import time
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import cast

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QEnterEvent, QPainter, QPaintEvent, QPalette
from PySide6.QtWidgets import (
    QFrame,
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

THUMBNAIL_BOX = QSize(480, 270)  # logical px; one decode serves row and monitor
ROW_IMAGE = QSize(176, 99)
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
    """
    As the config writes durations: 45s, 5m50s (under 10 minutes), 21m,
    1h, 1h5m, 2d, 2d3h.
    """
    whole = max(round(seconds), 0)
    if whole < 60:
        return f"{whole}s"
    if whole < 600:
        minutes, rest = divmod(whole, 60)
        return f"{minutes}m{rest}s" if rest else f"{minutes}m"
    minutes = round(whole / 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours}h" if hours else f"{days}d"


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
    "shuffle, every 30m · next in 12m"; "… · 12m left once resumed"
    (held); "shuffle · manual". `left`: seconds of active time left.
    """
    if details is None:
        return None
    order = details.order.value
    if view.manual or details.interval is None:
        return f"{order} · manual"
    text = f"{order}, every {format_duration(details.interval)}"
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
    """The row's details line: dimensions · file size · order, interval · next."""
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


# -- a row ------------------------------------------------------------------------


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


class PlaylistRow(QWidget):
    """
    One playlist: thumbnail; name, monitors, chip; file name; details; then
    the borderless buttons previous, next | favourite, remove, hold.
    `requested` asks the page to send a command; the row holds no client.
    """

    # pylint: disable=too-many-instance-attributes

    requested = Signal(str, str)  # playlist, command
    hovered = Signal(object)  # this playlist when the pointer enters, None on leave

    def __init__(self, view: PlaylistView, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view = view
        self._highlighted = False
        self._divider = False  # a line above (every row but the first)
        self._details: PlaylistDetails | None = None
        self._thumbnail: Thumbnail | None = None
        self._left: float | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.image = theme.ImageView(parent=self)
        self.image.setFixedSize(ROW_IMAGE)
        self.title = QLabel(self)
        self.title.setFont(theme.scaled_font(self.title.font(), 1.15, bold=True))
        self.link = QLabel(self)
        self.link.setToolTip("Mirrored group: the same image on every monitor")
        self.monitors = ElidedLabel(self)
        self.monitors.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        self.monitors.setMinimumWidth(theme.space(5))
        self.monitors.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        self.monitors.setFont(theme.scaled_font(self.monitors.font(), 0.93))
        self.chip = theme.Chip(self)
        self.file = ElidedLabel(self)
        self.details = theme.muted("", self, wrap=True)
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
        heading.addWidget(self.monitors)
        heading.addWidget(self.chip)
        heading.addStretch(1)

        message = QHBoxLayout()
        message.setSpacing(theme.space())
        message.addWidget(self.message_icon, 0, Qt.AlignmentFlag.AlignTop)
        message.addWidget(self.message, 1)

        text = QVBoxLayout()
        text.setSpacing(theme.space(0.5))
        text.addStretch(1)
        text.addLayout(heading)
        text.addWidget(self.file)
        text.addWidget(self.details)
        text.addLayout(message)
        text.addStretch(1)

        buttons = QHBoxLayout()
        buttons.setSpacing(theme.space(0.5))
        buttons.addWidget(self.prev_button)
        buttons.addWidget(self.next_button)
        separator = QWidget(self)
        separator.setFixedSize(theme.space(1.5) + 1, 20)
        separator_layout = QHBoxLayout(separator)
        separator_layout.setContentsMargins(theme.space(0.75), 0, theme.space(0.75), 0)
        separator_layout.addWidget(theme.Divider(separator, Qt.Orientation.Vertical))
        buttons.addWidget(separator)
        buttons.addWidget(self.fav_button)
        buttons.addWidget(self.remove_button)
        buttons.addWidget(self.hold_button)

        layout = QHBoxLayout(self)
        vertical = theme.space(1.75)
        layout.setContentsMargins(0, vertical + 1, theme.space(), vertical)
        layout.setSpacing(theme.space(2.5))
        layout.addWidget(self.image, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(text, 1)
        layout.addLayout(buttons)

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
        self.link.setVisible(len(view.monitors) > 1)  # a mirrored group
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
        """A note under the details (e.g. a command's error); None hides it."""
        self.message.setText(text or "")
        self.message.setVisible(bool(text))
        self.message_icon.setVisible(bool(text) and error)

    def set_divider(self, divider: bool) -> None:
        """A thin line above this row (every row but the first)."""
        if divider != self._divider:
            self._divider = divider
            self.update()

    def divider(self) -> bool:
        return self._divider

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
        """The small icons in the text (buttons do their own)."""
        ratio = self.devicePixelRatioF()
        muted = self.palette().color(QPalette.ColorRole.PlaceholderText)
        text = self.palette().color(QPalette.ColorRole.WindowText)
        self.link.setPixmap(theme.tinted_pixmap("link", muted, 14, ratio))
        self.message_icon.setPixmap(
            theme.tinted_pixmap("circle-alert", text, 16, ratio)
        )

    # -- painting ----------------------------------------------------------------

    def set_highlighted(self, highlighted: bool) -> None:
        """A faint fill while this playlist is pointed at (here or a monitor)."""
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
        tones = theme.tones()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._divider:
            painter.fillRect(QRectF(0, 0, self.width(), 1), tones.line)
        if self._highlighted:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(tones.hover)
            rect = QRectF(self.rect()).adjusted(0, 1 if self._divider else 0, 0, 0)
            painter.drawRoundedRect(rect, theme.RADIUS, theme.RADIUS)
        painter.end()

    def changeEvent(self, event: QEvent) -> None:  # pylint: disable=invalid-name
        super().changeEvent(event)
        if theme.palette_changed(event):
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


# -- the page ---------------------------------------------------------------------


class _ReplyTo:
    """
    A reply callback for `Client.run` that holds the page weakly: the window
    may close (and be deleted) before the reply arrives.
    """

    def __init__(self, page: HomePage, playlist: str | None) -> None:
        self._page = weakref.ref(page)
        self._playlist = playlist

    def __call__(self, reply: Reply) -> None:
        page = self._page()
        if page is not None and Shiboken.isValid(page):
            page.replied(self._playlist, reply)


class HomePage(QWidget):
    """See the module docstring."""

    # pylint: disable=too-many-instance-attributes

    def __init__(
        self,
        client: Client,
        loader: ThumbnailLoader | None = None,
        clock: Clock = time.monotonic,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._clock = clock
        self._overview: Overview | None = None
        self._details: dict[str, PlaylistDetails] = {}
        self._details_at = 0.0
        self._status_running = False
        self._status_again = False
        self._closed = False
        self.rows: dict[str, PlaylistRow] = {}

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
        self.monitors_heading = theme.heading("Monitors", self)
        self.monitors_view = MonitorsView(self)
        self.monitors_view.set_heights(220, 360, 0.3)
        self.monitors_view.hovered.connect(self._hover)
        self.empty = theme.muted("No playlists in the config yet.", self)
        self._rows_box = QVBoxLayout()
        self._rows_box.setSpacing(0)
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
        panel = theme.Panel(self)
        header = QHBoxLayout()
        header.setSpacing(theme.space(1.5))
        header.addWidget(self.monitors_heading, 1)
        header.addWidget(theme.muted("Hover a playlist to see its monitors", panel))
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(
            theme.space(2.5), theme.space(1.75), theme.space(2.5), theme.space()
        )
        panel_layout.setSpacing(theme.space(1.5))
        panel_layout.addLayout(header)
        panel_layout.addWidget(theme.Divider(panel, on_panel=True))
        panel_layout.addWidget(self.monitors_view)

        content = QWidget(self)
        layout = theme.page_layout(content, bottom=theme.PAGE_MARGIN)
        layout.addWidget(theme.title("Home", content))
        layout.addWidget(self.banner)
        layout.addWidget(panel)
        layout.addSpacing(theme.space())
        playlists = theme.heading("Playlists", content)
        layout.addWidget(playlists)
        layout.addWidget(self.empty)
        layout.addLayout(self._rows_box)
        layout.addStretch(1)
        layout.setSpacing(theme.space(2))

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
        if self._closed:
            return
        self._overview = overview
        self._set_banner(f"Error: {overview.error}" if overview.error else None)
        self.monitors_heading.setText(f"Monitors ({len(overview.monitors)})")
        self.monitors_view.show_overview(overview)
        self._update_rows(overview)
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
        row = self.rows.get(playlist)
        if row is None:
            return
        if isinstance(reply, ErrorReply):
            row.show_message(reply.error.message, error=True)
        elif isinstance(reply, PrevResult) and reply.change is None:
            row.show_message("Nothing earlier in the history")
        elif isinstance(reply, RemoveResult) and reply.change is None:
            row.show_message(
                f"{reply.image.name} is listed for removal; the playlist "
                "moves on once a monitor is on"
            )
        else:
            row.show_message(None)

    def shutdown(self) -> None:
        """The window closes: stop counting, wait for running thumbnail jobs."""
        self._closed = True
        self._countdown.stop()
        self.loader.close()

    # -- rows --------------------------------------------------------------------

    def _update_rows(self, overview: Overview) -> None:
        names = [p.name for p in overview.playlists]
        for name in [n for n in self.rows if n not in names]:
            gone = self.rows.pop(name)
            self._rows_box.removeWidget(gone)
            gone.hide()
            gone.deleteLater()
        for view in overview.playlists:
            row = self.rows.get(view.name)
            if row is None:
                new = PlaylistRow(view, self)
                new.requested.connect(self._send)
                new.hovered.connect(self._hover)
                self.rows[view.name] = new
            else:
                row.show_view(view)
        # In the overview's (the config's) order.
        self.rows = {name: self.rows[name] for name in names}
        for index, row in enumerate(self.rows.values()):
            self._rows_box.removeWidget(row)
            self._rows_box.insertWidget(index, row)
            row.set_divider(index > 0)
        self.empty.setVisible(not names and not overview.error)

    @Slot(object)
    def _hover(self, playlist: str | None) -> None:
        """A row or a monitor is pointed at: highlight its playlist in both."""
        self.monitors_view.set_highlight(playlist)
        for name, row in self.rows.items():
            row.set_highlighted(name == playlist)

    @Slot(str, str)
    def _send(self, playlist: str, command: str) -> None:
        row = self.rows.get(playlist)
        if row is not None:
            row.show_message(None)
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
        for row in self.rows.values():
            if row.view().current == path:  # only where it is still current
                row.set_thumbnail(thumbnail)
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
        for row in self.rows.values():
            view = row.view()
            row.set_details(self._details.get(view.name), self._left(view))
            any_counting |= counting(view, paused) and view.name in self._details
        if any_counting and not self._countdown.isActive():
            self._countdown.start()
        elif not any_counting:
            self._countdown.stop()

    @Slot()
    def refresh_schedule(self) -> None:
        """Redraw the time left (the countdown timer calls it every second)."""
        self._refresh_details()

    def _set_banner(self, text: str | None) -> None:
        self.banner.setText(text or "")
        self.banner.setVisible(bool(text))
