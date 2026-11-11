"""Command-line interface: `wallpaper list` and `wallpaper set`."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from .backends import get_backend
from .core import paths
from .core.backend import Backend
from .core.model import ImageMode, Monitor, WallpaperError
from .core.selection import resolve_monitor
from .core.setter import apply_wallpaper

BackendFactory = Callable[[], Backend]


def format_monitors(monitors: Sequence[Monitor]) -> str:
    lines = [f"Monitors ({len(monitors)}):"]
    for monitor in monitors:
        lines.append(f"  {monitor.name}")
        if monitor.rect is None:
            lines.append("    state:     detached")
        else:
            r = monitor.rect
            lines.append(f"    position:  {r.x1}, {r.y1}")
            lines.append(f"    size:      {r.width}x{r.height}")
        lines.append(f"    wallpaper: {monitor.wallpaper or '-'}")
        lines.append(f"    id:        {monitor.id}")
    return "\n".join(lines)


def cmd_list(backend: Backend, args: argparse.Namespace) -> int:
    monitors = backend.monitors()
    if not args.all:
        monitors = [m for m in monitors if m.attached]
    print(format_monitors(monitors))
    return 0


def cmd_set(backend: Backend, args: argparse.Namespace) -> int:
    monitor = resolve_monitor(backend.monitors(), args.monitor)
    used = apply_wallpaper(
        backend, monitor.id, args.image, args.mode, paths.render_dir()
    )
    suffix = f" (rendered: {used})" if used != args.image else ""
    print(f"Set {args.image.name} on {monitor.name}{suffix}")
    return 0


def existing_file(value: str) -> Path:
    path = Path(value)
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"{value} is not an existing file")
    return path


def image_mode(value: str) -> ImageMode:
    try:
        return ImageMode(value.lower())
    except ValueError as err:
        choices = ", ".join(m.value for m in ImageMode)
        raise argparse.ArgumentTypeError(f"choose from: {choices}") from err


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wallpaper", description="Per-monitor wallpaper management"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="list monitors")
    list_parser.add_argument(
        "--all", action="store_true", help="include detached monitors"
    )
    list_parser.set_defaults(handler=cmd_list)

    set_parser = sub.add_parser("set", help="set the wallpaper on one monitor")
    set_parser.add_argument("image", type=existing_file, help="image file")
    set_parser.add_argument(
        "-m",
        "--monitor",
        required=True,
        help="monitor name (or a unique part of it) or full id; see `wallpaper list`",
    )
    set_parser.add_argument(
        "--mode",
        type=image_mode,
        help="fill, fit or stretch; on Windows the image is rendered to the "
        "monitor's resolution so each monitor can use its own mode",
    )
    set_parser.set_defaults(handler=cmd_set)
    return parser


def main(
    argv: Sequence[str] | None = None, backend_factory: BackendFactory = get_backend
) -> int:
    args = build_parser().parse_args(argv)
    handler: Callable[[Backend, argparse.Namespace], int] = args.handler
    try:
        return handler(backend_factory(), args)
    except WallpaperError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
