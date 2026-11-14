"""
Playlist commands run on the service: a `Request` in, a result (or an
error) out, as the data models of `protocol.py`; no text. (`reload` is not
one: it replaces the service, so the app runs it; see app/worker.py.) The CLI formats
its output from them, the tray and the windows show them.

`execute()` runs a command on the app's service thread, whether it came over
the socket (CLI, UI process) or from the tray. Besides the reply it returns
the wallpaper change the command made, if any, so the app announces it like
a tick's. Qt-free, like the core.

`open_service()` runs the service on the settings in the state database.
Until Phase 4b step 3b it first imports config.toml into them (a temporary
bridge for the old Settings page, not a migration; see `import_config`).
`settings` and `save_settings` read and write them.

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
from .core.config import load_config
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
from .core.settings import (
    PlaylistSettings,
    SettingsError,
    StaleSettingsError,
    check_settings,
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
    SaveSettingsRequest,
    SaveSettingsResult,
    ServiceRequest,
    SettingsRequest,
    SettingsResult,
    StatusResult,
)

log = logging.getLogger(__name__)


class Outcome(NamedTuple):
    """
    What a command did: its reply, the wallpaper change it made, and
    whether monitors wait to be rendered again (their playlist, look or
    folder changed): the app then ticks before it replies.
    """

    reply: Reply
    change: Change | None = None
    render: bool = False


@contextmanager
def open_service(
    config_path: Path, backend: Backend, presence: Presence, tick_period: float = 5.0
) -> Iterator[PlaylistService]:
    """
    A service on the settings in the config's state database, after
    importing the config file into them; closes the database after.

    Raises:
        ConfigError: the file, or a list file.
    """
    with Store(paths.state_db(config_path)) as store:
        import_config(config_path, store)
        service = PlaylistService(
            backend,
            presence,
            store,
            paths.render_dir(),
            paths.data_dir(),
            tick_period=tick_period,
        )
        yield service


def import_config(config_path: Path, store: Store) -> None:
    """
    The bridge of Phase 4b step 3a, gone in 3b: the old Settings page still
    writes config.toml, so the file (if there is one) replaces the stored
    settings, at start and on `reload`. Each playlist's mode becomes its
    monitors' look. A playlist the file no longer has loses its state, as
    when the settings window deletes one.

    Raises:
        ConfigError: the file can't be used; nothing was imported.
    """
    config = load_config(config_path, default_dir=paths.data_dir())
    if config is None:
        return
    settings = config.to_settings()
    try:
        check_settings(settings)
    except SettingsError as err:
        raise ConfigError(f"{config_path}: {err}") from err
    if settings != store.settings():
        store.save_settings(settings, revision=None)


def execute(service: PlaylistService, request: ServiceRequest) -> Outcome:
    """
    An expected failure is an error reply, except a broken config.

    Raises:
        ConfigError: the config or a list file went bad (e.g. saved with a
            byte order mark). The service can't go on: the caller stops it,
            and answers with the error until a reload (see app/worker.py).
    """
    try:
        if isinstance(request, PruneRequest | SettingsRequest | SaveSettingsRequest):
            return _settings_command(service, request)
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


def _settings_command(
    service: PlaylistService,
    request: PruneRequest | SettingsRequest | SaveSettingsRequest,
) -> Outcome:
    """The commands on the settings and the state they keep."""
    match request:
        case PruneRequest():
            return Outcome(_prune(service))
        case SettingsRequest():
            return Outcome(
                SettingsResult(settings=service.settings, revision=service.revision())
            )
        case SaveSettingsRequest():
            return Outcome(_save_settings(service, request), render=True)


def _prune(service: PlaylistService) -> PruneResult:
    dropped = service.prune_state()
    for playlist_id in dropped:
        log.info(
            "Dropped the state of playlist id %s: not in the settings", playlist_id
        )
    return PruneResult(dropped=tuple(dropped))


def _save_settings(
    service: PlaylistService, request: SaveSettingsRequest
) -> SaveSettingsResult:
    """Monitors whose settings changed are rendered by the caller's tick."""
    revision = service.save_settings(request.settings, request.revision)
    return SaveSettingsResult(
        revision=revision, warnings=tuple(service.settings.warnings())
    )


def _status(service: PlaylistService) -> StatusResult:
    return StatusResult(
        playlists=tuple(
            _details(service, status) for status in service.status(upcoming=3)
        ),
        paused=service.pause_reason(),
        warnings=tuple(service.settings.warnings()),
    )


def _next(service: PlaylistService, playlist: PlaylistSettings) -> Outcome:
    change = service.next(playlist)
    return Outcome(NextResult(playlist=playlist.name, change=change), change)


def _prev(service: PlaylistService, playlist: PlaylistSettings) -> Outcome:
    change = service.previous(playlist)
    return Outcome(PrevResult(playlist=playlist.name, change=change), change)


def _fav(service: PlaylistService, playlist: PlaylistSettings) -> Outcome:
    image, added = service.favourite(playlist)
    return Outcome(_mark("fav", service, playlist, image, added))


def _unfav(service: PlaylistService, playlist: PlaylistSettings) -> Outcome:
    image, removed = service.unfavourite(playlist)
    return Outcome(_mark("unfav", service, playlist, image, removed))


def _mark(
    command: Literal["fav", "unfav"],
    service: PlaylistService,
    playlist: PlaylistSettings,
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


def _remove(service: PlaylistService, playlist: PlaylistSettings) -> Outcome:
    image, change = service.remove(playlist)
    result = RemoveResult(
        playlist=playlist.name,
        image=image,
        list_file=service.to_remove.file,
        change=change,
    )
    return Outcome(result, change)


def _hold(service: PlaylistService, playlist: PlaylistSettings) -> Outcome:
    changed = service.hold(playlist)
    return Outcome(HoldResult(playlist=playlist.name, held=True, changed=changed))


def _resume(service: PlaylistService, playlist: PlaylistSettings) -> Outcome:
    changed = service.resume(playlist)
    return Outcome(ResumeResult(playlist=playlist.name, held=False, changed=changed))


_Handler = Callable[[PlaylistService, PlaylistSettings], Outcome]
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
        monitors=tuple(_desktop_monitor(service, m) for m in monitors),
        paused=service.pause_reason(),
    )


def _desktop_monitor(service: PlaylistService, monitor: Monitor) -> DesktopMonitor:
    rect = monitor.rect
    geometry = (
        None
        if rect is None
        else Geometry(x=rect.x1, y=rect.y1, width=rect.width, height=rect.height)
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
        next_in_seconds=None if interval is None else interval - status.elapsed,
        upcoming=status.upcoming,
    )
