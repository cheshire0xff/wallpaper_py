"""The small interface every platform backend implements."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .model import Monitor, MonitorId


class Backend(Protocol):
    """
    Platform-specific wallpaper access.

    Everything above this layer (playlists, scheduler, GUI, CLI) only uses
    this protocol and addresses monitors by `Monitor.id`. Images are
    rendered above it (core.setter), so a backend only shows images that
    are exactly the monitor's size.
    """

    def monitors(self) -> list[Monitor]:
        """All known monitors, including detached ones (rect is None)."""

    def set_wallpaper(self, monitor_id: MonitorId, image: Path) -> None:
        """
        Show `image`, already the monitor's exact size, on one monitor.

        The backend keeps the system's own position at Fill for it, so a
        tile or span position left by another tool doesn't misplace it.

        Raises:
            MonitorNotAttachedError: monitor unknown or detached right now.
        """
