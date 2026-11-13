"""The Run key autostart, against a throwaway key (never the real Run key)."""

import sys
import uuid
from collections.abc import Iterator

import pytest

if sys.platform != "win32":
    pytest.skip("Windows only", allow_module_level=True)
# Never fails (skipped above); tells mypy the rest is Windows-only.
assert sys.platform == "win32"

# pylint: disable=wrong-import-position,wrong-import-order
import winreg

from wallpaper_py.backends.windows.autostart import RunKeyAutostart

PARENT = r"Software\wallpaper_py-tests"


@pytest.fixture(name="key")
def fixture_key() -> Iterator[str]:
    key = rf"{PARENT}\{uuid.uuid4().hex}"
    yield key
    for path in (key, PARENT):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            pass  # already gone, or the parent holds another run's key


def test_run_key(key: str) -> None:
    autostart = RunKeyAutostart(key=key)
    assert not autostart.enabled()
    autostart.disable()  # nothing to remove: fine
    autostart.enable([r"C:\My Apps\pythonw.exe", "-m", "wallpaper_py", "tray"])
    assert autostart.enabled()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as opened:
        value, kind = winreg.QueryValueEx(opened, "wallpaper_py")
    assert kind == winreg.REG_SZ
    assert value == r'"C:\My Apps\pythonw.exe" -m wallpaper_py tray'
    autostart.disable()
    assert not autostart.enabled()
