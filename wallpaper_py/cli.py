"""
Command-line interface.

Monitor commands (`list`, `set`) work on their own. Playlist commands
(`status`, `next`, ...) run in the daemon, so they never overlap its ticks;
without one they fail with NOT_RUNNING: the CLI never starts the daemon and
never runs a command itself. Replies are data (protocol.py); the text printed
here is made here. `run` and `tray` start the daemon, `ui` the windows (see
wallpaper_py.app). The settings are edited in the window (`ui settings`).
`reset-cycle` asks first (it can't be undone), unless `--yes`. The list
commands (`fav`, `unfav`, `remove`, `unremove`) take the playlist's
current image, or `--image PATH`.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from .backends import get_backend, get_presence
from .core import paths
from .core.backend import Backend
from .core.model import AppError, ImageMode, Look, Monitor, WallpaperError
from .core.presence import Presence
from .core.selection import resolve_monitor
from .core.service import ListStats, PlaylistStats
from .core.settings import Order, format_ago, format_duration
from .core.setter import apply_wallpaper
from .protocol import (
    MARK_COMMANDS,
    MAX_UPCOMING,
    TARGET_COMMANDS,
    CommandRequest,
    Envelope,
    ErrorReply,
    HoldResult,
    MarkCommand,
    MarkRequest,
    MarkResult,
    NextResult,
    PickRequest,
    PickResult,
    PlaylistCommand,
    PlaylistDetails,
    PrevResult,
    RemoveResult,
    Reply,
    Request,
    ResetCycleRequest,
    ResetCycleResult,
    SaveSettingsResult,
    SettingsResult,
    StatsRequest,
    StatsResult,
    StatusRequest,
    StatusResult,
    UpcomingRequest,
    UpcomingResult,
)
from .texts import traits

TARGET_HELP = "playlist name, or a monitor it drives; optional with one playlist"

# The pages `wallpaper ui` opens the window on (app/ui/launcher.py
# PAGE_NAMES; not imported from there, which would load Qt for every command).
PAGES = ("home", "library", "settings")

BackendFactory = Callable[[], Backend]
PresenceFactory = Callable[[], Presence]
# Runs a request in the running daemon, and returns its reply.
DaemonClient = Callable[[CommandRequest], Reply]

log = logging.getLogger(__name__)

NOT_RUNNING = (
    "wallpaper_py isn't running, so nothing rotates; `wallpaper tray` starts it"
)


def send_to_daemon(request: CommandRequest) -> Reply:
    """Run `request` in the running daemon; without one, NOT_RUNNING."""
    # pylint: disable=import-outside-toplevel
    from .app import ipc  # Qt is imported only for playlist commands

    name = ipc.server_name(paths.data_dir())
    log.info("Asking the running daemon (socket %s)", name)
    reply = ipc.send(name, Envelope(request=request))
    if reply is None:
        raise WallpaperError(NOT_RUNNING)
    log.info("The running daemon answered")
    return reply


@dataclass
class Context:
    backend_factory: BackendFactory
    presence_factory: PresenceFactory
    daemon_client: DaemonClient
    _backend: Backend | None = field(default=None, init=False)

    @property
    def backend(self) -> Backend:
        """Created on first use: commands sent to the daemon don't need one."""
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
            lines.append(f"    size:      {r.size[0]}x{r.size[1]}")
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
        ctx.backend, monitor.id, args.image, Look(args.mode), paths.render_dir()
    )
    print(f"Set {args.image.name} on {monitor.name} (rendered: {used})")
    return 0


# -- playlists ---------------------------------------------------------------


class Text(NamedTuple):
    """What a playlist command prints, and its exit code."""

    code: int
    out: str = ""
    err: str = ""


def cmd_playlist(ctx: Context, args: argparse.Namespace) -> int:
    """
    status, pick, upcoming, stats, unremove and the TARGET_COMMANDS: in the
    daemon.
    """
    target: str | None = getattr(args, "target", None)
    request: CommandRequest
    if args.command == "pick":
        request = PickRequest(target=target, image=args.image)
    elif args.command == "stats":
        request = StatsRequest(target=target, rescan=args.rescan)
    elif args.command == "upcoming":
        request = UpcomingRequest(target=target, limit=args.n)
    elif args.command in MARK_COMMANDS:
        mark: MarkCommand = args.command
        request = MarkRequest(command=mark, target=target, image=args.image)
    elif args.command == "status":
        request = StatusRequest()
    else:
        command: PlaylistCommand = args.command  # a subparser name
        request = Request(command=command, target=target)
    return _print(ctx.daemon_client(request))


def cmd_reset_cycle(ctx: Context, args: argparse.Namespace) -> int:
    """Asks first, with the playlist's count, unless --yes."""
    if not args.yes:
        reply = ctx.daemon_client(StatsRequest(target=args.target))
        if not isinstance(reply, StatsResult):
            return _print(reply)
        stats = reply.playlists[0]
        # A sorted one has no cycle: nothing to ask, the daemon says so.
        if stats.order is Order.SHUFFLE and not _confirm(
            f"Start {stats.name}'s shuffle cycle over? All "
            f"{stats.seen + stats.unseen:,} images become unseen. [y/N] "
        ):
            print("Nothing changed")
            return 1
    request = ResetCycleRequest(target=args.target)
    return _print(ctx.daemon_client(request))


def _confirm(question: str) -> bool:
    try:
        return input(question).strip().lower() in ("y", "yes")
    except EOFError:  # no terminal to answer from
        print()
        return False


def _print(reply: Reply) -> int:
    """Print a command's reply; its exit code."""
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
        case MarkResult():
            return Text(0, _mark_text(reply))
        case RemoveResult():
            return Text(0, _remove_text(reply))
        case UpcomingResult():
            if not reply.images:
                return Text(0, f"{reply.playlist}: nothing to come")
            return Text(0, "\n".join(str(p) for p in reply.images))
        case HoldResult():
            return Text(0, _hold_text(reply))
        case StatsResult():
            return Text(0, format_stats(reply))
        case ResetCycleResult():
            return Text(
                0,
                f"{reply.playlist}: a new shuffle cycle; "
                f"all {reply.unseen:,} images unseen",
            )
        case SettingsResult() | SaveSettingsResult():  # for the window; as data
            return Text(0, reply.model_dump_json(indent=2))
    raise AssertionError(f"Unhandled reply {reply!r}")


def format_status(playlists: Sequence[PlaylistDetails], paused: str | None) -> str:
    lines: list[str] = []
    for playlist in playlists:
        lines.append(f"{playlist.name}  ({', '.join(traits(playlist))})")
        members = []
        for monitor in playlist.monitors:
            if monitor.attached is None:
                state = "not found"
            else:
                state = "attached" if monitor.attached else "detached"
            members.append(f"{monitor.name} ({state}, {monitor.look.mode.value})")
        lines.append(f"  monitors:  {', '.join(members)}")
        lines.append(f"  directory: {playlist.directory}")
        lines.append(f"  current:   {playlist.current or '-'}")
        lines.append(f"  next in:   {_next_in(playlist)}")
        upcoming = ", ".join(p.name for p in playlist.upcoming) or "-"
        lines.append(f"  upcoming:  {upcoming}")
    if paused:
        lines.append(f"Rotation paused: {paused}")
    else:
        lines.append("Rotation active")
    return "\n".join(lines)


def format_stats(reply: StatsResult) -> str:
    lines: list[str] = []
    for playlist in reply.playlists:
        lines += _playlist_stats(playlist)
    lines.append(_list_line("favourites", reply.favourites))
    lines.append(_list_line("to remove", reply.to_remove))
    return "\n".join(lines)


def _playlist_stats(stats: PlaylistStats) -> list[str]:
    width = len(f"{stats.images:,}")

    def row(label: str, count: int, text: str) -> str:
        return f"  {label:<9}{count:>{width},}  {text}"

    if stats.order is Order.SORTED:
        walk = f"this walk: at {stats.seen:,} of {stats.seen + stats.unseen:,}"
        seen = "the current image and those before it, in name order"
        unseen = "after the current image, in name order"
    else:
        walk = f"this cycle: {stats.percent_seen} % seen"
        seen = "shown in this shuffle cycle"
        unseen = "still to come before the next reshuffle"
    return [
        f"{stats.name}  ({stats.order.value}; {walk})",
        f"  folder:  {stats.directory} (scanned {format_ago(stats.scan_age)})",
        row("images:", stats.images, "in the folder"),
        row("seen:", stats.seen, seen),
        row("unseen:", stats.unseen, unseen),
        row("skipped:", stats.skipped, "listed to remove, or unreadable"),
        f"  here:    {stats.favourites_here:,} favourites, "
        f"{stats.to_remove_here:,} to remove",
    ]


def _list_line(label: str, stats: ListStats) -> str:
    return (
        f"{label}: {stats.file}: {stats.listed:,} listed, "
        f"{stats.available:,} available"
    )


def _mark_text(reply: MarkResult) -> str:
    if reply.changed:
        if reply.command == "fav":
            return f"Added {reply.image} to {reply.list_file}"
        return f"Removed {reply.image} from {reply.list_file}"
    match reply.command:
        case "fav":
            return f"{reply.image.name} is already a favourite"
        case "unfav":
            return f"{reply.image.name} is not a favourite"
        case "unremove":
            return f"{reply.image.name} is not listed for removal"


def _remove_text(reply: RemoveResult) -> str:
    if reply.changed:
        listed = f"Listed {reply.image} in {reply.list_file} (not deleted)"
    else:
        listed = f"{reply.image.name} is already listed for removal"
    if reply.change is not None:
        moved = reply.change.describe()
    elif reply.playlist is None:  # an image named: only listed
        moved = "A playlist showing it moves on within a few seconds"
    else:
        moved = f"{reply.playlist}: changes once a monitor is attached"
    return f"{listed}\n{moved}"


def _hold_text(reply: HoldResult) -> str:
    name = reply.playlist
    if reply.held:
        if reply.changed:
            return (
                f"{name}: held; only next / prev / pick change it until "
                f"`wallpaper resume {name}`"
            )
        return f"{name} is already held"
    return f"{name}: rotating again" if reply.changed else f"{name} is not held"


def _next_in(playlist: PlaylistDetails) -> str:
    if playlist.next_in_seconds is None:
        return "only by next / prev / pick (manual)"
    left = f"{format_duration(playlist.next_in_seconds)} of active time"
    return f"held ({left} once resumed)" if playlist.held else left


def cmd_daemon(ctx: Context, args: argparse.Namespace) -> int:
    """`run` (headless, in this terminal) and `tray`."""
    # pylint: disable=import-outside-toplevel
    from .app.processes import setup_logging
    from .app.runtime import DaemonOptions, Platform, run_daemon

    tray = args.command == "tray"
    setup_logging(paths.log_file() if tray else None)
    options = DaemonOptions(
        tray=tray,
        tick_period=args.poll,
        quit_after_ticks=args.ticks,
    )
    return run_daemon(options, Platform(ctx.backend_factory, ctx.presence_factory))


def cmd_ui(ctx: Context, args: argparse.Namespace) -> int:
    """`ui [home|library|settings]`: the window, in its own process."""
    del ctx  # the window talks to the daemon, which has the backend
    # pylint: disable=import-outside-toplevel
    from .app.processes import setup_logging
    from .app.ui.process import UiOptions, run_ui

    setup_logging(paths.ui_log_file())
    try:
        return run_ui(UiOptions(args.page))
    except AppError as err:  # started by the tray, nobody sees stderr
        logging.getLogger(__name__).error("%s", err)
        raise


# -- arguments ---------------------------------------------------------------


def absolute_path(value: str) -> Path:
    """Absolute (the daemon runs in another directory); symlinks are kept."""
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


def upcoming_count(value: str) -> int:
    try:
        count = int(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"not a whole number: {value}") from err
    if not 1 <= count <= MAX_UPCOMING:
        raise argparse.ArgumentTypeError(f"must be 1 to {MAX_UPCOMING}")
    return count


def positive_seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:  # nan and inf parse as floats
        raise argparse.ArgumentTypeError("must be a number of seconds, more than 0")
    return seconds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wallpaper", description="Per-monitor wallpaper management"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help=(
            "log what the command does to stderr: the daemon it asked and the "
            "raw reply (run, tray and ui always log)"
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
        default=ImageMode.FILL,
        help="fill (the default), fit or stretch; the image is rendered to "
        "the monitor's resolution",
    )
    set_parser.set_defaults(handler=cmd_set)

    run_parser = sub.add_parser(
        "run", help="run the app in this terminal, without a tray icon (Ctrl+C stops)"
    )
    tray_parser = sub.add_parser(
        "tray", help="run the app with a tray icon (what starts at login)"
    )
    for daemon_parser, poll_help in (
        (run_parser, "seconds between checks (default: 5)"),
        (tray_parser, argparse.SUPPRESS),
    ):
        daemon_parser.add_argument(
            "--poll", type=positive_seconds, default=5.0, help=poll_help
        )
        daemon_parser.add_argument(
            "--ticks", type=int, default=None, help=argparse.SUPPRESS
        )
        daemon_parser.set_defaults(handler=cmd_daemon)

    ui_parser = sub.add_parser(
        "ui", help="open the window on a page (in the running UI process, or start one)"
    )
    ui_parser.add_argument(
        "page", nargs="?", choices=PAGES, default="home", help="default: home"
    )
    ui_parser.set_defaults(handler=cmd_ui)

    status_parser = sub.add_parser("status", help="show playlists and their state")
    status_parser.set_defaults(handler=cmd_playlist)
    _add_playlist_commands(sub)
    _add_library_commands(sub)
    return parser


def _add_playlist_commands(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """The TARGET_COMMANDS, `unremove` and `pick`."""
    helps = {
        "next": "show the next image",
        "prev": "go back in history",
        "fav": "add the current image (or --image) to favourites",
        "unfav": "take the current image (or --image) off favourites",
        "remove": "list the current image for removal and show the next; "
        "with --image, only list that one",
        "hold": "stop rotating until resume (next / prev / pick still work)",
        "resume": "rotate a held playlist again",
    }
    for name in TARGET_COMMANDS:
        command = sub.add_parser(name, help=helps[name])
        if name in MARK_COMMANDS:
            either = command.add_mutually_exclusive_group()
            either.add_argument("target", nargs="?", help=TARGET_HELP)
            either.add_argument(
                "--image",
                type=absolute_path,
                help="this image instead of a playlist's current one (any path)",
            )
        else:
            command.add_argument("target", nargs="?", help=TARGET_HELP)
        command.set_defaults(handler=cmd_playlist)
    unremove = sub.add_parser("unremove", help="take an image off the to-remove list")
    unremove.add_argument(
        "--image", type=absolute_path, required=True, help="the image (any path)"
    )
    unremove.set_defaults(handler=cmd_playlist)

    pick = sub.add_parser(
        "pick", help="show an image now, from any directory (prev goes back)"
    )
    pick.add_argument("image", type=absolute_path, help="image file")
    pick.add_argument("target", nargs="?", help=TARGET_HELP)
    pick.set_defaults(handler=cmd_playlist)


def _add_library_commands(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """`upcoming`, `stats` and `reset-cycle`."""
    upcoming = sub.add_parser(
        "upcoming", help="the images a playlist shows next, in order (a path a line)"
    )
    upcoming.add_argument("target", nargs="?", help=TARGET_HELP)
    upcoming.add_argument(
        "-n",
        type=upcoming_count,
        default=10,
        help=f"how many (default: 10, at most {MAX_UPCOMING})",
    )
    upcoming.set_defaults(handler=cmd_playlist)

    stats = sub.add_parser(
        "stats",
        help="count each playlist's images: seen and unseen in this cycle, "
        "skipped; and the lists",
    )
    stats.add_argument(
        "target", nargs="?", help="playlist name, or a monitor it drives; default: all"
    )
    stats.add_argument(
        "--rescan",
        action="store_true",
        help="scan the folders again first (the app's scan is up to 5 min old)",
    )
    stats.set_defaults(handler=cmd_playlist)

    reset = sub.add_parser(
        "reset-cycle",
        help="start a shuffle playlist's cycle over: every image unseen again",
    )
    reset.add_argument("target", help="playlist name, or a monitor it drives")
    reset.add_argument("--yes", action="store_true", help="don't ask first")
    reset.set_defaults(handler=cmd_reset_cycle)


def main(
    argv: Sequence[str] | None = None,
    backend_factory: BackendFactory = get_backend,
    presence_factory: PresenceFactory = get_presence,
    daemon_client: DaemonClient = send_to_daemon,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Callable[[Context, argparse.Namespace], int] = args.handler
    if args.verbose and handler not in (cmd_daemon, cmd_ui):  # those set up their own
        # pylint: disable=import-outside-toplevel
        from .app.processes import setup_logging

        setup_logging(None)
    ctx = Context(
        backend_factory=backend_factory,
        presence_factory=presence_factory,
        daemon_client=daemon_client,
    )
    try:
        return handler(ctx, args)
    except AppError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
