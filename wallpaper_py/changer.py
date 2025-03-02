import os
import sys
from pathlib import Path
import comtypes
from .desktop_wallpaper import IDesktopWallpaper

def list_monitors() -> None:
    """List all connected monitors with their properties."""
    comtypes.CoInitialize()
    try:
        dw = IDesktopWallpaper.CoCreateInstance()
        print("Before")
        count = dw.GetMonitorDevicePathCount()
        print(f"Connected monitors ({count}):")
        
        for index in range(count):
            monitor_id = dw.GetMonitorDevicePathAt(index)
            rect = dw.GetMonitorRECT(monitor_id)
            wallpaper = dw.GetWallpaper(monitor_id)
            
            print(f"[Monitor {index}]")
            print(f"\tDevice ID: {monitor_id}")
            print(f"\tPosition:  {rect.left}, {rect.top} to {rect.right}, {rect.bottom}")
            print(f"\tSize:      {rect.right - rect.left}x{rect.bottom - rect.top}")
            print(f"\tWallpaper: {wallpaper}")
            
    except comtypes.COMError as e:
        print(f"COM Error: {e}")
    finally:
        comtypes.CoUninitialize()

def set_wallpaper(image_path: str, monitor_ix: int = 0) -> None:
    comtypes.CoInitialize()
    try:
        dw = IDesktopWallpaper.CoCreateInstance()

        count = dw.GetMonitorDevicePathCount()
        if monitor_ix >= count:
            raise ValueError(f"Invalid index: {monitor_ix}. Found {count} monitors.")

        abs_path = str(Path(image_path).absolute())
        if not os.path.exists(abs_path):
            raise FileNotFoundError(f"Wallpaper not found: {abs_path}")

        monitor_id = dw.GetMonitorDevicePathAt(monitor_ix)
        dw.SetWallpaper(monitor_id, abs_path)
        
    except comtypes.COMError as e:
        print(f"COM Error: {e}")
    finally:
        comtypes.CoUninitialize()

def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python changer.py [list|set]")
        sys.exit(1)
        
    if sys.argv[1] == "list":
        list_monitors()
    elif sys.argv[1] == "set":
        if len(sys.argv) < 3:
            print("Usage: python changer.py set <image_path> [monitor_index=0] [mode=FILL]")
            sys.exit(1)
        monitor_ix = int(sys.argv[3]) if len(sys.argv) > 2 else 0
        set_wallpaper(sys.argv[2], monitor_ix)
        print(f"Wallpaper set successfully on monitor {monitor_ix}")


if __name__ == "__main__":
    main()