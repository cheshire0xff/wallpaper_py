import argparse
import os
from pathlib import Path
import comtypes
from .desktop_wallpaper import IDesktopWallpaper, Position


def list_monitors() -> None:
    """List all connected monitors with their properties."""
    comtypes.CoInitialize()
    try:
        dw = IDesktopWallpaper.CoCreateInstance()
        print("Before")
        count = dw.GetMonitorDevicePathCount()
        print(f"Connected monitors ({count}):")
        print("Position:", dw.GetPosition())
        print(f"Background color: #{dw.GetBackgroundColor():08X}")

        for index in range(count):
            monitor_id = dw.GetMonitorDevicePathAt(index)
            rect = dw.GetMonitorRECT(monitor_id)
            wallpaper = dw.GetWallpaper(monitor_id)

            print(f"[Monitor {index}]")
            print(f"\tDevice ID: {monitor_id}")
            print(
                f"\tPosition:  {rect.left}, {rect.top} to {rect.right}, {rect.bottom}"
            )
            print(f"\tSize:      {rect.right - rect.left}x{rect.bottom - rect.top}")
            print(f"\tWallpaper: {wallpaper}")

    except comtypes.COMError as e:
        print(f"COM Error: {e}")
    finally:
        comtypes.CoUninitialize()


def set_wallpaper(image_path: str, monitor_ix: int, mode: Position) -> None:
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
        dw.SetPosition(mode)

    except comtypes.COMError as e:
        print(f"COM Error: {e}")
    finally:
        comtypes.CoUninitialize()


def position_parse(arg: str) -> Position:
    return Position[arg.upper()]


def main() -> None:
    args = get_args()
    if args.command == "list":
        list_monitors()
    elif args.command == "set":
        set_wallpaper(args.image_path, args.monitor, args.mode)
        print(
            f"Wallpaper set successfully on monitor {args.monitor} with {args.mode} mode"
        )


def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage Windows desktop wallpapers")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # List command
    _list_parser = subparsers.add_parser("list", help="List all connected monitors")

    # Set command
    set_parser = subparsers.add_parser("set", help="Set wallpaper for a monitor")
    set_parser.add_argument("image_path", help="Path to the image file")
    set_parser.add_argument(
        "-m", "--monitor", type=int, default=0, help="Monitor index (default: 0)"
    )
    set_parser.add_argument(
        "--mode",
        type=position_parse,
        choices=list(Position),
        default="FILL",
        help="Wallpaper position mode (default: FILL)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
