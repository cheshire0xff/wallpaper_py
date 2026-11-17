"""
Pick the platform pieces for the current desktop environment: wallpaper
backend, presence hook, autostart, the per-thread setup they need, window
focus between our processes, and playing a sound.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from wallpaper_py.core.autostart import Autostart
from wallpaper_py.core.backend import Backend
from wallpaper_py.core.model import UnsupportedPlatformError, WallpaperError
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
    if sys.platform == "linux" and _kde():
        # pylint: disable=import-outside-toplevel
        from .kde.backend import KdeBackend

        return KdeBackend()
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    raise UnsupportedPlatformError(
        f"No wallpaper backend for platform={sys.platform!r}, "
        f"XDG_CURRENT_DESKTOP={desktop!r}. Supported: Windows, KDE Plasma."
    )


def _kde() -> bool:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    return "KDE" in desktop.upper().split(":")


def get_presence() -> Presence:
    """Session lock / idle detection; never fails, may know nothing."""
    if sys.platform == "win32":
        # pylint: disable=import-outside-toplevel
        from .windows.presence import WindowsPresence

        return WindowsPresence()
    if sys.platform == "linux" and _kde():
        # pylint: disable=import-outside-toplevel
        from .kde.presence import KdePresence

        return KdePresence()
    return NoPresence()


def allow_foreground() -> None:
    """
    Let another process (the window process) bring its window to the front;
    call it right after the user's click. Only Windows needs this.
    """
    if sys.platform == "win32":
        # pylint: disable=import-outside-toplevel
        from .windows.foreground import allow_foreground as allow

        allow()


def set_title_bar_colour(window: int, rgb: tuple[int, int, int]) -> None:
    """
    Paint a window's title bar (`window`: its native id) in `rgb`, where the
    system lets an app do that: Windows 11. Elsewhere (KDE: the window
    manager's, in the colour scheme) nothing.
    """
    if sys.platform == "win32":
        # pylint: disable=import-outside-toplevel
        from .windows.title_bar import set_title_bar_colour as set_colour

        set_colour(window, rgb)


def play_sound(path: Path) -> None:
    """
    Play a WAV file and return at once (it plays on); safe on the service
    thread.

    Raises:
        WallpaperError: the platform can't play it (e.g. no player installed).
    """
    if sys.platform == "win32":
        # pylint: disable=import-outside-toplevel
        from .windows.sound import play_sound as play
    elif sys.platform == "linux":
        # pylint: disable=import-outside-toplevel
        from .pipewire import play_sound as play
    else:
        raise WallpaperError(f"No sound player for platform={sys.platform!r}")
    play(path)


def get_autostart() -> Autostart:
    if sys.platform == "win32":
        # pylint: disable=import-outside-toplevel
        from .windows.autostart import RunKeyAutostart

        return RunKeyAutostart()
    # pylint: disable=import-outside-toplevel
    from .xdg import XdgAutostart

    return XdgAutostart()
