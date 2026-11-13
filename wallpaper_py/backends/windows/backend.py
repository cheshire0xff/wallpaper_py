"""Windows backend: IDesktopWallpaper (COM) + EDID names from DisplayConfig."""

import sys

assert sys.platform == "win32"  # pylint: disable=wrong-import-position

# pylint: disable=wrong-import-position,wrong-import-order
import re
from pathlib import Path

from comtypes import COMError

from wallpaper_py.core.model import (
    ImageMode,
    Monitor,
    MonitorId,
    MonitorNotAttachedError,
    UnsupportedModeError,
    WallpaperError,
)

from .com import IDesktopWallpaper, Position
from .display_config import monitor_names

_PNP_ID = re.compile(r"DISPLAY#([^#]+)#", re.IGNORECASE)


def fallback_name(device_path: str) -> str:
    """PnP id from the device path, e.g. 'DELA246', when EDID name is unknown."""
    match = _PNP_ID.search(device_path)
    return match.group(1) if match else device_path


class WindowsBackend:
    """
    Observed behaviour (see spikes/README.md): when a monitor is switched off
    it detaches; the remaining monitors get new indices *and* new positions.
    For a while its device path disappears, then it is listed again with
    GetMonitorRECT failing. Only the device path is a stable identity.

    IDesktopWallpaper is served by Explorer: once Explorer restarts, the old
    object only fails (RPC_E_DISCONNECTED). Any COM failure that is not
    handled in place drops the object and becomes a WallpaperError; the
    next call creates a new one.
    """

    def __init__(self) -> None:
        self._dw: IDesktopWallpaper | None = IDesktopWallpaper.co_create_instance()
        # EDID names are only reported for attached monitors; remember them
        # so a detached monitor keeps its name.
        self._known_names: dict[MonitorId, str] = {}

    def _wallpaper(self) -> IDesktopWallpaper:
        if self._dw is None:
            self._dw = IDesktopWallpaper.co_create_instance()
        return self._dw

    def _com_failed(self, err: COMError) -> WallpaperError:
        self._dw = None
        return WallpaperError(f"Windows wallpaper call failed: {err}")

    @property
    def per_monitor_modes(self) -> bool:
        return False

    def monitors(self) -> list[Monitor]:
        self._known_names.update(monitor_names())
        try:
            return self._monitors(self._wallpaper())
        except COMError as err:
            raise self._com_failed(err) from err

    def _monitors(self, dw: IDesktopWallpaper) -> list[Monitor]:
        result: list[Monitor] = []
        seen: set[MonitorId] = set()
        for index in range(dw.get_monitor_device_path_count()):
            try:
                device_path = dw.get_monitor_device_path_at(index)
            except COMError:
                continue
            monitor_id = MonitorId(device_path)
            # Stale entries with an empty id are always listed; skip them.
            if not device_path or monitor_id in seen:
                continue
            seen.add(monitor_id)
            try:
                rect = dw.get_monitor_rect(device_path)
            except COMError:
                rect = None  # known but detached
            try:
                wallpaper = dw.get_wallpaper(device_path)
            except COMError:
                wallpaper = ""
            result.append(
                Monitor(
                    id=monitor_id,
                    name=self._known_names.get(monitor_id)
                    or fallback_name(device_path),
                    rect=rect,
                    wallpaper=Path(wallpaper) if wallpaper else None,
                )
            )
        return result

    def set_wallpaper(
        self, monitor_id: MonitorId, image: Path, mode: ImageMode | None = None
    ) -> None:
        if mode is not None:
            raise UnsupportedModeError(
                "Windows applies the image mode to all monitors; "
                "use set_global_mode or a pre-rendered image"
            )
        try:
            dw = self._wallpaper()
        except COMError as err:
            raise self._com_failed(err) from err
        try:
            dw.get_monitor_rect(str(monitor_id))
        except COMError as err:
            # Also what a dead Explorer looks like: drop the object either way.
            self._dw = None
            raise MonitorNotAttachedError(f"{monitor_id} is not attached") from err
        try:
            dw.set_wallpaper(str(monitor_id), str(image.resolve()))
        except COMError as err:
            raise self._com_failed(err) from err

    def set_global_mode(self, mode: ImageMode) -> None:
        try:
            self._wallpaper().set_position(Position[mode.name])
        except COMError as err:
            raise self._com_failed(err) from err

    def global_mode(self) -> ImageMode | None:
        try:
            position = self._wallpaper().get_position()
        except COMError as err:
            raise self._com_failed(err) from err
        if position.name in ImageMode.__members__:
            return ImageMode[position.name]
        return None  # tile, span, center
