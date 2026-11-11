"""Platform-independent data model shared by backends, core logic, CLI and GUI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# Seconds since the epoch (time.time); injected so tests control time.
Clock = Callable[[], float]


@dataclass(frozen=True)
class Rectangle:
    """Monitor area in physical pixels, in desktop coordinates."""

    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height


class ImageMode(Enum):
    FILL = "fill"
    FIT = "fit"
    STRETCH = "stretch"


@dataclass(frozen=True)
class Monitor:
    """
    One physical monitor.

    `id` is the only stable identity: it survives the monitor being switched
    off and on, while index and position do not (on Windows, switching the
    monitor off re-indexes and moves the remaining ones).
    `rect` is None while the monitor is known but detached.
    """

    id: str
    name: str
    rect: Rectangle | None
    wallpaper: Path | None = None

    @property
    def attached(self) -> bool:
        return self.rect is not None


class WallpaperError(Exception):
    """Base class for errors raised by backends and core."""


class MonitorNotAttachedError(WallpaperError):
    """The target monitor is unknown or currently detached; nothing was set."""


class UnsupportedModeError(WallpaperError):
    """The backend cannot apply the requested image mode this way."""


class UnsupportedPlatformError(WallpaperError):
    """No backend exists for the current desktop environment."""


class ImageLoadError(WallpaperError):
    """The image file is missing, unreadable or not an image."""
