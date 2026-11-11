"""
The settings window's About page: the version (with the git commit when
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
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication, QPalette
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core import paths
from ...core.config import Config
from . import theme

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
        Location("State database", paths.state_db()),
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
    """Version and data locations; `set_locations` fills it."""

    def __init__(
        self,
        version: str,
        open_url: OpenUrl = QDesktopServices.openUrl,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._open_url = open_url
        self.locations: list[Location] = []

        title = QLabel("wallpaper_py", self)
        font = QFont(title.font())
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * 1.3)
        title.setFont(font)
        self.version = QLabel(f"Version {version}", self)
        self.version.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        heading = QLabel("Data locations", self)
        bold = QFont(heading.font())
        bold.setBold(True)
        heading.setFont(bold)
        self._grid_widget = QWidget(self)
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(theme.space(1.5))
        self._grid.setVerticalSpacing(theme.space())
        self._grid.setColumnStretch(1, 1)
        later = QLabel("Library statistics per playlist come in a later version.", self)
        later.setForegroundRole(QPalette.ColorRole.PlaceholderText)

        layout = QVBoxLayout(self)
        margin = theme.space(2)
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(theme.space())
        layout.addWidget(title)
        layout.addWidget(self.version)
        layout.addSpacing(theme.space())
        layout.addWidget(heading)
        layout.addWidget(self._grid_widget)
        layout.addSpacing(theme.space())
        layout.addWidget(later)
        layout.addStretch(1)

    def set_locations(self, locations: Sequence[Location]) -> None:
        if list(locations) == self.locations:
            return
        self.locations = list(locations)
        while (item := self._grid.takeAt(0)) is not None:
            if (widget := item.widget()) is not None:
                widget.deleteLater()
        for row, location in enumerate(self.locations):
            label = QLabel(location.label, self._grid_widget)
            path = QLabel(str(location.path), self._grid_widget)
            path.setWordWrap(True)
            path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            path.setAccessibleName(f"{location.label}: {location.path}")
            open_button = QPushButton("Open folder", self._grid_widget)
            open_button.setAccessibleName(f"Open the folder of: {location.label}")
            open_button.setProperty(_PATH, str(location.path))
            open_button.clicked.connect(self._open_folder)
            copy_button = QPushButton("Copy path", self._grid_widget)
            copy_button.setAccessibleName(f"Copy the path of: {location.label}")
            copy_button.setProperty(_PATH, str(location.path))
            copy_button.clicked.connect(self._copy_path)
            top = Qt.AlignmentFlag.AlignTop
            self._grid.addWidget(label, row, 0, top)
            self._grid.addWidget(path, row, 1, top)
            self._grid.addWidget(open_button, row, 2, top)
            self._grid.addWidget(copy_button, row, 3, top)

    def _sender_path(self) -> Path:
        return Path(str(self.sender().property(_PATH)))

    @Slot()
    def _open_folder(self) -> None:
        folder = existing_folder(self._sender_path())
        self._open_url(QUrl.fromLocalFile(str(folder)))

    @Slot()
    def _copy_path(self) -> None:
        QGuiApplication.clipboard().setText(str(self._sender_path()))
