"""
The settings window's form (settings.py): one `PlaylistEditor` per
playlist, the `Problem` shown under a field, and the texts the window
derives from the settings as edited (monitors, the drawing, why Save is
off). Widgets only report edits; the window owns the settings.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from PySide6.QtCore import QEvent, Qt, Signal, Slot
from PySide6.QtGui import QPainter, QPaintEvent, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QToolButton,
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

ORDERS: tuple[tuple[str, Order], ...] = (
    ("Shuffle: every image once before any repeats", Order.SHUFFLE),
    ("Sorted: natural order, 2 before 10", Order.SORTED),
)
MODES: tuple[tuple[str, ImageMode | None], ...] = (
    ("Fill: cover the screen, cropping", ImageMode.FILL),
    ("Fit: the whole image, letterboxed", ImageMode.FIT),
    ("Stretch: to the screen's shape", ImageMode.STRETCH),
    ("As is: the system's own mode", None),
)
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
        color = self.palette().color(QPalette.ColorRole.WindowText)
        ratio = self.devicePixelRatioF()
        self._icon.setPixmap(theme.tinted_pixmap("circle-alert", color, 16, ratio))

    def changeEvent(self, event: QEvent) -> None:  # pylint: disable=invalid-name
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self._tint()


def stacked(parent: QWidget, *parts: QWidget | QHBoxLayout) -> QWidget:
    """Widgets (or a row) above each other: a field and its problem."""
    box = QWidget(parent)
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(theme.space(0.5))
    for part in parts:
        if isinstance(part, QHBoxLayout):
            layout.addLayout(part)
        else:
            layout.addWidget(part)
    return box


def row(*widgets: QWidget, stretch: bool = False) -> QHBoxLayout:
    """Widgets side by side; line edits take the room."""
    layout = QHBoxLayout()
    layout.setSpacing(theme.space())
    for widget in widgets:
        layout.addWidget(widget, 1 if isinstance(widget, QLineEdit) else 0)
    if stretch:
        layout.addStretch(1)
    return layout


def hint(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setWordWrap(True)
    label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
    return label


def select(combo: QComboBox, value: object) -> None:
    index = combo.findData(value)
    combo.setCurrentIndex(max(index, 0))


# -- one playlist ------------------------------------------------------------------


class PlaylistEditor(QFrame):
    """
    One playlist's fields. `edited` on every change; the window collects
    the values (`settings`) and shows problems (`show_problems`). Monitors
    are set by the window (`set_monitors`): they change through menus.
    """

    # pylint: disable=too-many-instance-attributes

    edited = Signal()
    delete_requested = Signal(int)  # uid
    browse_requested = Signal(int)  # uid

    def __init__(self, playlist: PlaylistSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self.uid = playlist.uid
        self.origin = playlist.origin
        self._monitors = playlist.monitors

        self.name_edit = QLineEdit(playlist.name, self)
        self.name_edit.setAccessibleName("Playlist name")
        self.delete_button = theme.IconButton("trash-2", "Delete this playlist", self)
        self.directory_edit = QLineEdit(playlist.directory, self)
        self.directory_edit.setAccessibleName("Folder")
        self.directory_edit.setPlaceholderText("The folder with its images")
        self.browse_button = QPushButton("Browse…", self)
        self.order_combo = QComboBox(self)
        for text, order in ORDERS:
            self.order_combo.addItem(text, order)
        select(self.order_combo, playlist.order)
        self.interval_edit = QLineEdit(self)
        self.interval_edit.setAccessibleName("Interval")
        self.interval_edit.setPlaceholderText("e.g. 30m, 1h30m, 1d")
        self.interval_edit.setText(
            NEW_INTERVAL if playlist.manual else playlist.interval
        )
        self.manual_check = QCheckBox("Manual: only next / prev / pick", self)
        self.manual_check.setChecked(playlist.manual)
        self.interval_edit.setEnabled(not playlist.manual)
        self.mode_combo = QComboBox(self)
        for text, mode in MODES:
            self.mode_combo.addItem(text, mode)
        select(self.mode_combo, playlist.mode)
        self.monitors_label = QLabel(self)
        self.monitors_label.setWordWrap(True)
        self.monitors_button = QToolButton(self)
        self.monitors_button.setText("Choose…")
        self.monitors_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.monitors_menu = QMenu(self.monitors_button)
        self.monitors_menu.setProperty(UID, self.uid)
        self.monitors_button.setMenu(self.monitors_menu)
        self.problems = {
            key: Problem(self) for key in (NAME, DIRECTORY, INTERVAL, MONITORS)
        }
        self._build()

        self.name_edit.textChanged.connect(self._changed)
        self.directory_edit.textChanged.connect(self._changed)
        self.interval_edit.textChanged.connect(self._changed)
        self.order_combo.currentIndexChanged.connect(self._changed)
        self.mode_combo.currentIndexChanged.connect(self._changed)
        self.manual_check.toggled.connect(self._manual_toggled)
        self.delete_button.clicked.connect(self._delete)
        self.browse_button.clicked.connect(self._browse)

    def _build(self) -> None:
        form = QFormLayout()
        form.setHorizontalSpacing(theme.space(1.5))
        form.setVerticalSpacing(theme.space())
        form.addRow("Name", stacked(self, self.name_edit, self.problems[NAME]))
        folder = row(self.directory_edit, self.browse_button)
        form.addRow("Folder", stacked(self, folder, self.problems[DIRECTORY]))
        form.addRow("Order", self.order_combo)
        interval = row(self.interval_edit, self.manual_check)
        form.addRow("Interval", stacked(self, interval, self.problems[INTERVAL]))
        form.addRow("Mode", self.mode_combo)
        monitors = row(self.monitors_label, self.monitors_button)
        monitors.setStretch(0, 1)
        form.addRow("Monitors", stacked(self, monitors, self.problems[MONITORS]))

        top = QHBoxLayout()
        top.addStretch(1)
        top.addWidget(self.delete_button)
        layout = QVBoxLayout(self)
        margin = theme.space(1.5)
        layout.setContentsMargins(margin, theme.space(), margin, margin)
        layout.setSpacing(0)
        layout.addLayout(top)
        layout.addLayout(form)

    # -- values ------------------------------------------------------------------

    def settings(self) -> PlaylistSettings:
        manual = self.manual_check.isChecked()
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
        self.monitors_button.setAccessibleName(f"Monitors: {text}")

    def show_problems(self, problems: Mapping[str, str]) -> None:
        for key, problem in self.problems.items():
            problem.set_problem(problems.get(key))

    # -- slots -------------------------------------------------------------------

    @Slot()
    def _changed(self) -> None:
        self.edited.emit()

    @Slot(bool)
    def _manual_toggled(self, manual: bool) -> None:
        self.interval_edit.setEnabled(not manual)
        self.edited.emit()

    @Slot()
    def _delete(self) -> None:
        self.delete_requested.emit(self.uid)

    @Slot()
    def _browse(self) -> None:
        self.browse_requested.emit(self.uid)

    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self.palette().color(QPalette.ColorRole.Mid))
        painter.setBrush(self.palette().color(QPalette.ColorRole.Base))
        rect = self.rect().toRectF().adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(rect, theme.RADIUS, theme.RADIUS)
        painter.end()


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
