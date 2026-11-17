"""
The settings: playlists, the monitors they drive and how each monitor
shows its images, and the general settings (pausing, the list files).

They live in the state database (core.store), written only by the daemon: the
settings window reads them with the `settings` command and writes them with
`save_settings` (docs/architecture.md). `Settings` is that whole edit, as
stored and as sent; its fields are typed by pydantic, and what the values
mean (unique names, durations, folders, list files) is checked once, here:
`settings_problems`, `folder_problems` and `list_file_problems` find every
problem with its place (the Settings page shows them), `check_settings`,
`check_folders` and `check_list_files` raise the first (the daemon).

Per monitor or per playlist: how an image looks on a screen (`Look`) is per
monitor; which image and when (folder, order, interval) is per playlist.
A monitor names its playlist; several monitors on one playlist mirror it.
Monitors are held by id only, never by name (two monitors of one model
share a name).
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

from .model import MAX_BLUR, MAX_DARKEN, MIN_BLUR, Look, MonitorId, WallpaperError


class Order(Enum):
    SORTED = "sorted"
    SHUFFLE = "shuffle"


MANUAL = "manual"  # the interval of a playlist that never changes by itself
MIN_INTERVAL_S = 10.0  # a shorter interval is refused
DEFAULT_INTERVAL_S = 30 * 60.0
DEFAULT_IDLE_S = 5 * 60.0
NO_PLAYLISTS = (
    "No playlists yet: add one in Settings (in the tray menu, or "
    "`wallpaper ui settings`)"
)


class SettingsError(WallpaperError):
    """Settings that can't be saved; the message says which value and why."""


class StaleSettingsError(WallpaperError):
    """The stored settings changed since the edit was read; nothing saved."""


# A playlist's id keys its state (history, shuffle deck, hold), so the name
# can change freely.
PlaylistId = StrictStr
# A number of seconds, strictly: a whole or decimal number, never `true` or
# "300" (lax pydantic would read those as 1.0 and 300.0), nor inf / nan.
Seconds = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class PlaylistSettings(BaseModel):
    """
    A folder rotated on the monitors that name this playlist (the same
    image on each: mirrored).

    `id` is generated (`new_playlist_id`) and never reused: deleting a
    playlist deletes its state, and a new one starts afresh. `interval` is
    None for a manual playlist: it never changes by itself, only by next /
    prev / pick.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: PlaylistId
    name: StrictStr
    directory: Path
    order: Order = Order.SHUFFLE
    interval: Seconds | None = DEFAULT_INTERVAL_S  # active seconds; None: manual

    @property
    def manual(self) -> bool:
        return self.interval is None


class MonitorSettings(BaseModel):
    """
    One monitor's settings, by id: its playlist (None: none) and its look.
    A monitor without a row has no playlist and the default look.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: MonitorId
    playlist: PlaylistId | None = None
    look: Look = Look()


class Settings(BaseModel):
    """
    Everything the settings window edits, saved as a whole. Playlists are
    listed in this order (the tray, Home).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    playlists: tuple[PlaylistSettings, ...] = ()
    monitors: tuple[MonitorSettings, ...] = ()
    pause_when_locked: StrictBool = True
    pause_when_idle: Seconds | None = DEFAULT_IDLE_S  # seconds; None: never
    # A sound when a playlist rotates on its interval (not by a command).
    rotation_sound: StrictBool = False
    # None: in the data directory (favourites.txt, to-remove.txt).
    favourites_file: Path | None = None
    to_remove_file: Path | None = None

    def playlist(self, name: str) -> PlaylistSettings | None:
        """By name, case-insensitively."""
        wanted = name.strip().casefold()
        return next((p for p in self.playlists if p.name.casefold() == wanted), None)

    def members(self, playlist_id: str) -> tuple[MonitorId, ...]:
        """The monitors on the playlist, in the order of `monitors`."""
        return tuple(m.id for m in self.monitors if m.playlist == playlist_id)

    def look(self, monitor_id: MonitorId) -> Look:
        found = next((m for m in self.monitors if m.id == monitor_id), None)
        return Look() if found is None else found.look

    def list_files(self, data_dir: Path) -> tuple[Path, Path]:
        """favourites.txt and to-remove.txt, the defaults in `data_dir`."""
        return (
            self.favourites_file or data_dir / "favourites.txt",
            self.to_remove_file or data_dir / "to-remove.txt",
        )

    def warnings(self) -> list[str]:
        """Settings that are valid but probably not what was meant."""
        warnings = [] if self.playlists else [NO_PLAYLISTS]
        idle = self.pause_when_idle
        if idle is None:
            return warnings
        # The idle pause starts `idle` seconds after the last input. A shorter
        # interval can change the image twice in that time, so one goes by
        # unseen. (With a longer one, the image shown then stays on screen.)
        return warnings + [
            f"{p.name}: the interval ({format_duration(p.interval)}) is shorter "
            f"than the idle pause ({format_duration(idle)}): an image can "
            f"change unseen before rotation pauses"
            for p in self.playlists
            if p.interval is not None and p.interval < idle
        ]


# -- checks --------------------------------------------------------------------
#
# Each check finds every problem with its place (`*_problems`), for the
# Settings page to show next to its field, and the daemon raises the first
# (`check_*`) when it saves.

# A problem's field: a playlist's or a general setting's (as named in the
# models), or the monitors' rows.
NAME, ID, DIRECTORY, INTERVAL = "name", "id", "directory", "interval"
IDLE, FAVOURITES, TO_REMOVE = "pause_when_idle", "favourites_file", "to_remove_file"
MONITORS = "monitors"
FIELD_NAMES = {  # as the texts call them
    NAME: "name",
    ID: "id",
    DIRECTORY: "folder",
    INTERVAL: "interval",
    IDLE: "idle pause",
    FAVOURITES: "favourites file",
    TO_REMOVE: "to-remove file",
    MONITORS: "monitors",
}
SAME_LISTS = "The favourites and to-remove lists can't be the same file"


@dataclass(frozen=True)
class Problem:
    """A value that can't be used: where, and why (said next to the field)."""

    playlist: PlaylistId | None  # its id; None: a general setting or a monitor
    field: str  # NAME, DIRECTORY, IDLE, ...
    message: str  # "At least 10s"

    def where(self, name: str | None) -> str:
        """In words: "konachan, folder", "Idle pause". `name`: its playlist's."""
        if self.playlist is None:
            return FIELD_NAMES[self.field].capitalize()
        return f"{(name or '').strip() or 'a playlist'}, {FIELD_NAMES[self.field]}"


def settings_problems(settings: Settings) -> list[Problem]:
    """What the values mean, beyond their types (see also `folder_problems`)."""
    found = _playlist_problems(settings.playlists)
    ids = {p.id for p in settings.playlists}
    seen: set[MonitorId] = set()
    for monitor in settings.monitors:
        found += [
            Problem(None, MONITORS, message)
            for message in _monitor_problems(monitor, ids, seen)
        ]
        seen.add(monitor.id)
    idle = settings.pause_when_idle
    if idle is not None and idle <= 0:  # would pause for ever
        found.append(
            Problem(None, IDLE, "Must be more than 0; untick it to never pause")
        )
    for field, file in (
        (FAVOURITES, settings.favourites_file),
        (TO_REMOVE, settings.to_remove_file),
    ):
        if file is not None and not file.is_absolute():
            found.append(Problem(None, field, "Use the file's full path"))
    return found


def _playlist_problems(playlists: tuple[PlaylistSettings, ...]) -> list[Problem]:
    found: list[Problem] = []
    ids: set[str] = set()
    names: dict[str, str] = {}  # case-folded -> as written
    for playlist in playlists:
        name, key = playlist.name, playlist.id
        if not name.strip():
            found.append(Problem(key, NAME, "Give it a name"))
        elif name != name.strip():
            found.append(Problem(key, NAME, "Can't start or end with spaces"))
        elif (other := names.get(name.casefold())) is not None:
            found.append(Problem(key, NAME, f"Another playlist is called {other}"))
        else:
            names[name.casefold()] = name
        if not key.strip() or key in ids:
            found.append(Problem(key, ID, "Needs an id no other playlist has"))
        ids.add(key)
        if not playlist.directory.is_absolute():
            found.append(Problem(key, DIRECTORY, "Use the folder's full path"))
        interval = playlist.interval
        if interval is not None and interval < MIN_INTERVAL_S:
            minimum = format_duration(MIN_INTERVAL_S)
            found.append(Problem(key, INTERVAL, f"At least {minimum}"))
    return found


def _monitor_problems(
    monitor: MonitorSettings, ids: set[str], seen: set[MonitorId]
) -> list[str]:
    found: list[str] = []
    if not monitor.id.key:
        found.append("A monitor's id is empty")
    if monitor.id in seen:
        found.append(f"Monitor {monitor.id} is listed twice")
    if monitor.playlist is not None and monitor.playlist not in ids:
        found.append(
            f"Monitor {monitor.id} is on playlist id {monitor.playlist!r}, "
            f"which doesn't exist"
        )
    look = monitor.look
    if not MIN_BLUR <= look.blur <= MAX_BLUR:
        found.append(
            f"Monitor {monitor.id}: the blur must be from {MIN_BLUR} to {MAX_BLUR}"
        )
    if not 0 <= look.darken <= MAX_DARKEN:
        found.append(
            f"Monitor {monitor.id}: the darkening must be from 0 to {MAX_DARKEN}"
        )
    return found


def folder_problems(settings: Settings, old: Settings) -> list[Problem]:
    """
    A playlist's folder that is new or changed since `old` must exist. (One
    that was there may be gone for a while, e.g. a drive not mounted: the
    playlist then has nothing to show until it is back.)
    """
    before = {p.id: p.directory for p in old.playlists}
    return [
        Problem(p.id, DIRECTORY, f"No such folder: {p.directory}")
        for p in settings.playlists
        if before.get(p.id) != p.directory and not p.directory.is_dir()
    ]


def list_file_problems(settings: Settings, data_dir: Path) -> list[Problem]:
    """
    The favourites and to-remove lists are two files (the defaults are in
    `data_dir`): with one, every favourite would be listed for removal.
    """
    favourites, to_remove = settings.list_files(data_dir)
    if same_path(favourites, to_remove):
        return [Problem(None, TO_REMOVE, SAME_LISTS)]
    return []


def check_settings(settings: Settings) -> None:
    """
    Raises:
        SettingsError: the first of `settings_problems`, in words.
    """
    _raise_first(settings_problems(settings), settings)


def check_folders(settings: Settings, old: Settings) -> None:
    """
    Raises:
        SettingsError: the first of `folder_problems`.
    """
    _raise_first(folder_problems(settings, old), settings)


def check_list_files(settings: Settings, data_dir: Path) -> None:
    """
    Raises:
        SettingsError: the lists are one file (`list_file_problems`).
    """
    _raise_first(list_file_problems(settings, data_dir), settings)


def _raise_first(problems: list[Problem], settings: Settings) -> None:
    if problems:
        first = problems[0]
        names = {p.id: p.name for p in settings.playlists}
        where = first.where(names.get(first.playlist or ""))
        raise SettingsError(f"{where}: {first.message}")


def same_path(a: Path, b: Path) -> bool:
    """One path written two ways (case, on Windows; `..`; slashes)."""
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(
        os.path.normpath(b)
    )


def new_playlist_id() -> str:
    """
    An id no playlist has had: random, so a new playlist never takes over
    the state (history, hold) of a deleted one, whatever the order of edits.
    """
    return uuid.uuid4().hex


# -- durations -----------------------------------------------------------------

_DURATION = re.compile(
    r"(?:(?P<d>\d+)d)?(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s)?"
)
_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_duration(text: str) -> float:
    """
    '30m', '1h30m', '45s', '2d' -> seconds.

    Raises:
        ValueError: not a duration; the message says what one looks like.
    """
    match = _DURATION.fullmatch(text.strip().lower().replace(" ", ""))
    if not text.strip() or match is None:
        raise ValueError(
            f"Invalid duration {text!r}; use e.g. '45s', '30m', '1h30m', '1d'"
        )
    return float(
        sum(int(v) * _UNIT_SECONDS[unit] for unit, v in match.groupdict().items() if v)
    )


def format_duration(seconds: float) -> str:
    """Seconds -> '1h30m', '45s' (the inverse of parse_duration, without days)."""
    seconds = max(0, round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [f"{hours}h" if hours else "", f"{minutes}m" if minutes else ""]
    parts.append(f"{secs}s" if secs or not (hours or minutes) else "")
    return "".join(parts)


def format_ago(seconds: float) -> str:
    """
    How long ago, roughly: 'just now', '2 min ago', '3 h ago', '5 d ago',
    '2 mo ago' (30 days), '1 yr ago' (365 days).
    """
    minutes = int(max(0.0, seconds) // 60)
    hours, days = minutes // 60, minutes // (60 * 24)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if hours < 24:
        return f"{hours} h ago"
    if days < 30:
        return f"{days} d ago"
    if days < 365:
        return f"{days // 30} mo ago"
    return f"{days // 365} yr ago"
