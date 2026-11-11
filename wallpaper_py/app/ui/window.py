"""
A placeholder for the layout and library windows: it shows the live
`Overview` and the latest change, which proves the UI process's event flow
end to end. The real windows replace it (Phase 4 / 5).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QFontDatabase
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ...core.service import Change
from ...protocol import MonitorView, Overview
from .client import Client

TITLES = {"layout": "Layout", "library": "Library"}


def describe_overview(overview: Overview | None) -> str:
    """The overview as plain text, one block per playlist."""
    if overview is None:
        return "Connecting to wallpaper_py..."
    if overview.error:
        return f"Error: {overview.error}"
    lines: list[str] = []
    for playlist in overview.playlists:
        monitors = " + ".join(_monitor(m) for m in playlist.monitors)
        lines.append(f"{playlist.name} · {monitors}")
        current = playlist.current.name if playlist.current else "-"
        star = " ★" if playlist.favourite else ""
        lines.append(f"    {current}{star}")
    lines.append(f"Paused: {overview.paused}" if overview.paused else "Rotating")
    return "\n".join(lines)


def _monitor(monitor: MonitorView) -> str:
    if monitor.attached is None:
        return f"{monitor.name} (not found)"
    return monitor.name if monitor.attached else f"{monitor.name} (off)"


class Window(QWidget):
    """A top-level window of the UI process; `closed` once it has closed."""

    closed = Signal()

    def closeEvent(self, event: QCloseEvent) -> None:  # pylint: disable=invalid-name
        super().closeEvent(event)
        if event.isAccepted():  # a window may refuse (e.g. unsaved changes)
            self.closed.emit()


class PlaceholderWindow(Window):
    def __init__(self, kind: str, client: Client) -> None:
        super().__init__()
        self.setWindowTitle(f"wallpaper_py · {TITLES.get(kind, kind)}")
        self.resize(480, 320)

        heading = QLabel(f"{TITLES.get(kind, kind)}: coming soon. Live state:")
        self.overview_label = QLabel()
        self.overview_label.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        self.overview_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.change_label = QLabel("")
        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(self.overview_label, 1)
        layout.addWidget(self.change_label)

        self.show_overview(client.overview)
        client.overview_changed.connect(self.show_overview)
        client.changed.connect(self.show_change)
        client.failed.connect(self.show_error)

    @Slot(object)
    def show_overview(self, overview: Overview | None) -> None:
        self.overview_label.setText(describe_overview(overview))

    @Slot(object)
    def show_change(self, change: Change) -> None:
        self.change_label.setText(f"Last change: {change.describe()}")

    @Slot(str)
    def show_error(self, message: str) -> None:
        self.overview_label.setText(f"Error: {message}")
