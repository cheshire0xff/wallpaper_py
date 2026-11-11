"""Docs that name code must keep up with it."""

import re
from pathlib import Path
from types import ModuleType

from wallpaper_py import backends, commands
from wallpaper_py.app import ipc, runtime
from wallpaper_py.app.ipc import CommandServer
from wallpaper_py.app.screens import ScreenWatcher
from wallpaper_py.app.tray import Tray
from wallpaper_py.app.worker import ServiceHost, ServiceWorker

WIRING = Path(__file__).parents[1] / "docs" / "app-wiring.md"

# Every "Owner.member" in the doc (in text or code blocks) whose owner is one
# of these must exist; other owners (Qt classes) aren't checked.
OWNERS: dict[str, type | ModuleType] = {
    "backends": backends,
    "CommandServer": CommandServer,
    "ScreenWatcher": ScreenWatcher,
    "ServiceHost": ServiceHost,
    "ServiceWorker": ServiceWorker,
    "Tray": Tray,
    "commands": commands,
    "ipc": ipc,
    "runtime": runtime,
}
REFERENCE = re.compile(rf"\b({'|'.join(OWNERS)})\.(\w+)")


def test_app_wiring_names_exist() -> None:
    references = set(REFERENCE.findall(WIRING.read_text(encoding="utf-8")))
    missing = sorted(f"{o}.{m}" for o, m in references if not hasattr(OWNERS[o], m))
    assert not missing, f"{WIRING.name} names code that no longer exists: {missing}"
    assert len(references) >= 30, "the doc should reference the code it describes"
