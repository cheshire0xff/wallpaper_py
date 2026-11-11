"""
Command-line interface.

Monitor commands work on their own; playlist commands use the config file
and the state database. `run` rotates in the foreground; the other playlist
commands can be used alongside it from another terminal (they share the
database), until the tray app takes over in Phase 3.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .backends import get_backend, get_presence
from .core import paths
from .core.backend import Backend
from .core.config import format_duration, load_config
from .core.model import ImageMode, Monitor, WallpaperError
from .core.presence import Presence
from .core.selection import resolve_monitor
from .core.service import PlaylistService, PlaylistStatus
from .core.setter import apply_wallpaper
from .core.store import Store

BackendFactory = Callable[[], Backend]
PresenceFactory = Callable[[], Presence]

log = logging.getLogger("wallpaper_py")


@dataclass
class Context:
    backend: Backend
    presence_factory: PresenceFactory
    config_path: Path

    @contextmanager
    def service(self, tick_period: float = 5.0) -> Iterator[PlaylistService]:
        config = load_config(self.config_path, default_dir=paths.data_dir())
        with Store(paths.state_db()) as store:
            yield PlaylistService(
                config,
                self.backend,
                self.presence_factory(),
                store,
                paths.render_dir(),
                tick_period=tick_period,
            )


# -- monitors ----------------------------------------------------------------


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


def cmd_list(ctx: Context, args: argparse.Namespace) -> int:
    monitors = ctx.backend.monitors()
    if not args.all:
        monitors = [m for m in monitors if m.attached]
    print(format_monitors(monitors))
    return 0


def cmd_set(ctx: Context, args: argparse.Namespace) -> int:
    monitor = resolve_monitor(ctx.backend.monitors(), args.monitor)
    used = apply_wallpaper(
        ctx.backend, monitor.id, args.image, args.mode, paths.render_dir()
    )
    suffix = f" (rendered: {used})" if used != args.image else ""
    print(f"Set {args.image.name} on {monitor.name}{suffix}")
    return 0


# -- playlists ---------------------------------------------------------------


def warn_about_config(service: PlaylistService) -> None:
    for warning in service.config.warnings():
        print(f"Warning: {warning}", file=sys.stderr)


def format_status(statuses: Sequence[PlaylistStatus], pause: str | None) -> str:
    lines: list[str] = []
    for status in statuses:
        playlist = status.playlist
        mode = playlist.mode.value if playlist.mode else "as is"
        lines.append(
            f"{playlist.name}  ({playlist.order.value}, every "
            f"{format_duration(playlist.interval)}, {mode})"
        )
        members = []
        for member in status.members:
            if member.monitor is None:
                state = "not found"
            else:
                state = "attached" if member.monitor.attached else "detached"
            members.append(f"{member.selector} ({state})")
        lines.append(f"  monitors:  {', '.join(members)}")
        lines.append(f"  directory: {playlist.directory}")
        lines.append(f"  current:   {status.current or '-'}")
        left = format_duration(playlist.interval - status.elapsed)
        lines.append(f"  next in:   {left} of active time")
        upcoming = ", ".join(p.name for p in status.upcoming) or "-"
        lines.append(f"  upcoming:  {upcoming}")
    lines.append(f"Rotation paused: {pause}" if pause else "Rotation active")
    return "\n".join(lines)


def cmd_status(ctx: Context, _: argparse.Namespace) -> int:
    with ctx.service() as service:
        warn_about_config(service)
        print(format_status(service.status(), service.pause_reason()))
    return 0


def cmd_next(ctx: Context, args: argparse.Namespace) -> int:
    with ctx.service() as service:
        print(service.next(service.find_playlist(args.target)).describe())
    return 0


def cmd_prev(ctx: Context, args: argparse.Namespace) -> int:
    with ctx.service() as service:
        playlist = service.find_playlist(args.target)
        change = service.previous(playlist)
        print(change.describe() if change else f"{playlist.name}: no earlier image")
    return 0


def cmd_fav(ctx: Context, args: argparse.Namespace) -> int:
    with ctx.service() as service:
        image, added = service.favourite(service.find_playlist(args.target))
        if added:
            print(f"Added {image} to {service.favourites.file}")
        else:
            print(f"{image.name} is already a favourite")
    return 0


def cmd_remove(ctx: Context, args: argparse.Namespace) -> int:
    with ctx.service() as service:
        playlist = service.find_playlist(args.target)
        image, change = service.remove(playlist)
        print(f"Listed {image} in {service.to_remove.file} (not deleted)")
        if change:
            print(change.describe())
        else:
            print(f"{playlist.name}: changes once a monitor is attached")
    return 0


def cmd_run(ctx: Context, args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S"
    )
    with ctx.service(tick_period=args.poll) as service:
        warn_about_config(service)
        names = ", ".join(p.name for p in service.config.playlists)
        log.info("Running playlists: %s (Ctrl+C to stop)", names)
        service.restore()
        try:
            run_loop(service, args.poll, ticks=args.ticks)
        except KeyboardInterrupt:
            log.info("Stopped")
    return 0


def run_loop(
    service: PlaylistService,
    period: float,
    ticks: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Tick every `period` seconds, forever (or `ticks` times)."""
    count = 0
    while ticks is None or count < ticks:
        if count:
            sleep(period)
        count += 1
        try:
            for change in service.tick():
                log.info("%s", change.describe())
        except WallpaperError as err:
            log.warning("%s", err)
        # The loop must outlive surprises (e.g. a COM error while a monitor
        # is switching); they are logged in full and the next tick retries.
        except Exception:  # pylint: disable=broad-exception-caught
            log.exception("Tick failed")


# -- arguments ---------------------------------------------------------------


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


def positive_seconds(value: str) -> float:
    seconds = float(value)
    if seconds <= 0:
        raise argparse.ArgumentTypeError("must be more than 0")
    return seconds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wallpaper", description="Per-monitor wallpaper management"
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        help=f"playlist config file (default: {paths.config_file()})",
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

    run_parser = sub.add_parser("run", help="rotate wallpapers (foreground)")
    run_parser.add_argument(
        "--poll",
        type=positive_seconds,
        default=5.0,
        help="seconds between checks (default: 5)",
    )
    run_parser.add_argument("--ticks", type=int, default=None, help=argparse.SUPPRESS)
    run_parser.set_defaults(handler=cmd_run)

    status_parser = sub.add_parser("status", help="show playlists and their state")
    status_parser.set_defaults(handler=cmd_status)

    target_help = "playlist name, or a monitor it drives; optional with one playlist"
    for name, handler, help_text in (
        ("next", cmd_next, "show the next image"),
        ("prev", cmd_prev, "go back in history"),
        ("fav", cmd_fav, "add the current image to favourites"),
        ("remove", cmd_remove, "list the current image for removal, show the next"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("target", nargs="?", help=target_help)
        command.set_defaults(handler=handler)
    return parser


def main(
    argv: Sequence[str] | None = None,
    backend_factory: BackendFactory = get_backend,
    presence_factory: PresenceFactory = get_presence,
) -> int:
    args = build_parser().parse_args(argv)
    handler: Callable[[Context, argparse.Namespace], int] = args.handler
    try:
        ctx = Context(
            backend=backend_factory(),
            presence_factory=presence_factory,
            config_path=args.config or paths.config_file(),
        )
        return handler(ctx, args)
    except WallpaperError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
