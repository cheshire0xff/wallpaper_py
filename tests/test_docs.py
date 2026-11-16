"""Docs that name code must keep up with it."""

import re
from pathlib import Path
from types import ModuleType

from wallpaper_py import backends, cli, commands
from wallpaper_py.app import ipc, processes, runtime
from wallpaper_py.app.ipc import CommandServer
from wallpaper_py.app.screens import ScreenWatcher
from wallpaper_py.app.tray import Tray
from wallpaper_py.app.ui import launcher
from wallpaper_py.app.ui.arrangement import MonitorsView
from wallpaper_py.app.client import Client, SocketClient
from wallpaper_py.app.ui.grid import FolderScan, ImageGrid
from wallpaper_py.app.ui.home import HomePage, PlaylistRow
from wallpaper_py.app.ui.jobs import Jobs
from wallpaper_py.app.ui.launcher import UiServer, WindowOpener
from wallpaper_py.app.ui.library import LibraryPage
from wallpaper_py.app.ui.library_overview import OverviewTab
from wallpaper_py.app.ui.settings import SettingsPage
from wallpaper_py.app.ui.settings_form import PlaylistEditor
from wallpaper_py.app.ui.settings_look import LookEditor
from wallpaper_py.app.ui.thumbnails import ThumbnailLoader
from wallpaper_py.app.ui.tiles import Pregenerator, TileLoader
from wallpaper_py.app.ui.window import MainWindow, Sidebar
from wallpaper_py.app.worker import LocalClient, ServiceThread, ServiceWorker
from wallpaper_py.core import paths
from wallpaper_py.core.thumbnail_cache import ThumbnailCache

WIRING = Path(__file__).parents[1] / "docs" / "app-wiring.md"

# Every "Owner.member" in the doc (in text or code blocks) whose owner is one
# of these must exist; other owners (Qt classes) aren't checked.
OWNERS: dict[str, type | ModuleType] = {
    "backends": backends,
    "cli": cli,
    "Client": Client,
    "CommandServer": CommandServer,
    "FolderScan": FolderScan,
    "HomePage": HomePage,
    "ImageGrid": ImageGrid,
    "Jobs": Jobs,
    "LibraryPage": LibraryPage,
    "LocalClient": LocalClient,
    "LookEditor": LookEditor,
    "MonitorsView": MonitorsView,
    "MainWindow": MainWindow,
    "OverviewTab": OverviewTab,
    "PlaylistRow": PlaylistRow,
    "PlaylistEditor": PlaylistEditor,
    "Pregenerator": Pregenerator,
    "ScreenWatcher": ScreenWatcher,
    "ServiceThread": ServiceThread,
    "ServiceWorker": ServiceWorker,
    "SettingsPage": SettingsPage,
    "Sidebar": Sidebar,
    "SocketClient": SocketClient,
    "ThumbnailCache": ThumbnailCache,
    "ThumbnailLoader": ThumbnailLoader,
    "TileLoader": TileLoader,
    "Tray": Tray,
    "UiServer": UiServer,
    "WindowOpener": WindowOpener,
    "commands": commands,
    "ipc": ipc,
    "launcher": launcher,
    "paths": paths,
    "processes": processes,
    "runtime": runtime,
}
# (Not file names: "commands.py" is no reference.)
REFERENCE = re.compile(rf"\b({'|'.join(OWNERS)})\.(?!py\b)(\w+)")


def test_app_wiring_names_exist() -> None:
    references = set(REFERENCE.findall(WIRING.read_text(encoding="utf-8")))
    missing = sorted(f"{o}.{m}" for o, m in references if not hasattr(OWNERS[o], m))
    assert not missing, f"{WIRING.name} names code that no longer exists: {missing}"
    assert len(references) >= 30, "the doc should reference the code it describes"


def test_architecture_names_exist() -> None:
    doc = WIRING.with_name("architecture.md")
    references = set(REFERENCE.findall(doc.read_text(encoding="utf-8")))
    missing = sorted(f"{o}.{m}" for o, m in references if not hasattr(OWNERS[o], m))
    assert not missing, f"{doc.name} names code that no longer exists: {missing}"


def test_mermaid_lines_have_no_semicolon() -> None:
    # In a sequence diagram ";" ends the statement: the rest of the line
    # fails to parse, and the diagram doesn't render at all.
    found = []
    for doc in sorted(WIRING.parent.rglob("*.md")):
        inside = False
        for number, line in enumerate(doc.read_text(encoding="utf-8").splitlines()):
            if line.startswith("```"):
                inside = line.startswith("```mermaid")
            elif inside and ";" in line:
                found.append(f"{doc.name}:{number + 1}: {line.strip()}")
    assert not found, "';' in a diagram (use ',' instead):\n" + "\n".join(found)


def test_agents_notes_names_exist() -> None:
    notes = Path(__file__).parents[1] / "AGENTS.md"
    references = set(REFERENCE.findall(notes.read_text(encoding="utf-8")))
    missing = sorted(f"{o}.{m}" for o, m in references if not hasattr(OWNERS[o], m))
    assert not missing, f"{notes.name} names code that no longer exists: {missing}"
    assert references, "AGENTS.md should name code the docs test can check"
