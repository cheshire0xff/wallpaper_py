"""
Playlist commands run on the service: a `Request` in, a result (or an
error) out, as the data models of `protocol.py`; no text. (`reload` is not
one: it replaces the service, so the app runs it; see app/worker.py.) The CLI formats
its output from them, the tray and the windows show them.

`execute()` runs a command on the app's service thread, whether it came over
the socket (CLI, UI process) or from the tray. Besides the reply it returns
the wallpaper change the command made, if any, so the app announces it like
a tick's. Qt-free, like the core.

`open_service()` only reads: nothing is dropped when a config is loaded, so
a briefly missing file or a playlist taken out keeps its state. `prune`
drops the state of playlist ids no longer in the config, on request.

`overview()` is the small snapshot the tray and the windows draw from.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, NamedTuple

from .core import paths
from .core.backend import Backend
from .core.config import ConfigError, PlaylistConfig, load_config
from .core.marks import MarkFileBusyError
from .core.model import (
    AppError,
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
    PruneRequest,
    PruneResult,
    RemoveResult,
    Reply,
    ResumeResult,
    ServiceRequest,
    StatusResult,
)

log = logging.getLogger(__name__)


class Outcome(NamedTuple):
    """What a command did: its reply, and the wallpaper change it made."""

    reply: Reply
    change: Change | None = None


@contextmanager
def open_service(
    config_path: Path, backend: Backend, presence: Presence, tick_period: float = 5.0
) -> Iterator[PlaylistService]:
    """
    A service on the config's state database; closes the database after.
    It only reads: the state of playlists no longer in the config stays
    (a missing file, or a playlist taken out for a while, loses nothing)
    until the `prune` command drops it.

    Raises:
        ConfigError: the file, or a list file.
    """
    config = load_config(config_path, default_dir=paths.data_dir())
    with Store(paths.state_db(config_path)) as store:
        service = PlaylistService(
            config,
            backend,
            presence,
            store,
            paths.render_dir(),
            tick_period=tick_period,
        )
        yield service


def execute(service: PlaylistService, request: ServiceRequest) -> Outcome:
    """
    An expected failure is an error reply, except a broken config.

    Raises:
        ConfigError: the config or a list file went bad (e.g. saved with a
            byte order mark). The service can't go on: the caller stops it,
            and answers with the error until a reload (see app/worker.py).
    """
    try:
        if isinstance(request, PruneRequest):
            return Outcome(_prune(service))
        if request.command == "status":
            return Outcome(_status(service))
        playlist = service.find_playlist(request.target)
        if isinstance(request, PickRequest):
            change = service.pick(playlist, request.image)
            return Outcome(PickResult(playlist=playlist.name, change=change), change)
        return _TARGET_HANDLERS[request.command](service, playlist)
    except WallpaperError as err:  # not a ConfigError: that one goes up
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
    (AppError, "failed"),
)


def error_reply(err: Exception) -> ErrorReply:
    """An expected failure keeps its message; anything else is a bug."""
    for error_type, kind in _ERROR_KINDS:
        if isinstance(err, error_type):
            return ErrorReply.make(kind, err)
    return ErrorReply.make("internal", f"Unexpected error: {err}")


def _prune(service: PlaylistService) -> PruneResult:
    dropped = service.prune_state()
    for playlist_id in dropped:
        log.info("Dropped the state of playlist id %s: not in the config", playlist_id)
    return PruneResult(dropped=tuple(dropped))


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
            # As the desktop spells it if listed (as `Overview.monitors`
            # does), else as the config does.
            id=m.monitor.id if m.monitor else m.monitor_id,
            name=m.monitor.name if m.monitor else str(m.monitor_id),
            attached=m.monitor.attached if m.monitor else None,
        )
        for m in status.members
    )
    current = status.current
    return PlaylistView(
        name=status.playlist.name,
        monitors=monitors,
        current=current,
        favourite=_favourite(service, current),
        held=status.held,
        manual=status.playlist.manual,
    )


def _favourite(service: PlaylistService, current: Path | None) -> bool | None:
    """None: favourites.txt was never read (busy), so it isn't known."""
    if current is None:
        return False
    try:
        return current in service.favourites
    except MarkFileBusyError:
        return None


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
