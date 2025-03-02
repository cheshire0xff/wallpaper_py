from dataclasses import dataclass

from wallpaper_py.desktop_wallpaper import IDesktopWallpaper, Rectangle


@dataclass
class Monitor:
    id: str
    index: int
    rect: Rectangle
    wallpaper: str


def get_monitors(dw: IDesktopWallpaper) -> list[Monitor]:
    count = dw.GetMonitorDevicePathCount()
    result: list[Monitor] = []
    for index in range(count):
        monitor_id = dw.GetMonitorDevicePathAt(index)
        rect = dw.GetMonitorRECT(monitor_id)
        wallpaper = dw.GetWallpaper(monitor_id)
        result.append(
            Monitor(id=monitor_id, rect=rect, wallpaper=wallpaper, index=index)
        )
    return result
