"""
The human-edited TOML config: playlists, the monitors they drive, pausing.

Example (see docs/config.example.toml for the full commented version):

    pause_when_locked = true
    pause_when_idle = "5m"

    [playlists.konachan]
    directory = 'D:\\pictures\\KONACHAN'
    monitors = ["AW3225QF", "PLX2783H"]   # more than one: mirrored group
    order = "shuffle"
    interval = "30m"
    mode = "fill"
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .model import ImageMode, WallpaperError


class ConfigError(WallpaperError):
    """The config file is missing or invalid."""


class Order(Enum):
    SORTED = "sorted"
    SHUFFLE = "shuffle"


@dataclass(frozen=True)
class PlaylistConfig:
    """
    A directory rotated on one monitor, or on a mirrored group (the same
    image on every member).

    `monitors` holds selectors: exact monitor names or full ids, as shown by
    `wallpaper list --all`.
    """

    name: str
    directory: Path
    monitors: tuple[str, ...]
    interval: float  # seconds
    order: Order = Order.SHUFFLE
    mode: ImageMode | None = None


@dataclass(frozen=True)
class Config:
    playlists: tuple[PlaylistConfig, ...]
    favourites_file: Path
    to_remove_file: Path
    pause_when_locked: bool = True
    pause_when_idle: float | None = 300.0  # seconds without input; None = never

    def playlist(self, name: str) -> PlaylistConfig | None:
        wanted = name.strip().lower()
        return next((p for p in self.playlists if p.name.lower() == wanted), None)


_DURATION = re.compile(
    r"(?:(?P<d>\d+)d)?(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s)?"
)
_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}

_TOP_KEYS = {
    "playlists",
    "favourites_file",
    "to_remove_file",
    "pause_when_locked",
    "pause_when_idle",
}
_PLAYLIST_KEYS = {"directory", "monitors", "interval", "order", "mode"}


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


def load_config(path: Path, default_dir: Path) -> Config:
    """
    Read and validate the config file.

    Relative paths in the file are relative to the file's directory;
    favourites / to-remove files default to `default_dir`.
    """
    try:
        with path.open("rb") as file:
            raw = tomllib.load(file)
    except FileNotFoundError as err:
        raise ConfigError(
            f"No config file at {path}. Create one; see docs/config.example.toml."
        ) from err
    except (OSError, tomllib.TOMLDecodeError) as err:
        raise ConfigError(f"Cannot read {path}: {err}") from err
    try:
        return parse_config(raw, base_dir=path.parent, default_dir=default_dir)
    except ConfigError as err:
        raise ConfigError(f"{path}: {err}") from err


def parse_config(raw: Mapping[str, Any], base_dir: Path, default_dir: Path) -> Config:
    _reject_unknown(raw, _TOP_KEYS, "top level")
    tables = raw.get("playlists", {})
    if not isinstance(tables, Mapping) or not tables:
        raise ConfigError("Define at least one [playlists.NAME] table")
    playlists = tuple(
        _parse_playlist(name, table, base_dir) for name, table in tables.items()
    )
    _check_monitors_unique(playlists)

    idle = raw.get("pause_when_idle", "5m")
    if idle is False:
        pause_when_idle = None
    elif isinstance(idle, str):
        pause_when_idle = parse_duration(idle)
    else:
        raise ConfigError("pause_when_idle must be a duration like '5m', or false")
    locked = raw.get("pause_when_locked", True)
    if not isinstance(locked, bool):
        raise ConfigError("pause_when_locked must be true or false")

    return Config(
        playlists=playlists,
        favourites_file=_path(
            raw.get("favourites_file", default_dir / "favourites.txt"),
            base_dir,
            "favourites_file",
        ),
        to_remove_file=_path(
            raw.get("to_remove_file", default_dir / "to-remove.txt"),
            base_dir,
            "to_remove_file",
        ),
        pause_when_locked=locked,
        pause_when_idle=pause_when_idle,
    )


def _parse_playlist(name: str, table: Any, base_dir: Path) -> PlaylistConfig:
    where = f"[playlists.{name}]"
    if not isinstance(table, Mapping):
        raise ConfigError(f"{where} must be a table")
    _reject_unknown(table, _PLAYLIST_KEYS, where)
    for key in ("directory", "monitors", "interval"):
        if key not in table:
            raise ConfigError(f"{where} needs {key!r}")

    monitors = table["monitors"]
    if isinstance(monitors, str):
        monitors = [monitors]
    if (
        not isinstance(monitors, list)
        or not monitors
        or not all(isinstance(m, str) and m.strip() for m in monitors)
    ):
        raise ConfigError(f"{where} monitors must be a list of monitor names")

    if not isinstance(table["interval"], str):
        raise ConfigError(f"{where} interval must be a string like '30m'")
    interval = parse_duration(table["interval"])
    if interval < 10:
        raise ConfigError(f"{where} interval must be at least 10s")

    return PlaylistConfig(
        name=name,
        directory=_path(table["directory"], base_dir, f"{where} directory"),
        monitors=tuple(m.strip() for m in monitors),
        interval=interval,
        order=_enum(Order, table.get("order", "shuffle"), f"{where} order"),
        mode=(
            _enum(ImageMode, table["mode"], f"{where} mode")
            if "mode" in table
            else None
        ),
    )


def _check_monitors_unique(playlists: tuple[PlaylistConfig, ...]) -> None:
    owners: dict[str, str] = {}
    for playlist in playlists:
        for monitor in playlist.monitors:
            owner = owners.setdefault(monitor.lower(), playlist.name)
            if owner != playlist.name:
                raise ConfigError(
                    f"Monitor {monitor!r} is in both {owner!r} and "
                    f"{playlist.name!r}; a monitor can follow only one playlist"
                )


def _reject_unknown(table: Mapping[str, Any], known: set[str], where: str) -> None:
    unknown = sorted(set(table) - known)
    if unknown:
        raise ConfigError(
            f"Unknown key(s) in {where}: {', '.join(unknown)}; "
            f"expected: {', '.join(sorted(known))}"
        )


def _path(value: Any, base_dir: Path, what: str) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ConfigError(f"{what} must be a path")
    path = Path(value).expanduser()
    return path if path.is_absolute() else base_dir / path


def _enum[E: Enum](enum: type[E], value: Any, what: str) -> E:
    try:
        return enum(str(value).lower())
    except ValueError as err:
        choices = ", ".join(str(m.value) for m in enum)
        raise ConfigError(f"{what} must be one of: {choices}") from err
