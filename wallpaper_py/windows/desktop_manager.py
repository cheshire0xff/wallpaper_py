from dataclasses import dataclass
from typing import Iterable, Optional
from pathlib import Path
from wallpaper_py.desktop_protocol import (
    Monitor as MonitorProtocol,
    MonitorDescription as MonitorDescriptionProtocol,
    ImageMode,
    Rectangle,
    UnsupportedModeError,
    WallpaperSettings,
)
from .desktop_wallpaper import IDesktopWallpaper, Position


@dataclass
class MonitorDescription(MonitorDescriptionProtocol):
    id: str
    index: int
    rect: Rectangle


@dataclass
class Monitor(MonitorProtocol):
    monitor_description: MonitorDescription
    wallpaper_settings: WallpaperSettings


class DesktopManager:

    def __init__(self) -> None:
        self.__dw = IDesktopWallpaper.co_create_instance()

    @staticmethod
    def get_position(mode: ImageMode) -> Position:
        match mode:
            case ImageMode.FILL | ImageMode.STRETCH | ImageMode.FIT:
                return Position[mode.name]
            case _:
                raise UnsupportedModeError(f"{mode} not supported")

    @staticmethod
    def get_mode(position: Position) -> ImageMode:
        match position:
            case Position.FILL | Position.FIT | Position.STRETCH:
                return ImageMode[position.name]
            case _:
                raise UnsupportedModeError(f"{position} not supported")

    def get_monitors(self) -> list[Monitor]:
        count = self.__dw.get_monitor_device_path_count()
        result: list[Monitor] = []
        for index in range(count):
            monitor_id = self.__dw.get_monitor_device_path_at(index)
            rect = self.__dw.get_monitor_rect(monitor_id)
            wallpaper = Path(self.__dw.get_wallpaper(monitor_id))
            desc = MonitorDescription(
                id=monitor_id,
                rect=rect,
                index=index,
            )
            wallpaper_settings = WallpaperSettings(wallpaper)
            result.append(
                Monitor(monitor_description=desc, wallpaper_settings=wallpaper_settings)
            )
        return result

    def set_wallpaper(
        self,
        monitor_description: MonitorDescription,
        wallpaper: Path,
        *,
        mode: Optional[ImageMode] = None,
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
        self.__dw.set_wallpaper(monitor_description.id, str(wallpaper.absolute()))
        if mode is None:
            return
        self.set_global_mode(mode)

    def get_supported_modes(self) -> Iterable[ImageMode]:
        return (ImageMode.FILL, ImageMode.STRETCH, ImageMode.FIT)

    def is_mode_per_monitor_supported(self) -> bool:
        return False

    def set_global_mode(self, mode: ImageMode) -> None:
        position = self.get_position(mode)
        self.__dw.set_position(position)

    def get_global_mode(self) -> ImageMode:
        return self.get_mode(self.__dw.get_position())
