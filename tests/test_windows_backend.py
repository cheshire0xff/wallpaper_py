"""Read-only checks against the real Windows desktop. Never sets anything."""

import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows only", allow_module_level=True)
# Never fails (skipped above); tells mypy the rest is Windows-only.
assert sys.platform == "win32"

# pylint: disable=wrong-import-position
from wallpaper_py.backends.windows.backend import WindowsBackend, fallback_name
from wallpaper_py.core.model import ImageMode

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
