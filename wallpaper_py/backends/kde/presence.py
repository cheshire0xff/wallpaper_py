"""
The session's lock state on KDE Plasma, from the screen saver's D-Bus
interface (KScreenLocker serves it).

Idle time is unknown: on Wayland the compositor keeps it to itself (its
idle protocol needs a Wayland client), the screen saver interface refuses
GetSessionIdleTime there, and Plasma doesn't set logind's IdleHint. So on
KDE rotation pauses while locked, not while idle.
"""

import sys

assert sys.platform == "linux"  # pylint: disable=wrong-import-position

# pylint: disable=wrong-import-position,wrong-import-order
from wallpaper_py.core.model import WallpaperError

from . import tools


class KdePresence:
    def __init__(self, run: tools.Runner = tools.run) -> None:
        self._run = run

    def session_locked(self) -> bool | None:
        try:
            reply = tools.call(
                self._run,
                "org.freedesktop.ScreenSaver",
                "/ScreenSaver",
                "org.freedesktop.ScreenSaver.GetActive",
            )
        except WallpaperError:
            return None  # no screen locker answering: unknown, never paused
        return reply[0] if reply and isinstance(reply[0], bool) else None

    def idle_seconds(self) -> float | None:
        return None
