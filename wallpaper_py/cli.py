"""
Command-line interface.

Monitor commands (`list`, `set`) work on their own. Playlist commands
(`status`, `next`, ...) always run in the app, so they never overlap its
ticks: if it isn't running, it is started in the tray first. `run` and
`tray` start the app (see wallpaper_py.app).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .backends import get_backend, get_presence
from .commands import TARGET_COMMANDS, Command, Reply, Request
from .core import paths
from .core.backend import Backend
from .core.model import ImageMode, Monitor, WallpaperError
from .core.presence import Presence
from .core.selection import resolve_monitor
from .core.setter import apply_wallpaper

BackendFactory = Callable[[], Backend]
PresenceFactory = Callable[[], Presence]
# Runs a request in the app (starting it if needed) and returns its reply.
AppClient = Callable[[Request, Path], Reply]
# The command line that starts the app for a config file.
AppCommand = Callable[[Path], list[str]]

START_TIMEOUT_S = 10.0  # for a started app to answer
RETRY_INTERVAL_S = 0.2


def send_to_app(
    request: Request, config_path: Path, app_command: AppCommand | None = None
) -> Reply:
    """
    Run `request` in the app. If none is running, start it in the tray
    (`app_command`, by default the autostart command) and retry until it
    answers. Two CLIs may race to start it: the instance lock keeps one app,
    and both reach it.
    """
    # pylint: disable=import-outside-toplevel
    from .app import ipc  # Qt is imported only for playlist commands

    name = ipc.server_name(paths.data_dir())
    envelope = ipc.Envelope(request=request, config=config_path)
    reply = ipc.send(name, envelope)
    if reply is not None:
        return reply
    if app_command is None:
        from .app.runtime import autostart_command

        app_command = autostart_command
    start_detached(app_command(config_path))
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        time.sleep(RETRY_INTERVAL_S)
        reply = ipc.send(name, envelope)
        if reply is not None:
            return reply
    raise WallpaperError(f"could not start wallpaper_py; see {paths.log_file()}")


def start_detached(command: Sequence[str]) -> None:
    """Start a process that outlives this one, without a console or our pipes."""
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        try:  # leave our job object too, so closing the terminal can't end it
            _popen(command, flags | subprocess.CREATE_BREAKAWAY_FROM_JOB)
        except OSError:  # the job doesn't allow that
            _popen(command, flags)
    else:
        _popen(command)


def _popen(command: Sequence[str], creationflags: int = 0) -> None:
    # Never waited for: it runs on after this process exits.
    subprocess.Popen(  # pylint: disable=consider-using-with
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,  # Windows only
        start_new_session=True,  # POSIX only: not ended with our session
    )


@dataclass
class Context:
    backend_factory: BackendFactory
    presence_factory: PresenceFactory
    config_path: Path
    app_client: AppClient
    _backend: Backend | None = field(default=None, init=False)

    @property
    def backend(self) -> Backend:
        """Created on first use: commands sent to the app don't need one."""
        if self._backend is None:
            self._backend = self.backend_factory()
        return self._backend


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


def cmd_playlist(ctx: Context, args: argparse.Namespace) -> int:
    """status / next / prev / fav / unfav / remove: always in the app."""
    command: Command = args.command  # a subparser name from COMMANDS
    reply = ctx.app_client(
        Request(command, getattr(args, "target", None)), ctx.config_path
    )
    if reply.out:
        print(reply.out)
    if reply.err:
        print(reply.err, file=sys.stderr)
    return reply.code


def cmd_app(ctx: Context, args: argparse.Namespace) -> int:
    """`run` (headless, in this terminal) and `tray`."""
    # pylint: disable=import-outside-toplevel
    from .app.runtime import AppOptions, Platform, run_app, setup_logging

    tray = args.command == "tray"
    setup_logging(to_file=tray)
    options = AppOptions(
        ctx.config_path,
        tray=tray,
        tick_period=args.poll,
        quit_after_ticks=args.ticks,
    )
    return run_app(options, Platform(ctx.backend_factory, ctx.presence_factory))


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

    run_parser = sub.add_parser(
        "run", help="run the app in this terminal, without a tray icon (Ctrl+C stops)"
    )
    tray_parser = sub.add_parser(
        "tray", help="run the app with a tray icon (what starts at login)"
    )
    for app_parser, poll_help in (
        (run_parser, "seconds between checks (default: 5)"),
        (tray_parser, argparse.SUPPRESS),
    ):
        app_parser.add_argument(
            "--poll", type=positive_seconds, default=5.0, help=poll_help
        )
        app_parser.add_argument(
            "--ticks", type=int, default=None, help=argparse.SUPPRESS
        )
        app_parser.set_defaults(handler=cmd_app)

    status_parser = sub.add_parser("status", help="show playlists and their state")
    status_parser.set_defaults(handler=cmd_playlist)

    target_help = "playlist name, or a monitor it drives; optional with one playlist"
    helps = {
        "next": "show the next image",
        "prev": "go back in history",
        "fav": "add the current image to favourites",
        "unfav": "remove the current image from favourites",
        "remove": "list the current image for removal, show the next",
    }
    for name in TARGET_COMMANDS:
        command = sub.add_parser(name, help=helps[name])
        command.add_argument("target", nargs="?", help=target_help)
        command.set_defaults(handler=cmd_playlist)
    return parser


def main(
    argv: Sequence[str] | None = None,
    backend_factory: BackendFactory = get_backend,
    presence_factory: PresenceFactory = get_presence,
    app_client: AppClient = send_to_app,
) -> int:
    args = build_parser().parse_args(argv)
    handler: Callable[[Context, argparse.Namespace], int] = args.handler
    ctx = Context(
        backend_factory=backend_factory,
        presence_factory=presence_factory,
        config_path=args.config or paths.config_file(),
        app_client=app_client,
    )
    try:
        return handler(ctx, args)
    except WallpaperError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
