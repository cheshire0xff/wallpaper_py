"""
The Settings page's form (settings.py), as rows without boxes
(docs/design/settings.png): each `SettingRow` has its name and a muted
one-line description on the left and its control on the right, all
controls in one column; fields are borderless and filled.

One `PlaylistEditor` per playlist: open, it shows its heading (*Delete
playlist*) and its rows; closed, a clickable summary ("ZeroMOD · sorted ·
every 10m · fill") that asks to open it. Also the `Problem` shown under a
field, and the texts the page derives from the settings as edited
(monitors, the summary, the drawing, what changed, why Save is off).
Widgets only report edits; the page owns the settings.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, Signal, Slot
from PySide6.QtGui import QPainter, QPaintEvent, QPalette
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core.config import MANUAL, Order
from ...core.config_edit import (
    DIRECTORY,
    FAVOURITES,
    IDLE,
    INTERVAL,
    MONITORS,
    NAME,
    NEW_INTERVAL,
    TO_REMOVE,
    Field,
    PlaylistSettings,
    Settings,
    resolve,
)
from ...core.model import ImageMode
from ...protocol import DesktopMonitor, MonitorView, Overview, PlaylistView
from . import theme

UID = "uid"  # Qt property: the playlist a menu is about
EVERY, MANUALLY = "every", MANUAL  # the Changes row's choices

ORDERS: tuple[tuple[str, Order, str], ...] = (  # label, value, description
    ("Shuffle", Order.SHUFFLE, "Shuffle shows every image once before any repeats"),
    ("Sorted", Order.SORTED, "Sorted goes in natural order: 2 before 10"),
)
MODES: tuple[tuple[str, ImageMode | None, str], ...] = (  # label, value, tooltip
    ("Fill", ImageMode.FILL, "Cover the screen, cropping"),
    ("Fit", ImageMode.FIT, "The whole image, letterboxed"),
    ("Stretch", ImageMode.STRETCH, "To the screen's shape"),
    ("As is", None, "The system's own mode"),
)
CHANGES = (("Every", EVERY), ("Manually", MANUALLY))
CHANGES_HELP = {
    EVERY: "Active time: pauses while locked, idle or asleep",
    MANUALLY: "Only next, previous or pick change it",
}
FIELD_NAMES = {
    NAME: "name",
    DIRECTORY: "folder",
    INTERVAL: "interval",
    MONITORS: "monitors",
    IDLE: "idle pause",
    FAVOURITES: "favourites file",
    TO_REMOVE: "to-remove file",
}


# -- small widgets -------------------------------------------------------------------


class Problem(QWidget):
    """A problem with a field, under it: an alert icon and the text."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.message: str | None = None
        self._icon = QLabel(self)
        self._text = QLabel(self)
        self._text.setWordWrap(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.space(0.5))
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self._text, 1)
        self._tint()
        self.setVisible(False)

    def set_problem(self, message: str | None) -> None:
        if message != self.message:
            self.message = message
            self._text.setText(message or "")
            self.setVisible(bool(message))
            self.setAccessibleDescription(message or "")

    def _tint(self) -> None:
        color = theme.tones().error
        palette = QPalette()  # only this role; the rest is inherited
        palette.setColor(QPalette.ColorRole.WindowText, color)
        self._text.setPalette(palette)
        ratio = self.devicePixelRatioF()
        self._icon.setPixmap(theme.tinted_pixmap("circle-alert", color, 16, ratio))

    def changeEvent(self, event: QEvent) -> None:  # pylint: disable=invalid-name
        super().changeEvent(event)
        if theme.palette_changed(event):
            self._tint()


def borderless(field: QLineEdit | QComboBox) -> QLineEdit | QComboBox:
    """A field as the design has it: no frame, the filled Base / Button tone."""
    field.setFrame(False)
    field.setFixedHeight(theme.FIELD_HEIGHT)
    if isinstance(field, QLineEdit):
        field.setTextMargins(theme.space(0.5), 0, theme.space(0.5), 0)
    return field


def line_edit(text: str, name: str, parent: QWidget) -> QLineEdit:
    edit = QLineEdit(text, parent)
    edit.setAccessibleName(name)
    borderless(edit)
    return edit


def combo(parent: QWidget, name: str) -> QComboBox:
    box = QComboBox(parent)
    box.setAccessibleName(name)
    borderless(box)
    return box


def hint(text: str, parent: QWidget) -> QLabel:
    return theme.muted(text, parent, wrap=True)


def select(box: QComboBox, value: object) -> None:
    index = box.findData(value)
    box.setCurrentIndex(max(index, 0))


class SettingRow(QWidget):
    """
    One setting: its name and a muted one-line description on the left; its
    control (a widget or a row of them) in the control column on the right,
    with the field's `Problem` under it.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        name: str,
        description: str,
        control: QWidget | QHBoxLayout,
        problem: Problem | None = None,
        *,
        parent: QWidget | None = None,
        indent: int = 0,
    ) -> None:
        super().__init__(parent)
        self.label = QLabel(name, self)
        self.description = theme.muted(description, self)
        self.description.setVisible(bool(description))
        labels = QVBoxLayout()
        labels.setSpacing(2)
        labels.addWidget(self.label)
        labels.addWidget(self.description)

        column = QWidget(self)
        column.setFixedWidth(theme.CONTROL_WIDTH)
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(theme.space(0.5))
        if isinstance(control, QHBoxLayout):
            column_layout.addLayout(control)
        else:
            column_layout.addWidget(control)
        if problem is not None:
            column_layout.addWidget(problem)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(indent, theme.space(1.25), 0, theme.space(1.25))
        layout.setSpacing(theme.space(3))
        layout.addLayout(labels, 1)
        layout.addWidget(column)
        self.setMinimumHeight(theme.space(7))

    def set_description(self, text: str) -> None:
        self.description.setText(text)
        self.description.setVisible(bool(text))


def control_row(*widgets: QWidget) -> QHBoxLayout:
    """Widgets side by side in the control column; fields take the room."""
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(theme.space(1.5))
    for widget in widgets:
        stretch = 1 if isinstance(widget, (QLineEdit, QComboBox, QLabel)) else 0
        layout.addWidget(widget, stretch)
    return layout


class PlaylistSummary(theme.PaintedButton):
    """A closed playlist: its name, a muted summary, a chevron; a click opens it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self.summary = ""
        self.problem = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(theme.space(7))

    def set_summary(self, name: str, summary: str, problem: bool) -> None:
        self.setText(name)
        self.summary = summary
        self.problem = problem
        note = " (has a problem)" if problem else ""
        self.setAccessibleName(f"{name}: {summary}{note}. Open")
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        tones = theme.tones()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        if self.underMouse() or self.hasFocus():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(tones.hover)
            painter.drawRoundedRect(rect, theme.RADIUS_SMALL, theme.RADIUS_SMALL)
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        painter.setFont(theme.scaled_font(self.font(), 1.15, bold=True))
        painter.setPen(tones.text)
        name = painter.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, int(rect.width() / 3)
        )
        painter.drawText(rect, flags, name)
        left = painter.fontMetrics().horizontalAdvance(name) + theme.space(2)
        right = self._paint_icons(painter, tones)
        painter.setFont(theme.scaled_font(self.font(), 0.93))
        painter.setPen(tones.muted)
        summary = QRectF(left, 0, max(right - left, 0), rect.height())
        text = painter.fontMetrics().elidedText(
            self.summary, Qt.TextElideMode.ElideRight, int(summary.width())
        )
        painter.drawText(summary, flags, text)
        painter.end()

    def _paint_icons(self, painter: QPainter, tones: theme.Tones) -> float:
        """The chevron (and the alert) at the right; returns where they start."""
        size, ratio = theme.SMALL_ICON, self.devicePixelRatioF()
        middle = round((self.height() - size) / 2)
        x = round(self.width() - size - theme.space(0.5))
        chevron = theme.tinted_pixmap("chevron-down", tones.text, size, ratio)
        painter.drawPixmap(QPoint(x, middle), chevron)
        if self.problem:
            x -= size + theme.space()
            alert = theme.tinted_pixmap("circle-alert", tones.error, size, ratio)
            painter.drawPixmap(QPoint(x, middle), alert)
        return x - theme.space()


# -- one playlist ------------------------------------------------------------------


class PlaylistEditor(QWidget):
    """
    One playlist's fields, under a thin divider. `edited` on every change;
    the page collects the values (`settings`) and shows problems
    (`show_problems`) and the summary (`set_summary`). Monitors are set by
    the page (`set_monitors`): they change through menus. Closed, only the
    summary shows; `open_requested` asks the page to open it.
    """

    # pylint: disable=too-many-instance-attributes

    edited = Signal()
    delete_requested = Signal(int)  # uid
    browse_requested = Signal(int)  # uid
    open_requested = Signal(int)  # uid

    def __init__(self, playlist: PlaylistSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self.uid = playlist.uid
        self.origin = playlist.origin
        self._monitors = playlist.monitors
        self._expanded = True

        self.summary = PlaylistSummary(self)
        self.heading = theme.heading(playlist.name, self)
        self.delete_button = theme.TextButton("Delete playlist", self)
        self.name_edit = line_edit(playlist.name, "Playlist name", self)
        self.directory_edit = line_edit(playlist.directory, "Folder", self)
        self.directory_edit.setPlaceholderText("The folder with its images")
        self.directory_edit.setCursorPosition(0)  # the start of the path shows
        self.browse_button = theme.TextButton("Browse…", self)
        self.order_combo = combo(self, "Order")
        for text, order, description in ORDERS:
            self.order_combo.addItem(text, order)
            self.order_combo.setItemData(
                self.order_combo.count() - 1, description, Qt.ItemDataRole.ToolTipRole
            )
        select(self.order_combo, playlist.order)
        self.changes_combo = combo(self, "Changes")
        for text, value in CHANGES:
            self.changes_combo.addItem(text, value)
        select(self.changes_combo, MANUALLY if playlist.manual else EVERY)
        self.interval_edit = line_edit(
            NEW_INTERVAL if playlist.manual else playlist.interval,
            "Interval, in active time",
            self,
        )
        self.interval_edit.setPlaceholderText("e.g. 30m")
        self.interval_edit.setFixedWidth(theme.space(14))
        self.interval_edit.setVisible(not playlist.manual)
        self.mode_combo = combo(self, "Mode")
        for text, mode, description in MODES:
            self.mode_combo.addItem(text, mode)
            self.mode_combo.setItemData(
                self.mode_combo.count() - 1, description, Qt.ItemDataRole.ToolTipRole
            )
        select(self.mode_combo, playlist.mode)
        self.monitors_label = QLabel(self)
        self.monitors_label.setWordWrap(True)
        self.monitors_button = theme.TextButton("Choose…", self)
        self.monitors_menu = QMenu(self)
        self.monitors_menu.setProperty(UID, self.uid)
        self.problems = {
            key: Problem(self) for key in (NAME, DIRECTORY, INTERVAL, MONITORS)
        }
        self._build()

        self.name_edit.textChanged.connect(self._name_changed)
        self.directory_edit.textChanged.connect(self._changed)
        self.interval_edit.textChanged.connect(self._changed)
        self.order_combo.currentIndexChanged.connect(self._order_changed)
        self.mode_combo.currentIndexChanged.connect(self._changed)
        self.changes_combo.currentIndexChanged.connect(self._changes_changed)
        self.delete_button.clicked.connect(self._delete)
        self.browse_button.clicked.connect(self._browse)
        self.monitors_button.clicked.connect(self._show_monitors_menu)
        self.summary.clicked.connect(self._open)

    def _build(self) -> None:
        indent = theme.space(3)
        self.order_row = SettingRow(
            "Order", "", self.order_combo, parent=self, indent=indent
        )
        self.changes_row = SettingRow(
            "Changes",
            "",
            control_row(self.changes_combo, self.interval_edit),
            self.problems[INTERVAL],
            parent=self,
            indent=indent,
        )
        rows = [
            SettingRow(
                "Name",
                "",
                self.name_edit,
                self.problems[NAME],
                parent=self,
                indent=indent,
            ),
            SettingRow(
                "Folder",
                "Scanned with its subfolders",
                control_row(self.directory_edit, self.browse_button),
                self.problems[DIRECTORY],
                parent=self,
                indent=indent,
            ),
            self.order_row,
            self.changes_row,
            SettingRow(
                "Mode",
                "How an image fits its monitor",
                self.mode_combo,
                parent=self,
                indent=indent,
            ),
            SettingRow(
                "Monitors",
                "Two or more show the same image (mirrored)",
                control_row(self.monitors_label, self.monitors_button),
                self.problems[MONITORS],
                parent=self,
                indent=indent,
            ),
        ]
        self._describe()

        top = QHBoxLayout()
        top.setContentsMargins(0, theme.space(2), 0, theme.space(0.5))
        top.addWidget(self.heading, 1)
        top.addWidget(self.delete_button)
        self.details = QWidget(self)
        details = QVBoxLayout(self.details)
        details.setContentsMargins(0, 0, 0, theme.space())
        details.setSpacing(0)
        details.addLayout(top)
        for row in rows:
            details.addWidget(row)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(theme.Divider(self))
        layout.addWidget(self.summary)
        layout.addWidget(self.details)
        self.summary.setVisible(False)

    # -- values ------------------------------------------------------------------

    def settings(self) -> PlaylistSettings:
        manual = self.changes_combo.currentData() == MANUALLY
        return PlaylistSettings(
            uid=self.uid,
            origin=self.origin,
            name=self.name_edit.text(),
            directory=self.directory_edit.text().strip(),
            monitors=self._monitors,
            interval=MANUAL if manual else self.interval_edit.text().strip(),
            order=self.order_combo.currentData(),
            mode=self.mode_combo.currentData(),
        )

    def set_monitors(self, selectors: tuple[str, ...], text: str) -> None:
        self._monitors = selectors
        self.monitors_label.setText(text)
        self.monitors_button.setAccessibleName(f"Choose the monitors: {text}")

    def show_problems(self, problems: Mapping[str, str]) -> None:
        for key, problem in self.problems.items():
            problem.set_problem(problems.get(key))

    def set_summary(self, summary: str, problem: bool) -> None:
        self.summary.set_summary(self.name_edit.text() or "(no name)", summary, problem)

    def set_expanded(self, expanded: bool) -> None:
        """Open (the heading and the rows) or closed (the summary row)."""
        self._expanded = expanded
        self.details.setVisible(expanded)
        self.summary.setVisible(not expanded)

    def expanded(self) -> bool:
        return self._expanded

    def manual(self) -> bool:
        return bool(self.changes_combo.currentData() == MANUALLY)

    def _describe(self) -> None:
        order = self.order_combo.currentIndex()
        self.order_row.set_description(ORDERS[max(order, 0)][2])
        self.changes_row.set_description(
            CHANGES_HELP[MANUALLY if self.manual() else EVERY]
        )

    # -- slots -------------------------------------------------------------------

    @Slot()
    def _changed(self) -> None:
        self.edited.emit()

    @Slot(str)
    def _name_changed(self, name: str) -> None:
        self.heading.setText(name or "(no name)")
        self.edited.emit()

    @Slot(int)
    def _order_changed(self, index: int) -> None:
        del index
        self._describe()
        self.edited.emit()

    @Slot(int)
    def _changes_changed(self, index: int) -> None:
        del index
        self.interval_edit.setVisible(not self.manual())
        self._describe()
        self.edited.emit()

    @Slot()
    def _delete(self) -> None:
        self.delete_requested.emit(self.uid)

    @Slot()
    def _browse(self) -> None:
        self.browse_requested.emit(self.uid)

    @Slot()
    def _open(self) -> None:
        self.open_requested.emit(self.uid)

    @Slot()
    def _show_monitors_menu(self) -> None:
        button = self.monitors_button
        self.monitors_menu.popup(button.mapToGlobal(QPoint(0, button.height())))


# -- texts ---------------------------------------------------------------------------


def monitors_text(selectors: Sequence[str], listed: Sequence[DesktopMonitor]) -> str:
    """ "AW3225QF + ZeroMOD (off) + TV (not connected)", or "None"."""
    parts = []
    for selector in selectors:
        monitor = resolve(selector, listed)
        if monitor is None:
            parts.append(f"{selector} (not connected)")
        elif monitor.geometry is None:
            parts.append(f"{monitor.name} (off)")
        else:
            parts.append(monitor.name)
    return " + ".join(parts) or "None"


def summary_text(playlist: PlaylistSettings, listed: Sequence[DesktopMonitor]) -> str:
    """A closed playlist's row: "ZeroMOD · sorted · every 10m · fill"."""
    monitors = monitors_text(playlist.monitors, listed)
    if not playlist.monitors:
        monitors = "no monitor"
    if playlist.manual:
        changes = "manually"
    else:
        changes = f"every {playlist.interval}" if playlist.interval else "no interval"
    mode = next(label for label, value, _ in MODES if value == playlist.mode)
    return " · ".join((monitors, playlist.order.value, changes, mode.lower()))


_PLAYLIST_FIELDS = (
    ("name", "name"),
    ("folder", "directory"),
    ("monitors", "monitors"),
    ("order", "order"),
    ("interval", "interval"),
    ("mode", "mode"),
)
_GENERAL_FIELDS = (
    ("pausing while locked", "pause_when_locked"),
    ("the idle pause", "pause_when_idle"),
    ("the favourites file", "favourites_file"),
    ("the to-remove file", "to_remove_file"),
)
MAX_CHANGES = 3  # named in the status line; the rest are counted


def change_summary(base: Settings, edited: Settings) -> str:
    """What differs, for the status line: "interval of konachan, new playlist strip"."""
    parts: list[str] = []
    before = {p.uid: p for p in base.playlists}
    for playlist in edited.playlists:
        old = before.pop(playlist.uid, None)
        if old is None:
            parts.append(f"new playlist {playlist.name or '(no name)'}")
            continue
        fields = [
            label
            for label, key in _PLAYLIST_FIELDS
            if getattr(old, key) != getattr(playlist, key)
        ]
        if fields:
            parts.append(f"{', '.join(fields)} of {old.name}")
    parts += [f"deleted {p.name}" for p in before.values()]
    parts += [
        label
        for label, key in _GENERAL_FIELDS
        if getattr(base, key) != getattr(edited, key)
    ]
    if len(parts) > MAX_CHANGES:
        more = len(parts) - MAX_CHANGES
        parts = [*parts[:MAX_CHANGES], f"{more} more"]
    return ", ".join(parts)


def drawing(settings: Settings, listed: Sequence[DesktopMonitor]) -> Overview:
    """What MonitorsView draws: the monitors with the playlists as edited."""
    playlists = []
    for playlist in settings.playlists:
        members = []
        for selector in playlist.monitors:
            monitor = resolve(selector, listed)
            members.append(
                MonitorView(
                    selector=selector,
                    name=monitor.name if monitor else selector,
                    attached=(monitor.geometry is not None) if monitor else None,
                    id=monitor.id if monitor else None,
                )
            )
        playlists.append(
            PlaylistView(
                name=playlist.name or "(no name)",
                monitors=tuple(members),
                current=None,
                favourite=False,
            )
        )
    return Overview(playlists=tuple(playlists), monitors=tuple(listed))


def unlisted_text(settings: Settings, listed: Sequence[DesktopMonitor]) -> str:
    """The monitors in the config that the desktop doesn't list now."""
    missing = [
        f"{selector} ({playlist.name})"
        for playlist in settings.playlists
        for selector in playlist.monitors
        if resolve(selector, listed) is None
    ]
    if not missing:
        return ""
    return (
        f"In the config but not connected now: {', '.join(missing)}. They stay "
        f"until you remove them (a playlist's Monitors menu)."
    )


def problem_summary(problems: Mapping[Field, str], settings: Settings) -> str:
    """The first problem, for the line beside Save: "konachan, folder: …"."""
    (uid, key), message = next(iter(problems.items()))
    playlist = settings.playlist(uid) if uid is not None else None
    if playlist is not None:
        where = f"{playlist.name or 'a playlist'}, {FIELD_NAMES.get(key, key)}: "
    elif key in FIELD_NAMES:
        where = f"{FIELD_NAMES[key].capitalize()}: "
    else:
        where = ""
    more = len(problems) - 1
    tail = f" (and {more} more)" if more else ""
    return f"Can't save yet. {where}{message}{tail}"
