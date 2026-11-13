"""
The TOML config: playlists, the monitors they drive, pausing.

The settings window writes it; people aren't expected to edit it by hand
(it holds monitor ids and generated playlist ids), but a hand edit is
checked like any other. No file, or no playlists, is a valid config that
rotates nothing. Example (see docs/config.example.toml):

    pause_when_locked = true
    pause_when_idle = "5m"

    [playlists.konachan]
    id = "3f0c9d6e5b0e4a8c9a51f0f6d1e7c2b4"  # its state goes by this
    directory = 'D:\\pictures\\KONACHAN'
    monitors = ['\\\\?\\DISPLAY#DELA246#...']  # ids; several: mirrored
    order = "shuffle"
    interval = "30m"  # or "manual": only next / prev / pick
    mode = "fill"
"""

from __future__ import annotations

import codecs
import re
import tomllib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    StringConstraints,
    ValidationError,
)

from .model import AppError, ImageMode, MonitorId


class ConfigError(AppError):
    """
    The config file, or a list file it names, can't be used. Not a
    WallpaperError: `except WallpaperError` (one operation failed, carry on)
    lets it through, to stop the service.
    """


class Order(Enum):
    SORTED = "sorted"
    SHUFFLE = "shuffle"


@dataclass(frozen=True)
class PlaylistConfig:
    """
    A directory rotated on one monitor, or on a mirrored group (the same
    image on every member).

    `id` keys the playlist's state (history, shuffle deck, hold), so the
    name can change freely: generated (`new_playlist_id`), never reused.

    `monitors` holds monitor ids, as `wallpaper list --all` shows them (not
    names: two monitors of one model share one); they compare
    case-insensitively (`MonitorId`). `interval` is None for a
    manual playlist
    (`interval = "manual"`): it never changes by itself, only by next /
    prev / pick.
    """

    name: str
    id: str
    directory: Path
    monitors: tuple[MonitorId, ...]
    interval: float | None  # seconds of active time; None: manual
    order: Order = Order.SHUFFLE
    mode: ImageMode | None = None

    @property
    def manual(self) -> bool:
        return self.interval is None


@dataclass(frozen=True)
class Config:
    playlists: tuple[PlaylistConfig, ...]
    favourites_file: Path
    to_remove_file: Path
    pause_when_locked: bool = True
    pause_when_idle: float | None = 300.0  # seconds without input; None = never
    # False: there was no file (`load_config`), so the empty config says
    # nothing about which playlists exist; `prune` refuses to go by it.
    from_file: bool = True

    def playlist(self, name: str) -> PlaylistConfig | None:
        wanted = name.strip().lower()
        return next((p for p in self.playlists if p.name.lower() == wanted), None)

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
            f"[playlists.{p.name}] interval ({format_duration(p.interval)}) is "
            f"shorter than pause_when_idle ({format_duration(idle)}): an image "
            f"can change unseen before the idle pause starts"
            for p in self.playlists
            if p.interval is not None and p.interval < idle
        ]


_DURATION = re.compile(
    r"(?:(?P<d>\d+)d)?(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s)?"
)
_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
MANUAL = "manual"  # the interval of a playlist that never changes by itself
NO_PLAYLISTS = (
    "No playlists yet: add one in Settings (in the tray menu, or "
    "`wallpaper ui settings`)"
)
MIN_INTERVAL_S = 10.0  # a shorter interval is refused

# -- the file as written -------------------------------------------------------------


def _not_blank(monitor_id: MonitorId) -> MonitorId:
    if not monitor_id.key:
        raise ValueError("blank")
    return monitor_id


def _false_only(value: bool) -> bool:
    if value:
        raise ValueError("only false")
    return value


PlaylistId = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
MonitorIds = Annotated[
    tuple[Annotated[MonitorId, AfterValidator(_not_blank)], ...], Field(min_length=1)
]
Never = Annotated[StrictBool, AfterValidator(_false_only)]  # `false`: never


class PlaylistTable(BaseModel):
    """
    A `[playlists.NAME]` table: its keys and their types. What the values
    mean (durations, paths, unique ids and monitors) is checked by
    `parse_config`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: PlaylistId
    directory: StrictStr
    monitors: MonitorIds
    interval: StrictStr  # a duration, or MANUAL
    order: Order = Order.SHUFFLE
    mode: ImageMode | None = None


class ConfigFile(BaseModel):
    """The whole file: its keys and their types (see PlaylistTable)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    playlists: Mapping[str, PlaylistTable] = Field(default_factory=dict)
    favourites_file: StrictStr | None = None  # None: in the default directory
    to_remove_file: StrictStr | None = None
    pause_when_locked: StrictBool = True
    pause_when_idle: StrictStr | Never = "5m"


def _choices(enum: type[Enum]) -> str:
    return ", ".join(str(m.value) for m in enum)


_PROBLEMS = {
    "playlists": "must be [playlists.NAME] tables",
    "id": "must be text",
    "directory": "must be a path",
    "favourites_file": "must be a path",
    "to_remove_file": "must be a path",
    "monitors": "must be a list of one or more monitor ids",
    "interval": "must be a string like '30m' or 'manual'",
    "order": f"must be one of: {_choices(Order)}",
    "mode": f"must be one of: {_choices(ImageMode)}",
    "pause_when_locked": "must be true or false",
    "pause_when_idle": "must be a duration like '5m', or false",
}


def validate_file[M: BaseModel](
    model: type[M], raw: Mapping[str, Any], error: type[AppError]
) -> M:
    """
    A parsed TOML document as `model` (ConfigFile, or a subclass); a problem
    is raised as `error`, worded for the person who edits the file.
    """
    try:
        return model.model_validate(raw)
    except ValidationError as err:
        raise error(_explain(err)) from err


def _explain(err: ValidationError) -> str:
    """The first problem in words (an unknown key first: it is likely a typo)."""
    errors = err.errors()
    unknown = [e for e in errors if e["type"] == "extra_forbidden"]
    first = (unknown or errors)[0]
    loc = tuple(str(part) for part in first["loc"])
    in_playlist = loc[0] == "playlists" and len(loc) > 1
    where, rest = (f"[playlists.{loc[1]}]", loc[2:]) if in_playlist else ("", loc)
    if not rest:
        return f"{where} must be a table"
    key = rest[0]
    if unknown:
        keys = sorted(
            {str(e["loc"][-1]) for e in unknown if e["loc"][:-1] == first["loc"][:-1]}
        )
        expected = (PlaylistTable if in_playlist else ConfigFile).model_fields
        return (
            f"Unknown key(s) in {where or 'top level'}: {', '.join(keys)}; "
            f"expected: {', '.join(sorted(expected))}"
        )
    # An empty id is no id: no state can go by it.
    if in_playlist and key == "id" and first["type"] in ("missing", "string_too_short"):
        return missing_id(where)
    message = f"needs {key!r}" if first["type"] == "missing" else _problem(key)
    return f"{where} {message}" if in_playlist else message


def _problem(key: str) -> str:
    return f"{key} {_PROBLEMS[key]}"


def parse_duration(text: str) -> float:
    """'30m', '1h30m', '45s', '2d' -> seconds."""
    match = _DURATION.fullmatch(text.strip().lower().replace(" ", ""))
    if not text.strip() or match is None:
        raise ConfigError(
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


def load_config(path: Path, default_dir: Path) -> Config:
    """
    Read and validate the config file. No file is a config without
    playlists (`from_file` False): the settings window creates it.

    Relative paths in the file are relative to the file's directory;
    favourites / to-remove files default to `default_dir`.
    """
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return replace(loads_config("", path, default_dir), from_file=False)
    except OSError as err:
        raise ConfigError(f"Cannot read {path}: {err}") from err
    return loads_config(decode_text(data, path), path, default_dir)


_BOMS = (codecs.BOM_UTF8, codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)


def decode_text(data: bytes, path: Path) -> str:
    """
    The text of a file this app reads (the config, the list files): UTF-8
    without a byte order mark, and nothing else.

    Raises:
        ConfigError: any other encoding, or a BOM.
    """
    # A UTF-8 BOM decodes without error, to an invisible U+FEFF before the
    # first line: a first path that never matches, or a TOML error on line
    # 1 that the user can't see. Refused with a message that says what to
    # do, not stripped, so the app reads what every other tool (scripts, on
    # both systems) reads. The UTF-16 BOMs would fail to decode anyway;
    # checked here for the clearer message (Notepad offers "UTF-16 LE").
    if data.startswith(_BOMS):
        raise ConfigError(
            f"{path} is not UTF-8 text: it starts with a byte order mark (BOM). "
            f"Save it as UTF-8 without BOM."
        )
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as err:
        raise ConfigError(f"{path} is not UTF-8 text: {err}") from err


def loads_config(text: str, path: Path, default_dir: Path) -> Config:
    """
    Validate config text as if it were the file at `path` (relative paths
    in it are relative to the file's directory). The settings window checks
    a new text with this before writing it.
    """
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(f"Cannot read {path}: {err}") from err
    try:
        return parse_config(raw, base_dir=path.parent, default_dir=default_dir)
    except ConfigError as err:
        raise ConfigError(f"{path}: {err}") from err


def parse_config(raw: Mapping[str, Any], base_dir: Path, default_dir: Path) -> Config:
    file = validate_file(ConfigFile, raw, ConfigError)
    playlists = tuple(
        _playlist(name, table, base_dir) for name, table in file.playlists.items()
    )
    _check_ids_unique(playlists)
    _check_monitors_unique(playlists)

    idle = file.pause_when_idle
    pause_when_idle = parse_duration(idle) if isinstance(idle, str) else None
    if pause_when_idle is not None and pause_when_idle <= 0:  # would pause for ever
        raise ConfigError(
            "pause_when_idle must be more than 0; use false to never pause"
        )

    return Config(
        playlists=playlists,
        favourites_file=_path(
            file.favourites_file,
            default_dir / "favourites.txt",
            base_dir,
            "favourites_file",
        ),
        to_remove_file=_path(
            file.to_remove_file,
            default_dir / "to-remove.txt",
            base_dir,
            "to_remove_file",
        ),
        pause_when_locked=file.pause_when_locked,
        pause_when_idle=pause_when_idle,
    )


def _playlist(name: str, table: PlaylistTable, base_dir: Path) -> PlaylistConfig:
    where = f"[playlists.{name}]"
    return PlaylistConfig(
        name=name,
        id=table.id,
        directory=_path(table.directory, None, base_dir, f"{where} directory"),
        monitors=table.monitors,
        interval=_interval(table.interval, where),
        order=table.order,
        mode=table.mode,
    )


def _interval(value: str, where: str) -> float | None:
    """A duration in seconds, or None for "manual"."""
    if value.strip().lower() == MANUAL:
        return None
    interval = parse_duration(value)
    if interval < MIN_INTERVAL_S:
        raise ConfigError(f"{where} interval must be at least 10s, or 'manual'")
    return interval


def _path(value: str | None, default: Path | None, base_dir: Path, what: str) -> Path:
    """`value` (or `default` if None) relative to `base_dir`."""
    if value is None and default is not None:
        path = default
    elif value is not None and value.strip():
        path = Path(value).expanduser()
    else:
        raise ConfigError(f"{what} must be a path")
    return path if path.is_absolute() else base_dir / path


def new_playlist_id() -> str:
    """
    An id no playlist has had: random, so a new playlist never takes over
    the state (history, hold) of a deleted one, whatever the order of edits.
    """
    return uuid.uuid4().hex


def missing_id(where: str) -> str:
    return f"{where} needs an id, text no other playlist has"


def _check_ids_unique(playlists: tuple[PlaylistConfig, ...]) -> None:
    owners: dict[str, str] = {}
    for playlist in playlists:
        owner = owners.setdefault(playlist.id, playlist.name)
        if owner != playlist.name:
            raise ConfigError(
                f"{owner!r} and {playlist.name!r} both have id {playlist.id!r}; "
                f"ids must be unique"
            )


def _check_monitors_unique(playlists: tuple[PlaylistConfig, ...]) -> None:
    owners: dict[MonitorId, str] = {}
    for playlist in playlists:
        for monitor in playlist.monitors:
            owner = owners.setdefault(monitor, playlist.name)
            if owner != playlist.name:
                raise ConfigError(
                    f"Monitor {str(monitor)!r} is in both {owner!r} and "
                    f"{playlist.name!r}; a monitor can follow only one playlist"
                )
