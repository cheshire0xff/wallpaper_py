"""
This module defines the base protocol for desktop wallpaper management.
It specifies interfaces for MonitorDescription, DesktopManager, and related types.
Note: The Windows-specific implementation (in desktop_manager.py) extends MonitorDescription
with additional attributes (e.g., 'id', 'index') required for Windows.
Cross-platform code should only rely on the attributes defined here.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional, Protocol, Sequence


@dataclass
class Rectangle:
    x1: int
    y1: int
    x2: int
    y2: int

    def get_height(self) -> int:
        return self.y2 - self.y1

    def get_width(self) -> int:
        return self.x2 - self.x1


class ImageMode(Enum):
    FILL = "fill"
    FIT = "fit"
    STRETCH = "stretch"


class MonitorDescription(Protocol):
    rect: Rectangle


@dataclass
class WallpaperSettings:
    wallpaper: Path
    image_mode: Optional[ImageMode] = None


class Monitor(Protocol):
    monitor_description: MonitorDescription
    wallpaper_settings: WallpaperSettings


class UnsupportedModeError(Exception): ...


class DesktopManager(Protocol):
    """
    Protocol defining the interface for managing desktop settings such as wallpaper
    and image modes.
    """

    def get_monitors(self) -> Sequence[Monitor]:
        """
        Retrieve all monitors connected to the desktop.

        Returns:
            An iterable of Monitor objects.
        """

    def set_wallpaper(
        self,
        monitor_description: MonitorDescription,
        wallpaper: Path,
        *,
        mode: Optional[ImageMode] = None
    ) -> None:
        """
        Set the wallpaper on a specific monitor.

        Parameters:
            monitor: The monitor on which to set the wallpaper.
            wallpaper: The path to the new wallpaper image.
            mode: Optional image mode (e.g., fill, fit, stretch). If not specified,
                  the existing mode remains unchanged.

        Raises:
            UnsupportedModeError: If the requested image mode is not supported.
        """

    def get_supported_modes(self) -> Iterable[ImageMode]:
        """
        Get the list of image modes that are supported by this desktop manager.

        Returns:
            An iterable of supported ImageMode enums.
        """

    def is_mode_per_monitor_supported(self) -> bool:
        """
        Check if individual monitors can have different image modes.

        Returns:
            True if per-monitor image modes are supported, otherwise False.
        """

    def set_global_mode(self, mode: ImageMode) -> None:
        """
        Set a global image mode for all monitors.

        Parameters:
            mode: The image mode to be applied globally.

        Raises:
            UnsupportedModeError: If the requested image mode is not supported globally.
        """

    def get_global_mode(self) -> ImageMode:
        """
        Get a global image mode for all monitors.
        Raises:
            UnsupportedModeError: If the image mode is not set globally.
        """
