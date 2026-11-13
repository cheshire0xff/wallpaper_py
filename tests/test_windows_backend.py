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
    ImageMode,
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


def test_global_mode_is_readable() -> None:
    mode = WindowsBackend().global_mode()
    assert mode is None or isinstance(mode, ImageMode)


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
    def get_monitor_device_path_count(self) -> int:
        return 0

    def get_position(self) -> Position:
        return Position.FILL


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
    assert backend.global_mode() is ImageMode.FILL


def test_dead_explorer_on_set_reconnects(monkeypatch: pytest.MonkeyPatch) -> None:
    instances: list[object] = [_DeadExplorer(), _LiveExplorer()]
    monkeypatch.setattr(
        IDesktopWallpaper, "co_create_instance", lambda: instances.pop(0)
    )
    backend = WindowsBackend()
    with pytest.raises(MonitorNotAttachedError):
        backend.set_wallpaper(MonitorId("x"), Path("a.jpg"))
    assert backend.global_mode() is ImageMode.FILL
