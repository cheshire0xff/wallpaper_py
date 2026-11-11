"""
Pick the platform pieces for the current desktop environment: wallpaper
backend, presence hook, autostart, the per-thread setup they need, and
window focus between our processes.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager

from wallpaper_py.core.autostart import Autostart
from wallpaper_py.core.backend import Backend
from wallpaper_py.core.model import UnsupportedPlatformError
from wallpaper_py.core.presence import NoPresence, Presence


@contextmanager
def thread_scope() -> Iterator[None]:
    """
    Per-thread setup for a thread that creates and uses the backend (COM on
    Windows). Enter it on that thread before `get_backend()`, and leave it
    only after the backend is released.
    """
    if sys.platform == "win32":
        # pylint: disable=import-outside-toplevel
        from .windows.com import com_apartment

        with com_apartment():
            yield
    else:
        yield


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


def allow_foreground() -> None:
    """
    Let another process (the UI process) bring its window to the front;
    call it right after the user's click. Only Windows needs this.
    """
    if sys.platform == "win32":
        # pylint: disable=import-outside-toplevel
        from .windows.foreground import allow_foreground as allow

        allow()


def get_autostart() -> Autostart:
    if sys.platform == "win32":
        # pylint: disable=import-outside-toplevel
        from .windows.autostart import RunKeyAutostart

        return RunKeyAutostart()
    # pylint: disable=import-outside-toplevel
    from .xdg import XdgAutostart

    return XdgAutostart()
