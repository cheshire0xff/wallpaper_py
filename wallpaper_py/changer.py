import argparse
import os
from pathlib import Path
from typing import Optional
import comtypes

from wallpaper_py.cli_parsers import existing_file_type
from wallpaper_py.image import ImageMode, image_mode_parse, process_image
from wallpaper_py.monitor import get_monitors
from .desktop_wallpaper import IDesktopWallpaper, Position


def list_monitors() -> None:
    """List all connected monitors with their properties."""
    comtypes.CoInitialize()
    try:
        dw = IDesktopWallpaper.CoCreateInstance()
        monitors = get_monitors(dw)
        print(f"Connected monitors ({len(monitors)}):")
        print("Global mode:", dw.GetPosition())
        print(f"Background color: #{dw.GetBackgroundColor():08X}")

        for monitor in monitors:
            monitor_id = monitor.id
            rect = monitor.rect
            print(f"[Monitor {monitor.index}]")
            print(f"\tDevice ID: {monitor_id}")
            print(f"\tPosition:  {rect.x1}, {rect.y1} to {rect.x2}, {rect.y2}")
            print(f"\tSize:      {rect.get_width()}x{rect.get_height()}")
            print(f"\tWallpaper: {monitor.wallpaper}")

    except comtypes.COMError as e:
        print(f"COM Error: {e}")
    finally:
        comtypes.CoUninitialize()


def set_wallpaper(
    image_path: Path, monitor_ix: int, mode: Optional[ImageMode] = None
) -> None:
    comtypes.CoInitialize()
    try:
        dw = IDesktopWallpaper.CoCreateInstance()
        monitors = get_monitors(dw)
        try:
            monitor = [
                monitor for monitor in monitors if (monitor.index == monitor_ix)
            ][0]
        except IndexError as err:
            raise RuntimeError(
                f"Invalid monitor index: {monitor_ix}! Found monitors: {monitors}"
            ) from err
        image = image_path
        if mode is not None:
            image = process_image(
                image_path, monitor.rect.get_width(), monitor.rect.get_height(), mode
            )
        dw.SetWallpaper(monitor.id, str(image.absolute()))

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
    set_parser.add_argument(
        "image_path", type=existing_file_type, help="Path to the image file"
    )
    set_parser.add_argument(
        "-m", "--monitor", type=int, default=0, help="Monitor index (default: 0)"
    )
    set_parser.add_argument(
        "--mode",
        type=image_mode_parse,
        choices=list(ImageMode),
        help="Wallpaper position mode (default: None)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
