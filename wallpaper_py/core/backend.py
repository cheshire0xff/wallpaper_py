"""The small interface every platform backend implements."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .model import ImageMode, Monitor


class Backend(Protocol):
    """
    Platform-specific wallpaper access.

    Everything above this layer (playlists, scheduler, GUI, CLI) only uses
    this protocol and addresses monitors by `Monitor.id`.
    """

    @property
    def per_monitor_modes(self) -> bool:
        """True if `set_wallpaper(..., mode=...)` can differ per monitor."""

    def monitors(self) -> list[Monitor]:
        """All known monitors, including detached ones (rect is None)."""

    def set_wallpaper(
        self, monitor_id: str, image: Path, mode: ImageMode | None = None
    ) -> None:
        """
        Set `image` on one monitor.

        `mode` may only be passed when `per_monitor_modes` is True; otherwise
        use `set_global_mode` or pre-render the image (see core.setter).

        Raises:
            MonitorNotAttachedError: monitor unknown or detached right now.
            UnsupportedModeError: mode passed but not supported per monitor.
        """

    def set_global_mode(self, mode: ImageMode) -> None:
        """Apply `mode` to every monitor."""

    def global_mode(self) -> ImageMode | None:
        """
        The mode applied to all monitors, or None if there is none or it is
        one this app does not model (Windows tile / span / center).
        """
