"""
Command-line interface.

Monitor commands (`list`, `set`) work on their own. Playlist commands
(`status`, `next`, ...) always run in the app, so they never overlap its
ticks: if it isn't running, it is started in the tray first. The app
replies with data (protocol.py); the text printed here is made here. `run`
and `tray` start the app, `ui` the windows (see wallpaper_py.app).

Which config: `run` / `tray` (and an app started for a command) use
--config, else the default file. A command sends --config only if it was
given: without it, it means whatever config the running app uses.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from .backends import get_backend, get_presence
from .core import paths
from .core.backend import Backend
from .core.config import format_duration
from .core.model import ImageMode, Monitor, WallpaperError
from .core.presence import Presence
from .core.selection import resolve_monitor
from .core.setter import apply_wallpaper
from .protocol import (
    TARGET_COMMANDS,
    Command,
    CommandRequest,
    Envelope,
    ErrorReply,
    HoldResult,
    MarkResult,
    NextResult,
    PickRequest,
    PickResult,
    PlaylistDetails,
    PrevResult,
    ReloadRequest,
    ReloadResult,
    RemoveResult,
    Reply,
    Request,
    ResumeResult,
    StatusResult,
)

# The pages `wallpaper ui` opens the window on (app/ui/launcher.py
# PAGE_NAMES; not imported from there, which would load Qt for every command).
PAGES = ("home", "library", "settings")

BackendFactory = Callable[[], Backend]
PresenceFactory = Callable[[], Presence]
# Runs a request in the app (starting it if needed) and returns its reply.
# The config: the one the user named, or None (whatever the app runs).
AppClient = Callable[[CommandRequest, Path | None], Reply]
# The command line that starts the app for a config file.
AppCommand = Callable[[Path], list[str]]

log = logging.getLogger(__name__)

START_TIMEOUT_S = 10.0  # for a started app to answer
RETRY_INTERVAL_S = 0.2


def send_to_app(
    request: CommandRequest,
    config_path: Path | None,
    app_command: AppCommand | None = None,
) -> Reply:
    """
    Run `request` in the app. If none is running, start it in the tray
    (`app_command`, by default the autostart command; on `config_path`, else
    the default config) and retry until it answers. Two CLIs may race to
    start it: the instance lock keeps one app, and both reach it.
    `config_path` None: the app may run any config.
    """
    # pylint: disable=import-outside-toplevel
    from .app import ipc  # Qt is imported only for playlist commands
    from .app.processes import autostart_command, start_detached

    name = ipc.server_name(paths.data_dir())
    envelope = Envelope(request=request, config=config_path)
    log.info("Asking the running app (socket %s)", name)
    reply = ipc.send(name, envelope)
    if reply is not None:
        log.info("The running app answered")
        return reply
    log.info("No app is running: starting one")
    start_detached(
        (app_command or autostart_command)(config_path or paths.config_file())
    )
    started = time.monotonic()
    while time.monotonic() < started + START_TIMEOUT_S:
        time.sleep(RETRY_INTERVAL_S)
        reply = ipc.send(name, envelope)
        if reply is not None:
            log.info("The new app answered after %.1f s", time.monotonic() - started)
            return reply
    raise WallpaperError(f"could not start wallpaper_py; see {paths.log_file()}")


@dataclass
class Context:
    backend_factory: BackendFactory
    presence_factory: PresenceFactory
    config: Path | None  # --config, if given
    app_client: AppClient
    _backend: Backend | None = field(default=None, init=False)

    @property
    def config_path(self) -> Path:
        """The config to start the app on: --config, else the default."""
        return self.config or paths.config_file()

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


class Text(NamedTuple):
    """What a playlist command prints, and its exit code."""

    code: int
    out: str = ""
    err: str = ""


def cmd_playlist(ctx: Context, args: argparse.Namespace) -> int:
    """status, pick, reload and the TARGET_COMMANDS: always in the app."""
    target: str | None = getattr(args, "target", None)
    request: CommandRequest
    if args.command == "pick":
        request = PickRequest(target=target, image=args.image)
    elif args.command == "reload":
        request = ReloadRequest()
    else:
        command: Command = args.command  # a subparser name from Command
        request = Request(command=command, target=target)
    reply = ctx.app_client(request, ctx.config)
    log.info("Reply: %s", reply.model_dump_json())
    text = format_reply(reply)
    if text.out:
        print(text.out)
    if text.err:
        print(text.err, file=sys.stderr)
    return text.code


def format_reply(reply: Reply) -> Text:
    # One case per reply model.
    # pylint: disable=too-many-return-statements,too-many-branches
    match reply:
        case ErrorReply():
            return Text(1, err=f"error: {reply.error.message}")
        case StatusResult():
            warnings = "\n".join(f"Warning: {w}" for w in reply.warnings)
            return Text(0, format_status(reply.playlists, reply.paused), warnings)
        case NextResult() | PickResult():
            return Text(0, reply.change.describe())
        case PrevResult():
            if reply.change is None:
                return Text(0, f"{reply.playlist}: no earlier image")
            return Text(0, reply.change.describe())
        case MarkResult(command="fav"):
            if reply.changed:
                return Text(0, f"Added {reply.image} to {reply.list_file}")
            return Text(0, f"{reply.image.name} is already a favourite")
        case MarkResult():
            if reply.changed:
                return Text(0, f"Removed {reply.image} from {reply.list_file}")
            return Text(0, f"{reply.image.name} is not a favourite")
        case RemoveResult():
            lines = [f"Listed {reply.image} in {reply.list_file} (not deleted)"]
            if reply.change:
                lines.append(reply.change.describe())
            else:
                lines.append(f"{reply.playlist}: changes once a monitor is attached")
            return Text(0, "\n".join(lines))
        case HoldResult() | ResumeResult():
            return Text(0, _hold_text(reply))
        case ReloadResult():
            warnings = "\n".join(f"Warning: {w}" for w in reply.warnings)
            return Text(0, "Config reloaded", warnings)
    raise AssertionError(f"Unhandled reply {reply!r}")


def format_status(playlists: Sequence[PlaylistDetails], paused: str | None) -> str:
    lines: list[str] = []
    for playlist in playlists:
        lines.append(f"{playlist.name}  ({', '.join(_traits(playlist))})")
        members = []
        for monitor in playlist.monitors:
            if monitor.attached is None:
                state = "not found"
            else:
                state = "attached" if monitor.attached else "detached"
            members.append(f"{monitor.selector} ({state})")
        lines.append(f"  monitors:  {', '.join(members)}")
        lines.append(f"  directory: {playlist.directory}")
        lines.append(f"  current:   {playlist.current or '-'}")
        lines.append(f"  next in:   {_next_in(playlist)}")
        upcoming = ", ".join(p.name for p in playlist.upcoming) or "-"
        lines.append(f"  upcoming:  {upcoming}")
    lines.append(f"Rotation paused: {paused}" if paused else "Rotation active")
    return "\n".join(lines)


def _hold_text(reply: HoldResult | ResumeResult) -> str:
    name = reply.playlist
    if reply.held:
        if reply.changed:
            return (
                f"{name}: held; only next / prev / pick change it until "
                f"`wallpaper resume {name}`"
            )
        return f"{name} is already held"
    return f"{name}: rotating again" if reply.changed else f"{name} is not held"


def _traits(playlist: PlaylistDetails) -> list[str]:
    """Order, interval, mode, and held if it is."""
    if playlist.interval is None:
        rotation = "manual"
    else:
        rotation = f"every {format_duration(playlist.interval)}"
    mode = playlist.mode.value if playlist.mode else "as is"
    traits = [playlist.order.value, rotation, mode]
    if playlist.held:
        traits.append("held")
    return traits


def _next_in(playlist: PlaylistDetails) -> str:
    if playlist.next_in_seconds is None:
        return "only by next / prev / pick (manual)"
    left = f"{format_duration(playlist.next_in_seconds)} of active time"
    return f"held ({left} once resumed)" if playlist.held else left


def cmd_app(ctx: Context, args: argparse.Namespace) -> int:
    """`run` (headless, in this terminal) and `tray`."""
    # pylint: disable=import-outside-toplevel
    from .app.processes import setup_logging
    from .app.runtime import AppOptions, Platform, run_app

    tray = args.command == "tray"
    setup_logging(paths.log_file() if tray else None)
    options = AppOptions(
        ctx.config_path,
        tray=tray,
        tick_period=args.poll,
        quit_after_ticks=args.ticks,
    )
    return run_app(options, Platform(ctx.backend_factory, ctx.presence_factory))


def cmd_ui(ctx: Context, args: argparse.Namespace) -> int:
    """`ui [home|library|settings]`: the window, in its own process."""
    # pylint: disable=import-outside-toplevel
    from .app.processes import setup_logging
    from .app.ui.process import UiOptions, run_ui

    setup_logging(paths.ui_log_file())
    try:
        return run_ui(UiOptions(ctx.config, args.page))
    except WallpaperError as err:  # started by the tray, nobody sees stderr
        logging.getLogger(__name__).error("%s", err)
        raise


# -- arguments ---------------------------------------------------------------


def absolute_path(value: str) -> Path:
    """Absolute (the app runs in another directory); symlinks are kept."""
    return Path(os.path.abspath(value))


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
        help=(
            f"playlist config file (default: {paths.config_file()}); for "
            f"commands to the running app, only needed to insist on one"
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help=(
            "log what the command does to stderr: whether it reached the "
            "running app or started one, and the raw reply (run, tray and ui "
            "always log)"
        ),
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

    ui_parser = sub.add_parser(
        "ui", help="open the window on a page (in the running UI process, or start one)"
    )
    ui_parser.add_argument(
        "page", nargs="?", choices=PAGES, default="home", help="default: home"
    )
    ui_parser.set_defaults(handler=cmd_ui)

    status_parser = sub.add_parser("status", help="show playlists and their state")
    status_parser.set_defaults(handler=cmd_playlist)
    sub.add_parser(
        "reload", help="read the config file again (after editing it)"
    ).set_defaults(handler=cmd_playlist)

    target_help = "playlist name, or a monitor it drives; optional with one playlist"
    helps = {
        "next": "show the next image",
        "prev": "go back in history",
        "fav": "add the current image to favourites",
        "unfav": "remove the current image from favourites",
        "remove": "list the current image for removal, show the next",
        "hold": "stop rotating until resume (next / prev / pick still work)",
        "resume": "rotate a held playlist again",
    }
    for name in TARGET_COMMANDS:
        command = sub.add_parser(name, help=helps[name])
        command.add_argument("target", nargs="?", help=target_help)
        command.set_defaults(handler=cmd_playlist)

    pick = sub.add_parser(
        "pick", help="show an image now, from any directory (prev goes back)"
    )
    pick.add_argument("image", type=absolute_path, help="image file")
    pick.add_argument("target", nargs="?", help=target_help)
    pick.set_defaults(handler=cmd_playlist)
    return parser


def main(
    argv: Sequence[str] | None = None,
    backend_factory: BackendFactory = get_backend,
    presence_factory: PresenceFactory = get_presence,
    app_client: AppClient = send_to_app,
) -> int:
    args = build_parser().parse_args(argv)
    handler: Callable[[Context, argparse.Namespace], int] = args.handler
    if args.verbose and handler not in (cmd_app, cmd_ui):  # those set up their own
        # pylint: disable=import-outside-toplevel
        from .app.processes import setup_logging

        setup_logging(None)
    ctx = Context(
        backend_factory=backend_factory,
        presence_factory=presence_factory,
        config=args.config,
        app_client=app_client,
    )
    try:
        return handler(ctx, args)
    except WallpaperError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
