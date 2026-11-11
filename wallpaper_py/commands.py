"""
Playlist commands as data: a `Request` in, a `Reply` (exit code and text) out.

`execute()` runs a command on the app's service thread, whether it came over
the socket (from the CLI) or from the tray. Besides the reply it returns the
wallpaper change the command made, if any, so the app announces it like a
tick's. Qt-free, like the core.

`overview()` is the small snapshot the tray draws its menu from.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NamedTuple, get_args

from .core import paths
from .core.backend import Backend
from .core.config import PlaylistConfig, format_duration, load_config
from .core.model import WallpaperError
from .core.presence import Presence
from .core.service import Change, PlaylistService, PlaylistStatus
from .core.store import Store

Command = Literal["status", "next", "prev", "fav", "unfav", "remove"]
COMMANDS: tuple[Command, ...] = get_args(Command)
# Commands that act on a target (playlist or monitor): all but `status`.
TARGET_COMMANDS: tuple[Command, ...] = COMMANDS[1:]


@dataclass(frozen=True)
class Request:
    command: Command
    target: str | None = None  # playlist name, or a monitor it drives

    def __post_init__(self) -> None:
        if self.command not in COMMANDS:
            raise ValueError(f"Unknown command {self.command!r}")


@dataclass(frozen=True)
class Reply:
    code: int  # process exit code
    out: str = ""
    err: str = ""

    @classmethod
    def error(cls, message: object) -> Reply:
        return cls(1, err=f"error: {message}")


class Outcome(NamedTuple):
    """What a command did: its reply, and the wallpaper change it made."""

    reply: Reply
    change: Change | None = None


@contextmanager
def open_service(
    config_path: Path, backend: Backend, presence: Presence, tick_period: float = 5.0
) -> Iterator[PlaylistService]:
    """A service on the per-user state database; closes the database after."""
    config = load_config(config_path, default_dir=paths.data_dir())
    with Store(paths.state_db()) as store:
        yield PlaylistService(
            config,
            backend,
            presence,
            store,
            paths.render_dir(),
            tick_period=tick_period,
        )


def execute(service: PlaylistService, request: Request) -> Outcome:
    try:
        if request.command == "status":
            return _status(service)
        playlist = service.find_playlist(request.target)
        return _TARGET_HANDLERS[request.command](service, playlist)
    except WallpaperError as err:
        return Outcome(Reply.error(err))


def _status(service: PlaylistService) -> Outcome:
    warnings = [f"Warning: {w}" for w in service.config.warnings()]
    status = format_status(service.status(), service.pause_reason())
    return Outcome(Reply(0, out=status, err="\n".join(warnings)))


def _next(service: PlaylistService, playlist: PlaylistConfig) -> Outcome:
    change = service.next(playlist)
    return Outcome(Reply(0, out=change.describe()), change)


def _prev(service: PlaylistService, playlist: PlaylistConfig) -> Outcome:
    change = service.previous(playlist)
    if change is None:
        return Outcome(Reply(0, out=f"{playlist.name}: no earlier image"))
    return Outcome(Reply(0, out=change.describe()), change)


def _fav(service: PlaylistService, playlist: PlaylistConfig) -> Outcome:
    image, added = service.favourite(playlist)
    if added:
        return Outcome(Reply(0, out=f"Added {image} to {service.favourites.file}"))
    return Outcome(Reply(0, out=f"{image.name} is already a favourite"))


def _unfav(service: PlaylistService, playlist: PlaylistConfig) -> Outcome:
    image, removed = service.unfavourite(playlist)
    if removed:
        return Outcome(Reply(0, out=f"Removed {image} from {service.favourites.file}"))
    return Outcome(Reply(0, out=f"{image.name} is not a favourite"))


def _remove(service: PlaylistService, playlist: PlaylistConfig) -> Outcome:
    image, change = service.remove(playlist)
    lines = [f"Listed {image} in {service.to_remove.file} (not deleted)"]
    if change:
        lines.append(change.describe())
    else:
        lines.append(f"{playlist.name}: changes once a monitor is attached")
    return Outcome(Reply(0, out="\n".join(lines)), change)


_Handler = Callable[[PlaylistService, PlaylistConfig], Outcome]
_TARGET_HANDLERS: dict[Command, _Handler] = {
    "next": _next,
    "prev": _prev,
    "fav": _fav,
    "unfav": _unfav,
    "remove": _remove,
}
assert set(_TARGET_HANDLERS) == set(TARGET_COMMANDS)


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


# -- overview (what the tray shows) ------------------------------------------


@dataclass(frozen=True)
class MonitorView:
    name: str  # the monitor's name if listed, else the selector from the config
    attached: bool | None  # None: not listed right now


@dataclass(frozen=True)
class PlaylistView:
    name: str
    monitors: tuple[MonitorView, ...]
    current: Path | None
    favourite: bool  # the current image is in favourites

    @property
    def attached(self) -> bool:
        """At least one member is attached, so next / previous can work."""
        return any(m.attached for m in self.monitors)


@dataclass(frozen=True)
class Overview:
    playlists: tuple[PlaylistView, ...] = ()
    paused: str | None = None
    error: str | None = None  # the service could not start (e.g. bad config)


def overview(service: PlaylistService) -> Overview:
    views = []
    for status in service.status(upcoming=0):
        monitors = tuple(
            MonitorView(
                m.monitor.name if m.monitor else m.selector,
                m.monitor.attached if m.monitor else None,
            )
            for m in status.members
        )
        current = status.current
        favourite = current is not None and current in service.favourites
        views.append(PlaylistView(status.playlist.name, monitors, current, favourite))
    return Overview(tuple(views), paused=service.pause_reason())
