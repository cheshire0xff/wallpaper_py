"""Read-only checks of session lock / idle detection on the real desktop."""

import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows only", allow_module_level=True)
# Never fails (skipped above); tells mypy the rest is Windows-only.
assert sys.platform == "win32"

# pylint: disable=wrong-import-position
from wallpaper_py.backends.windows.presence import WindowsPresence

pytestmark = pytest.mark.windows


def test_lock_state_is_readable() -> None:
    # CI runs without an interactive desktop, so only the type is checked.
    assert WindowsPresence().session_locked() in (True, False, None)


def test_idle_time_is_plausible() -> None:
    idle = WindowsPresence().idle_seconds()
    assert idle is None or 0 <= idle < 50 * 86400
