"""
Playlist commands run on the service: a request in, a result (or an
error) out, as the data models of `protocol.py`; no text. The CLI formats
its output from them, the tray and the windows show them.

`execute()` runs a command on the daemon's service thread, whether it came over
the socket (CLI, window process) or from the tray. Besides the reply it returns
the wallpaper change the command made, if any, so the daemon announces it like
a tick's. Qt-free, like the core.

`open_service()` runs the service on the settings in the state database;
`settings` and `save_settings` read and write them. `stats` counts what
playlists pick up, `reset_cycle` starts a shuffle cycle over, `upcoming`
lists what comes next. The list commands (`fav`, `unfav`, `remove`,
`unremove`) take a playlist's current image or any image named.

`overview()` is the small snapshot the tray and the windows draw from.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

from .core import paths
from .core.backend import Backend
from .core.marks import MarkFileBusyError
from .core.model import (
    AppError,
    ConfigError,
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
from .core.settings import PlaylistSettings, SettingsError, StaleSettingsError
from .core.store import Store
from .protocol import (
    DesktopMonitor,
    ErrorKind,
    Geometry,
    ErrorReply,
    HoldResult,
    MarkRequest,
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
    Request,
    Reply,
    ResetCycleRequest,
    ResetCycleResult,
    SaveSettingsRequest,
    SaveSettingsResult,
    CommandRequest,
    SettingsRequest,
    SettingsResult,
    StatsRequest,
    StatsResult,
    StatusRequest,
    StatusResult,
    UpcomingRequest,
    UpcomingResult,
)


class Outcome(NamedTuple):
    """
    What a command did: its reply, the wallpaper change it made, and
    whether monitors wait to be rendered again (their playlist, look or
    folder changed): the daemon then ticks before it replies.
    """

    reply: Reply
    change: Change | None = None
    render: bool = False


@contextmanager
def open_service(
    backend: Backend, presence: Presence, tick_period: float = 5.0
) -> Iterator[PlaylistService]:
    """
    A service on the settings in the state database; closes it after.

    Raises:
        ConfigError: a list file can't be used.
    """
    with Store(paths.state_db()) as store:
        service = PlaylistService(
            backend,
            presence,
            store,
            paths.render_dir(),
            paths.data_dir(),
            tick_period=tick_period,
        )
        yield service


def execute(  # pylint: disable=too-many-return-statements
    service: PlaylistService, request: CommandRequest
) -> Outcome:
    """
    An expected failure is an error reply, except a broken list file.

    Raises:
        ConfigError: a list file went bad (e.g. saved with a byte order
            mark). The service can't go on: the caller stops it, and answers
            with the error until the file can be read (see app/worker.py).
    """
    try:
        match request:
            case StatusRequest():
                return Outcome(_status(service))
            case Request():
                return _playlist_command(service, request)
            case MarkRequest():
                return _mark_command(service, request)
            case PickRequest():
                playlist = service.find_playlist(request.target)
                change = service.pick(playlist, request.image)
                return Outcome(
                    PickResult(playlist=playlist.name, change=change), change
                )
            case UpcomingRequest():
                playlist = service.find_playlist(request.target)
                images = tuple(service.upcoming(playlist, request.limit))
                return Outcome(UpcomingResult(playlist=playlist.name, images=images))
            case StatsRequest():
                return Outcome(_stats(service, request))
            case ResetCycleRequest():
                playlist = service.find_playlist(request.target)
                unseen = service.reset_cycle(playlist)
                return Outcome(ResetCycleResult(playlist=playlist.name, unseen=unseen))
            case SettingsRequest():
                return Outcome(
                    SettingsResult(
                        settings=service.settings, revision=service.revision()
                    )
                )
            case SaveSettingsRequest():
                # Monitors whose settings changed are rendered by the caller's tick.
                return Outcome(_save_settings(service, request), render=True)
    except WallpaperError as err:  # not a ConfigError: that one goes up
        return Outcome(error_reply(err))


def _playlist_command(service: PlaylistService, request: Request) -> Outcome:
    """`next`, `prev`, `hold` and `resume` on one playlist."""
    playlist = service.find_playlist(request.target)
    match request.command:
        case "next":
            change = service.next(playlist)
            return Outcome(NextResult(playlist=playlist.name, change=change), change)
        case "prev":
            earlier = service.previous(playlist)
            return Outcome(PrevResult(playlist=playlist.name, change=earlier), earlier)
        case "hold":
            changed = service.hold(playlist)
            held = HoldResult(
                command="hold", playlist=playlist.name, held=True, changed=changed
            )
            return Outcome(held)
        case "resume":
            changed = service.resume(playlist)
            resumed = HoldResult(
                command="resume", playlist=playlist.name, held=False, changed=changed
            )
            return Outcome(resumed)


# Most specific first: the first match names the kind.
_ERROR_KINDS: tuple[tuple[type[Exception], ErrorKind], ...] = (
    (ConfigError, "config_error"),
    (MonitorNotAttachedError, "not_attached"),
    (UnknownTargetError, "unknown_target"),
    (MonitorSelectionError, "unknown_target"),
    (NoImagesError, "no_images"),
    (NothingShownError, "nothing_shown"),
    (ImageLoadError, "bad_image"),
    (SettingsError, "invalid_settings"),
    (StaleSettingsError, "stale_settings"),
    (AppError, "failed"),
)


def error_reply(err: Exception) -> ErrorReply:
    """An expected failure keeps its message; anything else is a bug."""
    for error_type, kind in _ERROR_KINDS:
        if isinstance(err, error_type):
            return ErrorReply.make(kind, err)
    return ErrorReply.make("internal", f"Unexpected error: {err}")


def _save_settings(
    service: PlaylistService, request: SaveSettingsRequest
) -> SaveSettingsResult:
    revision = service.save_settings(request.settings, request.revision)
    return SaveSettingsResult(
        revision=revision, warnings=tuple(service.settings.warnings())
    )


def _stats(service: PlaylistService, request: StatsRequest) -> StatsResult:
    """One playlist if a target is named, else all."""
    if request.target is None:
        playlists = service.settings.playlists
    else:
        playlists = (service.find_playlist(request.target),)
    favourites, to_remove = service.list_stats()
    return StatsResult(
        playlists=tuple(service.stats(p, rescan=request.rescan) for p in playlists),
        favourites=favourites,
        to_remove=to_remove,
    )


def _status(service: PlaylistService) -> StatusResult:
    return StatusResult(
        playlists=tuple(
            _details(service, status) for status in service.status(upcoming=3)
        ),
        paused=service.pause_reason(),
        warnings=tuple(service.settings.warnings()),
    )


def _mark_command(service: PlaylistService, request: MarkRequest) -> Outcome:
    """
    The image named, or the target playlist's current one (`playlist` in
    the result then names it). `remove` of the current image also moves on.
    """
    playlist: str | None = None
    if request.image is None:
        settings = service.find_playlist(request.target)
        if request.command == "remove":
            return _remove_current(service, settings)
        playlist, image = settings.name, service.require_current(settings)
    else:
        image = request.image
    command = request.command
    match command:
        case "fav":
            marks, changed = service.favourites, service.favourites.add(image)
        case "unfav":
            marks, changed = service.favourites, service.favourites.discard(image)
        case "remove":  # an image named: only listed, the next tick moves on
            changed = service.to_remove.add(image)
            removed = RemoveResult(
                playlist=None,
                image=image,
                list_file=service.to_remove.file,
                changed=changed,
                change=None,
            )
            return Outcome(removed)
        case "unremove":
            marks, changed = service.to_remove, service.to_remove.discard(image)
    mark = MarkResult(
        command=command,
        playlist=playlist,
        image=image,
        list_file=marks.file,
        changed=changed,
    )
    return Outcome(mark)


def _remove_current(service: PlaylistService, playlist: PlaylistSettings) -> Outcome:
    image, listed, change = service.remove(playlist)
    result = RemoveResult(
        playlist=playlist.name,
        image=image,
        list_file=service.to_remove.file,
        changed=listed,
        change=change,
    )
    return Outcome(result, change)


# -- overview and status ---------------------------------------------------------


def overview(service: PlaylistService) -> Overview:
    monitors = service.monitors()
    statuses = service.status(upcoming=0, monitors=monitors)
    return Overview(
        playlists=tuple(_view(service, s) for s in statuses),
        monitors=tuple(_desktop_monitor(service, m) for m in monitors),
        paused=service.pause_reason(),
        favourites_file=service.favourites.file,
        to_remove_file=service.to_remove.file,
    )


def _desktop_monitor(service: PlaylistService, monitor: Monitor) -> DesktopMonitor:
    rect = monitor.rect
    geometry = (
        None
        if rect is None
        else Geometry(
            x=rect.x1,
            y=rect.y1,
            width=rect.width,
            height=rect.height,
            pixels=rect.pixels,
        )
    )
    return DesktopMonitor(
        id=monitor.id,
        name=monitor.name,
        geometry=geometry,
        look=service.settings.look(monitor.id),
    )


def _view(service: PlaylistService, status: PlaylistStatus) -> PlaylistView:
    monitors = tuple(
        MonitorView(
            # As the desktop spells it if listed (as `Overview.monitors`
            # does), else as the settings do.
            id=m.monitor.id if m.monitor else m.monitor_id,
            name=m.monitor.name if m.monitor else str(m.monitor_id),
            attached=m.monitor.attached if m.monitor else None,
            look=service.settings.look(m.monitor_id),
        )
        for m in status.members
    )
    current = status.current
    return PlaylistView(
        id=status.playlist.id,
        name=status.playlist.name,
        directory=status.playlist.directory,
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
        order=playlist.order,
        interval=interval,
        next_in_seconds=None if interval is None else interval - status.elapsed,
        upcoming=status.upcoming,
    )
