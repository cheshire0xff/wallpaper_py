"""
Playlist commands run on the service: a `Request` in, a result (or an
error) out, as the data models of `protocol.py`; no text. (`reload` is not
one: it replaces the service, so the app runs it; see app/worker.py.) The CLI formats
its output from them, the tray and the windows show them.

`execute()` runs a command on the app's service thread, whether it came over
the socket (CLI, UI process) or from the tray. Besides the reply it returns
the wallpaper change the command made, if any, so the app announces it like
a tick's. Qt-free, like the core.

`overview()` is the small snapshot the tray and the windows draw from.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, NamedTuple

from .core import paths
from .core.backend import Backend
from .core.config import ConfigError, PlaylistConfig, load_config
from .core.model import (
    ImageLoadError,
    Monitor,
    MonitorNotAttachedError,
    WallpaperError,
)
from .core.presence import Presence
from .core.selection import MonitorSelectionError
from .core.service import (
    Change,
    NoImagesError,
    NothingShownError,
    PlaylistService,
    PlaylistStatus,
    UnknownTargetError,
)
from .core.store import Store
from .protocol import (
    TARGET_COMMANDS,
    Command,
    DesktopMonitor,
    ErrorKind,
    Geometry,
    ErrorReply,
    HoldResult,
    MarkResult,
    MonitorView,
    NextResult,
    Overview,
    PickRequest,
    PickResult,
    PlaylistDetails,
    PlaylistView,
    PrevResult,
    RemoveResult,
    Reply,
    ResumeResult,
    ServiceRequest,
    StatusResult,
)


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


def execute(service: PlaylistService, request: ServiceRequest) -> Outcome:
    try:
        if request.command == "status":
            return Outcome(_status(service))
        playlist = service.find_playlist(request.target)
        if isinstance(request, PickRequest):
            change = service.pick(playlist, request.image)
            return Outcome(PickResult(playlist=playlist.name, change=change), change)
        return _TARGET_HANDLERS[request.command](service, playlist)
    except WallpaperError as err:
        return Outcome(error_reply(err))


# Most specific first: the first match names the kind.
_ERROR_KINDS: tuple[tuple[type[Exception], ErrorKind], ...] = (
    (ConfigError, "config_error"),
    (MonitorNotAttachedError, "not_attached"),
    (UnknownTargetError, "unknown_target"),
    (MonitorSelectionError, "unknown_target"),
    (NoImagesError, "no_images"),
    (NothingShownError, "nothing_shown"),
    (ImageLoadError, "bad_image"),
    (WallpaperError, "failed"),
)


def error_reply(err: Exception) -> ErrorReply:
    """An expected failure keeps its message; anything else is a bug."""
    for error_type, kind in _ERROR_KINDS:
        if isinstance(err, error_type):
            return ErrorReply.make(kind, err)
    return ErrorReply.make("internal", f"Unexpected error: {err}")


def _status(service: PlaylistService) -> StatusResult:
    return StatusResult(
        playlists=tuple(
            _details(service, status) for status in service.status(upcoming=3)
        ),
        paused=service.pause_reason(),
        warnings=tuple(service.config.warnings()),
    )


def _next(service: PlaylistService, playlist: PlaylistConfig) -> Outcome:
    change = service.next(playlist)
    return Outcome(NextResult(playlist=playlist.name, change=change), change)


def _prev(service: PlaylistService, playlist: PlaylistConfig) -> Outcome:
    change = service.previous(playlist)
    return Outcome(PrevResult(playlist=playlist.name, change=change), change)


def _fav(service: PlaylistService, playlist: PlaylistConfig) -> Outcome:
    image, added = service.favourite(playlist)
    return Outcome(_mark("fav", service, playlist, image, added))


def _unfav(service: PlaylistService, playlist: PlaylistConfig) -> Outcome:
    image, removed = service.unfavourite(playlist)
    return Outcome(_mark("unfav", service, playlist, image, removed))


def _mark(
    command: Literal["fav", "unfav"],
    service: PlaylistService,
    playlist: PlaylistConfig,
    image: Path,
    changed: bool,
) -> MarkResult:
    return MarkResult(
        command=command,
        playlist=playlist.name,
        image=image,
        list_file=service.favourites.file,
        changed=changed,
    )


def _remove(service: PlaylistService, playlist: PlaylistConfig) -> Outcome:
    image, change = service.remove(playlist)
    result = RemoveResult(
        playlist=playlist.name,
        image=image,
        list_file=service.to_remove.file,
        change=change,
    )
    return Outcome(result, change)


def _hold(service: PlaylistService, playlist: PlaylistConfig) -> Outcome:
    changed = service.hold(playlist)
    return Outcome(HoldResult(playlist=playlist.name, held=True, changed=changed))


def _resume(service: PlaylistService, playlist: PlaylistConfig) -> Outcome:
    changed = service.resume(playlist)
    return Outcome(ResumeResult(playlist=playlist.name, held=False, changed=changed))


_Handler = Callable[[PlaylistService, PlaylistConfig], Outcome]
_TARGET_HANDLERS: dict[Command, _Handler] = {
    "next": _next,
    "prev": _prev,
    "fav": _fav,
    "unfav": _unfav,
    "remove": _remove,
    "hold": _hold,
    "resume": _resume,
}
assert set(_TARGET_HANDLERS) == set(TARGET_COMMANDS)


# -- overview and status ---------------------------------------------------------


def overview(service: PlaylistService) -> Overview:
    monitors = service.monitors()
    statuses = service.status(upcoming=0, monitors=monitors)
    return Overview(
        playlists=tuple(_view(service, s) for s in statuses),
        monitors=tuple(_desktop_monitor(m) for m in monitors),
        paused=service.pause_reason(),
    )


def _desktop_monitor(monitor: Monitor) -> DesktopMonitor:
    rect = monitor.rect
    geometry = (
        None
        if rect is None
        else Geometry(x=rect.x1, y=rect.y1, width=rect.width, height=rect.height)
    )
    return DesktopMonitor(id=monitor.id, name=monitor.name, geometry=geometry)


def _view(service: PlaylistService, status: PlaylistStatus) -> PlaylistView:
    monitors = tuple(
        MonitorView(
            selector=m.selector,
            name=m.monitor.name if m.monitor else m.selector,
            attached=m.monitor.attached if m.monitor else None,
            id=m.monitor.id if m.monitor else None,
        )
        for m in status.members
    )
    current = status.current
    return PlaylistView(
        name=status.playlist.name,
        monitors=monitors,
        current=current,
        favourite=current is not None and current in service.favourites,
        held=status.held,
        manual=status.playlist.manual,
    )


def _details(service: PlaylistService, status: PlaylistStatus) -> PlaylistDetails:
    playlist = status.playlist
    interval = playlist.interval
    return PlaylistDetails(
        **dict(_view(service, status)),
        directory=playlist.directory,
        order=playlist.order,
        interval=interval,
        mode=playlist.mode,
        next_in_seconds=None if interval is None else interval - status.elapsed,
        upcoming=status.upcoming,
    )
