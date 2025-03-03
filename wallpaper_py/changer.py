import argparse
from pathlib import Path
from typing import Optional
import comtypes

from wallpaper_py.cli_parsers import existing_file_type
from wallpaper_py.image import image_mode_parse, process_image
from .desktop_manager import DesktopManager
from .desktop_protocol import ImageMode


def list_monitors() -> None:
    """List all connected monitors with their properties."""
    try:
        manager = DesktopManager()
        monitors = manager.get_monitors()

        print(f"Connected monitors ({len(monitors)}):")
        if not manager.is_mode_per_monitor_supported():
            mode = manager.get_global_mode()
            print("Global mode:", mode)

        for index, monitor in enumerate(monitors):
            rect = monitor.monitor_description.rect
            print(f"\tIndex:     {index}")
            print(f"\tPosition:  {rect.x1}, {rect.y1} to {rect.x2}, {rect.y2}")
            print(f"\tSize:      {rect.get_width()}x{rect.get_height()}")
            print(f"\tWallpaper: {monitor.wallpaper_settings.wallpaper}")

    except comtypes.COMError as e:
        print(f"COM Error: {e}")
    finally:
        comtypes.CoUninitialize()


def set_wallpaper(
    image_path: Path, monitor_ix: int, mode: Optional[ImageMode] = None
) -> None:
    comtypes.CoInitialize()
    try:
        manager = DesktopManager()
        monitors = manager.get_monitors()
        try:
            monitor = monitors[monitor_ix]
        except IndexError as err:
            raise RuntimeError(
                f"Invalid monitor index: {monitor_ix}! Found monitors: {monitors}"
            ) from err
        image = image_path
        if mode is not None:
            rect = monitor.monitor_description.rect
            image = process_image(image_path, rect.get_width(), rect.get_height(), mode)
        manager.set_wallpaper(monitor.monitor_description, image)

    except comtypes.COMError as e:
        print(f"COM Error: {e}")
    finally:
        comtypes.CoUninitialize()


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
