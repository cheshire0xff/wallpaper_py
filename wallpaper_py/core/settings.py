"""
The settings: playlists, the monitors they drive and how each monitor
shows its images, and the general settings (pausing, the list files).

They live in the state database (core.store), written only by the app: the
settings window reads them with the `settings` command and writes them with
`save_settings` (docs/plan.md, Phase 4b). `Settings` is that whole edit, as
stored and as sent; its fields are typed by pydantic, and what the values
mean (unique names, durations, folders, list files) is checked by
`check_settings`, `check_folders` and `check_list_files`.

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
from enum import Enum
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

from .model import Look, MonitorId, WallpaperError


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


def check_settings(settings: Settings) -> None:
    """
    What the values mean, beyond their types (see also `check_folders`).

    Raises:
        SettingsError: the first problem, in words.
    """
    ids = _check_playlists(settings.playlists)
    seen: set[MonitorId] = set()
    for monitor in settings.monitors:
        if not monitor.id.key:
            raise SettingsError("A monitor's id is empty")
        if monitor.id in seen:
            raise SettingsError(f"Monitor {monitor.id} is listed twice")
        seen.add(monitor.id)
        if monitor.playlist is not None and monitor.playlist not in ids:
            raise SettingsError(
                f"Monitor {monitor.id} is on playlist id {monitor.playlist!r}, "
                f"which doesn't exist"
            )
    idle = settings.pause_when_idle
    if idle is not None and idle <= 0:  # would pause for ever
        raise SettingsError("The idle pause must be more than 0, or never")
    for what, file in (
        ("favourites", settings.favourites_file),
        ("to-remove", settings.to_remove_file),
    ):
        if file is not None and not file.is_absolute():
            raise SettingsError(f"The {what} list must be a full path")


def _check_playlists(playlists: tuple[PlaylistSettings, ...]) -> set[str]:
    """Their ids."""
    ids: set[str] = set()
    names: set[str] = set()
    for playlist in playlists:
        name = playlist.name
        if not name.strip():
            raise SettingsError("Every playlist needs a name")
        if name != name.strip():
            raise SettingsError(f"{name!r}: a name can't start or end with spaces")
        if not playlist.id.strip() or playlist.id in ids:
            raise SettingsError(f"{name}: needs an id no other playlist has")
        ids.add(playlist.id)
        if name.casefold() in names:
            raise SettingsError(f"Two playlists are named {name!r}")
        names.add(name.casefold())
        if not playlist.directory.is_absolute():
            raise SettingsError(f"{name}: the folder must be a full path")
        interval = playlist.interval
        if interval is not None and interval < MIN_INTERVAL_S:
            raise SettingsError(
                f"{name}: the interval must be at least "
                f"{format_duration(MIN_INTERVAL_S)}, or manual"
            )
    return ids


def check_folders(settings: Settings, old: Settings) -> None:
    """
    A playlist's folder that is new or changed since `old` exists. (One
    that was there may be gone for a while, e.g. a drive not mounted: the
    playlist then has nothing to show until it is back.)

    Raises:
        SettingsError: the first folder that doesn't.
    """
    before = {p.id: p.directory for p in old.playlists}
    for playlist in settings.playlists:
        directory = playlist.directory
        if before.get(playlist.id) != directory and not directory.is_dir():
            raise SettingsError(
                f"{playlist.name}: the folder {directory} doesn't exist"
            )


SAME_LISTS = "The favourites and to-remove lists can't be the same file"


def check_list_files(settings: Settings, data_dir: Path) -> None:
    """
    The favourites and to-remove lists are two files (the defaults are in
    `data_dir`): with one, every favourite would be listed for removal.

    Raises:
        SettingsError: they are one file.
    """
    favourites, to_remove = settings.list_files(data_dir)
    if same_path(favourites, to_remove):
        raise SettingsError(SAME_LISTS)


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
