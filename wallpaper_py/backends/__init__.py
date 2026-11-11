"""Pick the backend (and presence hook) for the current desktop environment."""

from __future__ import annotations

import os
import sys

from wallpaper_py.core.backend import Backend
from wallpaper_py.core.model import UnsupportedPlatformError
from wallpaper_py.core.presence import NoPresence, Presence


def get_backend() -> Backend:
    if sys.platform == "win32":
        # pylint: disable=import-outside-toplevel
        from .windows.backend import WindowsBackend

        return WindowsBackend()
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    if "KDE" in desktop.upper().split(":"):
        raise UnsupportedPlatformError(
            "KDE Plasma support is planned but not implemented yet"
        )
    raise UnsupportedPlatformError(
        f"No wallpaper backend for platform={sys.platform!r}, "
        f"XDG_CURRENT_DESKTOP={desktop!r}. Supported: Windows (KDE Plasma planned)."
    )


def get_presence() -> Presence:
    """Session lock / idle detection; never fails, may know nothing."""
    if sys.platform == "win32":
        # pylint: disable=import-outside-toplevel
        from .windows.presence import WindowsPresence

        return WindowsPresence()
    return NoPresence()  # logind / KDE idle: Phase 6
