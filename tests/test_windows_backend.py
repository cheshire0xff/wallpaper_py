"""Read-only checks against the real Windows desktop. Never sets anything."""

import sys
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest

if sys.platform != "win32":
    pytest.skip("Windows only", allow_module_level=True)
# Never fails (skipped above); tells mypy the rest is Windows-only.
assert sys.platform == "win32"

# pylint: disable=wrong-import-position
from comtypes import COMError

from wallpaper_py.backends.windows import backend as backend_module
from wallpaper_py.backends.windows.backend import WindowsBackend, fallback_name
from wallpaper_py.backends.windows.com import IDesktopWallpaper, Position
from wallpaper_py.core.model import (
    MonitorId,
    MonitorNotAttachedError,
    WallpaperError,
)

RPC_E_DISCONNECTED = -2147417848  # 0x80010108

pytestmark = pytest.mark.windows


def test_monitors_have_stable_ids_names_and_sizes() -> None:
    monitors = WindowsBackend().monitors()
    ids = [m.id for m in monitors]
    assert all(str(i) for i in ids), "stale entries with empty ids must be filtered out"
    assert len(ids) == len(set(ids))
    for monitor in monitors:
        assert monitor.name
        if monitor.rect is not None:
            assert monitor.rect.width > 0 and monitor.rect.height > 0


@pytest.mark.parametrize(
    ("device_path", "expected"),
    [
        ("\\\\?\\DISPLAY#DELA246#5&48925cd&0&UID4353#{e6f07b5f}", "DELA246"),
        ("weird", "weird"),
    ],
)
def test_fallback_name(device_path: str, expected: str) -> None:
    assert fallback_name(device_path) == expected


class _DeadExplorer:
    """IDesktopWallpaper after Explorer restarted: every call fails."""

    def __getattr__(self, name: str) -> Callable[..., NoReturn]:
        def fail(*_args: object) -> NoReturn:
            raise COMError(RPC_E_DISCONNECTED, "disconnected", None)

        return fail


class _LiveExplorer:
    """Records what is set; one attached monitor, any id."""

    def __init__(self, position: Position = Position.FILL) -> None:
        self.position = position
        self.positions_set: list[Position] = []
        self.wallpapers: list[tuple[str, str]] = []

    def get_monitor_device_path_count(self) -> int:
        return 0

    def get_monitor_rect(self, _monitor_id: str) -> object:
        return object()

    def get_position(self) -> Position:
        return self.position

    def set_position(self, position: Position) -> None:
        self.positions_set.append(position)
        self.position = position

    def set_wallpaper(self, monitor_id: str, wallpaper: str) -> None:
        self.wallpapers.append((monitor_id, wallpaper))


def test_com_failure_becomes_wallpaper_error_and_reconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances: list[object] = [_DeadExplorer(), _LiveExplorer()]
    monkeypatch.setattr(
        IDesktopWallpaper, "co_create_instance", lambda: instances.pop(0)
    )
    monkeypatch.setattr(backend_module, "monitor_names", dict)
    backend = WindowsBackend()
    with pytest.raises(WallpaperError):
        backend.monitors()
    assert not backend.monitors()


def test_dead_explorer_on_set_reconnects(monkeypatch: pytest.MonkeyPatch) -> None:
    live = _LiveExplorer()
    instances: list[object] = [_DeadExplorer(), live]
    monkeypatch.setattr(
        IDesktopWallpaper, "co_create_instance", lambda: instances.pop(0)
    )
    backend = WindowsBackend()
    with pytest.raises(MonitorNotAttachedError):
        backend.set_wallpaper(MonitorId("x"), Path("a.bmp"))
    backend.set_wallpaper(MonitorId("x"), Path("a.bmp"))
    assert live.wallpapers == [("x", str(Path("a.bmp").resolve()))]


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        (Position.TILE, [Position.FILL]),
        (Position.SPAN, [Position.FILL]),
        (Position.FIT, [Position.FILL]),
        (Position.FILL, []),
    ],
)
def test_set_keeps_the_position_at_fill(
    monkeypatch: pytest.MonkeyPatch, position: Position, expected: list[Position]
) -> None:
    """Set only when it differs: setting it redraws every monitor."""
    live = _LiveExplorer(position)
    monkeypatch.setattr(IDesktopWallpaper, "co_create_instance", lambda: live)
    backend = WindowsBackend()
    backend.set_wallpaper(MonitorId("x"), Path("a.bmp"))
    backend.set_wallpaper(MonitorId("x"), Path("b.bmp"))
    assert live.positions_set == expected
    assert len(live.wallpapers) == 2
