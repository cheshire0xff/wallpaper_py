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
from wallpaper_py.app.ui.client import Client, SocketClient
from wallpaper_py.app.ui.layout import LayoutWindow, PlaylistCard
from wallpaper_py.app.ui.launcher import UiServer, WindowOpener
from wallpaper_py.app.ui.process import Windows
from wallpaper_py.app.ui.thumbnails import ThumbnailLoader
from wallpaper_py.app.ui.window import PlaceholderWindow
from wallpaper_py.app.worker import ServiceHost, ServiceWorker
from wallpaper_py.core import paths

WIRING = Path(__file__).parents[1] / "docs" / "app-wiring.md"

# Every "Owner.member" in the doc (in text or code blocks) whose owner is one
# of these must exist; other owners (Qt classes) aren't checked.
OWNERS: dict[str, type | ModuleType] = {
    "backends": backends,
    "cli": cli,
    "Client": Client,
    "CommandServer": CommandServer,
    "LayoutWindow": LayoutWindow,
    "MonitorsView": MonitorsView,
    "PlaceholderWindow": PlaceholderWindow,
    "PlaylistCard": PlaylistCard,
    "ScreenWatcher": ScreenWatcher,
    "ServiceHost": ServiceHost,
    "ServiceWorker": ServiceWorker,
    "SocketClient": SocketClient,
    "ThumbnailLoader": ThumbnailLoader,
    "Tray": Tray,
    "UiServer": UiServer,
    "WindowOpener": WindowOpener,
    "Windows": Windows,
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


def test_agents_notes_names_exist() -> None:
    notes = Path(__file__).parents[1] / "AGENTS.md"
    references = set(REFERENCE.findall(notes.read_text(encoding="utf-8")))
    missing = sorted(f"{o}.{m}" for o, m in references if not hasattr(OWNERS[o], m))
    assert not missing, f"{notes.name} names code that no longer exists: {missing}"
    assert references, "AGENTS.md should name code the docs test can check"
