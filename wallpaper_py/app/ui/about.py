"""
The Settings page's About tab: the version (with the git commit when
run from a checkout), and where everything is kept, each location with
*Open folder* and *Copy path* (todo.md, "Settings / about").

Library stats per playlist come later, with a `stats` command (Phase 7).
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ...core import paths
from ...core.config import Config
from . import theme
from .settings_form import SettingRow

_PATH = "path"  # Qt property on the buttons: the location they act on
OpenUrl = Callable[[QUrl], object]  # QDesktopServices.openUrl


@dataclass(frozen=True)
class Location:
    label: str
    path: Path


def data_locations(config_path: Path | None, config: Config | None) -> list[Location]:
    """
    What the app and the windows keep where. The lists come from the config
    (its defaults when it can't be read); the rest are per-user locations.
    """
    favourites = config.favourites_file if config else None
    to_remove = config.to_remove_file if config else None
    locations = [Location("Config file", config_path)] if config_path else []
    return [
        *locations,
        Location("State database", paths.state_db(config_path or paths.config_file())),
        Location("Favourites", favourites or paths.data_dir() / "favourites.txt"),
        Location("To remove", to_remove or paths.data_dir() / "to-remove.txt"),
        Location("App log", paths.log_file()),
        Location("Windows log", paths.ui_log_file()),
        Location("Rendered wallpapers", paths.render_dir()),
        Location("Thumbnail cache", paths.thumbnail_dir()),
    ]


def version_text() -> str:
    """ "0.3.0 (6038478)": the package's version and, in a checkout, the commit."""
    try:
        version = metadata.version("wallpaper_py")
    except metadata.PackageNotFoundError:
        version = "unknown"
    commit = _git_commit(Path(__file__).parents[3])
    return f"{version} ({commit})" if commit else version


def _git_commit(root: Path) -> str | None:
    """`git describe` of the checkout the code runs from (-dirty if edited)."""
    if not (root / ".git").exists():
        return None
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "describe", "--always", "--dirty", "--abbrev=7"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
            creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def existing_folder(path: Path) -> Path:
    """The folder to open for a location: itself or its parent, else the
    nearest one that exists."""
    folder = path if path.is_dir() or not path.suffix else path.parent
    while not folder.exists() and folder.parent != folder:
        folder = folder.parent
    return folder


class AboutPage(QWidget):
    """Version and data locations, as rows; `set_locations` fills it."""

    def __init__(
        self,
        version: str,
        open_url: OpenUrl = QDesktopServices.openUrl,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._open_url = open_url
        self.locations: list[Location] = []

        self.version = theme.ValueLabel(version, self)
        self.version.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.version.setAccessibleName(f"Version {version}")
        self._rows = QVBoxLayout()
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, theme.space(2), 0, theme.space(2))
        layout.setSpacing(0)
        layout.addWidget(theme.heading("wallpaper_py", self))
        layout.addWidget(SettingRow("Version", "", self.version, parent=self))
        layout.addSpacing(theme.space())
        layout.addWidget(theme.Divider(self))
        data = theme.heading("Data locations", self)
        data.setContentsMargins(0, theme.space(2), 0, theme.space(0.5))
        layout.addWidget(data)
        layout.addLayout(self._rows)
        layout.addSpacing(theme.space())
        layout.addWidget(theme.Divider(self))
        later = theme.muted(
            "Library statistics per playlist come in a later version.", self
        )
        later.setContentsMargins(0, theme.space(2), 0, 0)
        layout.addWidget(later)
        layout.addStretch(1)

    def set_locations(self, locations: Sequence[Location]) -> None:
        if list(locations) == self.locations:
            return
        self.locations = list(locations)
        while (item := self._rows.takeAt(0)) is not None:
            if (widget := item.widget()) is not None:
                widget.hide()  # now: it is deleted later
                widget.deleteLater()
        for location in self.locations:
            open_button = theme.TextButton("Open folder", self)
            open_button.setAccessibleName(f"Open the folder of: {location.label}")
            open_button.setProperty(_PATH, str(location.path))
            open_button.clicked.connect(self._open_folder)
            copy_button = theme.TextButton("Copy path", self)
            copy_button.setAccessibleName(f"Copy the path of: {location.label}")
            copy_button.setProperty(_PATH, str(location.path))
            copy_button.clicked.connect(self._copy_path)
            buttons = QHBoxLayout()
            buttons.setSpacing(theme.space(2))
            buttons.addWidget(open_button)
            buttons.addWidget(copy_button)
            buttons.addStretch(1)
            row = SettingRow(location.label, str(location.path), buttons, parent=self)
            row.description.setWordWrap(True)
            row.description.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            row.description.setAccessibleName(f"{location.label}: {location.path}")
            self._rows.addWidget(row)

    def _sender_path(self) -> Path:
        return Path(str(self.sender().property(_PATH)))

    @Slot()
    def _open_folder(self) -> None:
        folder = existing_folder(self._sender_path())
        self._open_url(QUrl.fromLocalFile(str(folder)))

    @Slot()
    def _copy_path(self) -> None:
        QGuiApplication.clipboard().setText(str(self._sender_path()))
