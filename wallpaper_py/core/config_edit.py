"""
Editing config.toml in place, as the settings window does: the file stays
the source of truth, so hand edits, comments, order and formatting survive.

- `read_settings(text)`: the editable values, as written (`Settings`).
- `render(text, base, edited)`: the new text. Only what differs between
  `base` (read from `text`) and `edited` is touched, through tomlkit;
  everything else stays byte for byte. No change gives the same text.
- `validate(edited, ...)`: problems per field, for the window to show next
  to the field. The caller then checks the rendered text with the core's
  own parser (`config.loads_config`) before writing it.
- `choose_selector`, `assign_monitor`, `remove_selector`: which selector to
  write for a monitor, and moving monitors between playlists so that a
  monitor is in one playlist at most.
- `read_file`, `write_file`: the file as text (line endings kept), and an
  atomic write that refuses if the file changed since it was read.

Pure Python like the rest of `core`: no Qt, no platform APIs.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

import tomlkit
from tomlkit.container import Container
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import (
    AoT,
    Array,
    Comment,
    Item,
    Null,
    SingleKey,
    String,
    StringType,
    Table,
    Whitespace,
)
from tomlkit.toml_document import TOMLDocument

from .config import MANUAL, MIN_INTERVAL_S, ConfigError, Order, parse_duration
from .model import ImageMode, WallpaperError

PLAYLISTS = "playlists"
# A playlist name the window writes must be a bare TOML key (no quoting
# needed): names already in the file may be anything and are kept as written.
NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
DEFAULT_IDLE = "5m"  # pause_when_idle when the file doesn't say (core.config)
NEW_INTERVAL = "30m"  # for a playlist added in the window


class ConfigEditError(WallpaperError):
    """The file can't be edited here (not TOML, or a value of the wrong type)."""


class ConfigConflictError(WallpaperError):
    """The file changed on disk since it was read; nothing was written."""


# -- what can be edited ------------------------------------------------------------


@dataclass(frozen=True)
class PlaylistSettings:
    """One `[playlists.NAME]` table, as written (texts are not parsed here)."""

    # pylint: disable=too-many-instance-attributes  # one per key, plus identity

    uid: int  # identity while editing (never written)
    origin: str | None  # its table's key in the file read; None: added
    name: str
    directory: str
    monitors: tuple[str, ...]  # selectors: names or full ids
    interval: str  # a duration ("30m"), or MANUAL
    order: Order = Order.SHUFFLE
    mode: ImageMode | None = None  # None: as is (no `mode` key)

    @property
    def manual(self) -> bool:
        return self.interval == MANUAL


@dataclass(frozen=True)
class Settings:
    """Everything the settings window edits; defaults as in core.config."""

    playlists: tuple[PlaylistSettings, ...] = ()
    pause_when_locked: bool = True
    pause_when_idle: str | None = DEFAULT_IDLE  # None: never (false)
    favourites_file: str = ""  # "": the default, next to the state database
    to_remove_file: str = ""

    def playlist(self, uid: int) -> PlaylistSettings | None:
        return next((p for p in self.playlists if p.uid == uid), None)

    def replace_playlist(self, playlist: PlaylistSettings) -> Settings:
        return replace(
            self,
            playlists=tuple(
                playlist if p.uid == playlist.uid else p for p in self.playlists
            ),
        )


# -- reading -------------------------------------------------------------------------


def read_settings(text: str) -> Settings:
    """The editable values of a config text; raises ConfigEditError."""
    doc = _parse(text)
    root = doc.unwrap()
    playlists = tuple(
        _read_playlist(uid, name, table)
        for uid, (name, table) in enumerate(_tables(doc))
    )
    locked = root.get("pause_when_locked", True)
    if not isinstance(locked, bool):
        raise ConfigEditError("pause_when_locked must be true or false")
    idle = root.get("pause_when_idle", DEFAULT_IDLE)
    if idle is not False and not isinstance(idle, str):
        raise ConfigEditError("pause_when_idle must be a duration like '5m', or false")
    return Settings(
        playlists=playlists,
        pause_when_locked=locked,
        pause_when_idle=None if idle is False else str(idle).strip(),
        favourites_file=_path_text(root, "favourites_file"),
        to_remove_file=_path_text(root, "to_remove_file"),
    )


def _parse(text: str) -> TOMLDocument:
    try:
        return tomlkit.parse(text)
    except TOMLKitError as err:
        raise ConfigEditError(f"Not valid TOML: {err}") from err


def _tables(doc: TOMLDocument) -> list[tuple[str, Table]]:
    """The playlist tables, in file order."""
    if PLAYLISTS not in doc:
        return []
    section = doc[PLAYLISTS]
    if not isinstance(section, Table):
        raise ConfigEditError(
            "[playlists] is split up or not a table; edit it by hand, or keep "
            "the [playlists.NAME] tables together"
        )
    tables: list[tuple[str, Table]] = []
    for key, item in section.value.body:
        if key is None:
            continue
        if not isinstance(item, Table):
            raise ConfigEditError(
                f"playlists.{key.as_string()} is not written as a "
                f"[playlists.NAME] table; only those can be edited here"
            )
        tables.append((key.key, item))
    return tables


def _read_playlist(uid: int, name: str, table: Table) -> PlaylistSettings:
    raw = table.unwrap()
    where = f"[playlists.{name}]"
    monitors = raw.get("monitors", [])
    if isinstance(monitors, str):
        monitors = [monitors]
    if not isinstance(monitors, list) or not all(isinstance(m, str) for m in monitors):
        raise ConfigEditError(f"{where} monitors must be a list of monitor names")
    interval = raw.get("interval", "")
    directory = raw.get("directory", "")
    if not isinstance(interval, str) or not isinstance(directory, str):
        raise ConfigEditError(f"{where} directory and interval must be strings")
    interval = interval.strip()
    return PlaylistSettings(
        uid=uid,
        origin=name,
        name=name,
        directory=directory,
        monitors=tuple(monitors),
        interval=MANUAL if interval.lower() == MANUAL else interval,
        order=_enum(Order, raw.get("order", Order.SHUFFLE.value), f"{where} order"),
        mode=_enum(ImageMode, raw["mode"], f"{where} mode") if "mode" in raw else None,
    )


def _enum[E: Enum](enum: type[E], value: Any, what: str) -> E:
    try:
        return enum(str(value).strip().lower())
    except ValueError as err:
        choices = ", ".join(str(m.value) for m in enum)
        raise ConfigEditError(f"{what} must be one of: {choices}") from err


def _path_text(root: dict[str, Any], key: str) -> str:
    value = root.get(key, "")
    if not isinstance(value, str):
        raise ConfigEditError(f"{key} must be a path")
    return value


# -- writing -------------------------------------------------------------------------


def render(text: str, base: Settings, edited: Settings) -> str:
    """
    `text` with what changed from `base` to `edited` written into it;
    everything else (comments, order, formatting) as it was. `base` must
    be `read_settings(text)`.
    """
    if edited == base:
        return text
    doc = _parse(text)
    _write_general(doc, base, edited)
    _write_playlists(doc, base, edited)
    out = doc.as_string()
    # Added playlists last, on the result: its table keys are the new
    # names by then (a rename only changes the header in place).
    added = [p for p in edited.playlists if p.origin is None]
    if added:
        doc = _parse(out)
        for playlist in added:
            _add_playlist(doc, playlist)
        out = doc.as_string()
    return _match_newlines(out, text)


def _write_general(doc: TOMLDocument, base: Settings, edited: Settings) -> None:
    if edited.pause_when_locked != base.pause_when_locked:
        _set(doc, "pause_when_locked", tomlkit.boolean(edited.pause_when_locked))
    if edited.pause_when_idle != base.pause_when_idle:
        idle = edited.pause_when_idle
        item: Item = (
            tomlkit.boolean(False)
            if idle is None
            else _string(idle, _existing(doc, "pause_when_idle"))
        )
        _set(doc, "pause_when_idle", item)
    for key in ("favourites_file", "to_remove_file"):
        new = getattr(edited, key)
        if new == getattr(base, key):
            continue
        if new:
            _set(doc, key, _string(new, _existing(doc, key), path=True))
        elif key in doc:
            del doc[key]


def _write_playlists(doc: TOMLDocument, base: Settings, edited: Settings) -> None:
    old = {p.origin: p for p in base.playlists if p.origin is not None}
    tables = dict(_tables(doc))
    for playlist in edited.playlists:
        if playlist.origin is None:
            continue
        table = tables[playlist.origin]
        _write_playlist(table, old[playlist.origin], playlist)
        if playlist.name != playlist.origin:
            _rename(doc[PLAYLISTS], table, playlist.origin, playlist.name)
    kept = {p.origin for p in edited.playlists}
    for origin in old:
        if origin not in kept:
            _delete_table(doc, origin)


def _write_playlist(table: Table, old: PlaylistSettings, new: PlaylistSettings) -> None:
    if new.directory != old.directory:
        like = _existing(table, "directory")
        _set(table, "directory", _string(new.directory, like, path=True))
    if new.monitors != old.monitors:
        _set(table, "monitors", _selectors(new.monitors, _existing(table, "monitors")))
    if new.order != old.order:
        _set(table, "order", _string(new.order.value, _existing(table, "order")))
    if new.interval != old.interval:
        _set(table, "interval", _string(new.interval, _existing(table, "interval")))
    if new.mode != old.mode:
        if new.mode is None:  # as is: no key
            if "mode" in table:
                del table["mode"]
        else:
            _set(table, "mode", _string(new.mode.value, _existing(table, "mode")))


def _add_playlist(doc: TOMLDocument, playlist: PlaylistSettings) -> None:
    if PLAYLISTS not in doc:
        doc.append(PLAYLISTS, tomlkit.table(is_super_table=True))
    section = doc[PLAYLISTS]
    assert isinstance(section, Table)
    table = tomlkit.table()
    table.append("directory", _string(playlist.directory, path=True))
    table.append("monitors", _selectors(playlist.monitors, None))
    table.append("order", playlist.order.value)
    table.append("interval", playlist.interval)
    if playlist.mode is not None:
        table.append("mode", playlist.mode.value)
    # A blank line before its header, unless the file already ends with one.
    rendered = doc.as_string()
    if rendered and not rendered.endswith("\n\n"):
        table.trivia.indent = "\n" if rendered.endswith("\n") else "\n\n"
    section.append(playlist.name, table)


def _rename(section: Item, table: Table, origin: str, name: str) -> None:
    """
    Only the header changes, in place (`[playlists.old]` -> `[playlists.new]`,
    spacing and its comment kept). tomlkit writes the header from the
    table's display name; its key in the document stays the old one, which
    `render` re-parses before adding playlists.
    """
    assert isinstance(section, Table)
    old_raw = next(
        k.as_string()
        for k, _ in section.value.body
        if k is not None and k.key == origin
    )
    new_raw = SingleKey(name).as_string()  # quoted if it must be
    display = table.display_name or f"{PLAYLISTS}.{old_raw}"
    stripped = display.rstrip()
    if stripped.endswith(old_raw):
        table.display_name = (
            stripped[: -len(old_raw)] + new_raw + display[len(stripped) :]
        )
    else:
        table.display_name = f"{PLAYLISTS}.{new_raw}"


def _delete_table(doc: TOMLDocument, origin: str) -> None:
    """
    Remove a playlist's table, with the comment block right above its header
    (after a blank line). tomlkit reads such a block as the end of the
    table before, so: the previous table's block goes, and this table's own
    (above the next header) is kept, in this table's place.
    """
    section = doc[PLAYLISTS]
    assert isinstance(section, Table)
    container = section.value
    # pylint: disable-next=protected-access  # tomlkit has no public index
    index = container._map[SingleKey(origin)]
    assert isinstance(index, int)
    table = container.item(origin)
    assert isinstance(table, Table)
    previous = [v for k, v in container.body[:index] if k is not None]
    if previous and isinstance(previous[-1], Table):
        for position in _attached_comments(previous[-1]):
            previous[-1].value.body[position] = (None, Null())
    later = any(k is not None for k, _ in container.body[index + 1 :])
    kept = (
        "".join(table.value.body[i][1].as_string() for i in _attached_comments(table))
        if later  # else nothing follows: its trailing lines are its own
        else ""
    )
    container.remove(origin)
    if kept:
        # `remove` leaves a placeholder at the index; the comments take it.
        # The first table had none before it: keep a blank line above them.
        container.body[index] = (None, Whitespace(("" if previous else "\n") + kept))


def _attached_comments(table: Table) -> list[int]:
    """
    Where in its body the comment lines after the table's last blank line
    are, if it ends so (they introduce the next table).
    """
    body = table.value.body
    tail: list[int] = []
    for position in range(len(body) - 1, -1, -1):
        key, item = body[position]
        if isinstance(item, Null):
            continue
        if key is not None or not isinstance(item, (Comment, Whitespace)):
            break
        tail.append(position)
    tail.reverse()
    blanks = [i for i, p in enumerate(tail) if isinstance(body[p][1], Whitespace)]
    if not blanks:
        return []  # no blank line: commented-out lines of this table
    return [p for p in tail[blanks[-1] + 1 :] if isinstance(body[p][1], Comment)]


def _existing(target: Table | Container, key: str) -> Item | None:
    item = target.item(key) if key in target else None
    return item if isinstance(item, Item) else None


def _set(target: Table | Container, key: str, item: Item) -> None:
    """Replace the value (keeping its comment and its column), or add the key."""
    old = _existing(target, key)
    if old is not None:
        _keep_comment_column(old, item)
        target[key] = item
        return
    container = target.value if isinstance(target, Table) else target
    last = None
    for k, v in container.body:
        if k is not None and not isinstance(v, (Table, AoT)):
            last = k
    if last is None:
        container.append(key, item)
    else:
        # After the last key, not at the very end: a table's body may end
        # with the comments above the next table's header.
        # pylint: disable-next=protected-access  # no public insert-after
        container._insert_after(last, key, item)


def _keep_comment_column(old: Item, new: Item) -> None:
    """Pad so a comment after the value stays in its column."""
    if isinstance(old, Whitespace) or isinstance(new, Whitespace):
        return
    if not old.trivia.comment:
        return
    old_text, new_text = old.as_string(), new.as_string()
    if "\n" in old_text or "\n" in new_text:
        return
    width = len(old_text) + len(old.trivia.comment_ws)
    new.trivia.comment_ws = " " * max(1, width - len(new_text))


def _string(value: str, like: Item | None = None, *, path: bool = False) -> String:
    """
    A TOML string: literal ('...') like the value it replaces, or for a new
    path (backslashes stay readable), when the text allows it; else basic.
    """
    if like is not None:
        literal = isinstance(like, String) and like.type == StringType.SLL
    else:
        literal = path
    if literal and _literal_ok(value):
        return tomlkit.string(value, literal=True)
    return tomlkit.string(value)


def _literal_ok(value: str) -> bool:
    return "'" not in value and not any(
        (ord(c) < 0x20 and c != "\t") or ord(c) == 0x7F for c in value
    )


def _selectors(selectors: Sequence[str], like: Item | None) -> Array:
    array = tomlkit.array()
    for selector in selectors:
        # Ids hold backslashes: literal strings keep them readable.
        array.append(_string(selector, path="\\" in selector))
    if like is not None and "\n" in like.as_string():
        array.multiline(True)
    return array


def _match_newlines(out: str, original: str) -> str:
    """Lines tomlkit added end in \\n; a file with \\r\\n gets those too."""
    if "\r\n" in original:
        return re.sub(r"(?<!\r)\n", "\r\n", out)
    return out


# -- checking ------------------------------------------------------------------------

Field = tuple[
    int | None, str
]  # (playlist uid, or None for the general settings; field)
NAME, DIRECTORY, MONITORS, INTERVAL = "name", "directory", "monitors", "interval"
IDLE, FAVOURITES, TO_REMOVE = "pause_when_idle", "favourites_file", "to_remove_file"


class NamedMonitor(Protocol):
    """A monitor the desktop lists (protocol.DesktopMonitor, core Monitor)."""

    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...


def validate(
    settings: Settings,
    config_dir: Path,
    listed: Sequence[NamedMonitor],
    base: Settings | None = None,
) -> dict[Field, str]:
    """
    Problems that keep `settings` from being saved, per field. What is
    still as `base` (the file) has it is not held against an edit: a
    playlist's folder must exist only when it is new or changed (an
    unplugged drive must not block changing something else), and a name
    needs no quoting only when it is new or changed.
    """
    written = {p.uid: p.directory for p in base.playlists} if base else {}
    errors: dict[Field, str] = {}
    seen_names: dict[str, str] = {}
    owners: dict[str, str] = {}  # monitor (id, or selector if not listed) -> playlist
    for playlist in settings.playlists:
        uid = playlist.uid
        if (problem := _name_problem(playlist, seen_names)) is not None:
            errors[(uid, NAME)] = problem
        seen_names.setdefault(playlist.name.strip().lower(), playlist.name)
        problem = directory_problem(
            playlist.directory,
            config_dir,
            must_exist=written.get(uid) != playlist.directory,
        )
        if problem is not None:
            errors[(uid, DIRECTORY)] = problem
        if (problem := interval_problem(playlist.interval)) is not None:
            errors[(uid, INTERVAL)] = problem
        if (problem := _monitors_problem(playlist, listed, owners)) is not None:
            errors[(uid, MONITORS)] = problem
    if not settings.playlists:
        errors[(None, PLAYLISTS)] = "Add at least one playlist"
    if settings.pause_when_idle is not None:
        try:
            parse_duration(settings.pause_when_idle)
        except ConfigError as err:
            errors[(None, IDLE)] = str(err)
    for key in (FAVOURITES, TO_REMOVE):
        value: str = getattr(settings, key)
        if value and _resolve(value, config_dir).is_dir():
            errors[(None, key)] = "This is a folder; choose a file"
    return errors


def _name_problem(playlist: PlaylistSettings, seen: dict[str, str]) -> str | None:
    name = playlist.name
    if not name.strip():
        return "Give it a name"
    if name != playlist.origin and not NAME_PATTERN.fullmatch(name):
        return "Use only letters, digits, - and _ (no spaces)"
    if (other := seen.get(name.strip().lower())) is not None:
        return f"Another playlist is called {other}"
    return None


def directory_problem(
    directory: str, config_dir: Path, must_exist: bool = True
) -> str | None:
    if not directory.strip():
        return "Choose a folder"
    path = _resolve(directory, config_dir)
    if must_exist and not path.is_dir():
        return f"No such folder: {path}"
    return None


def interval_problem(interval: str) -> str | None:
    if interval == MANUAL:
        return None
    try:
        seconds = parse_duration(interval)
    except ConfigError:
        return "Use a duration like 45s, 30m, 1h30m or 1d"
    if seconds < MIN_INTERVAL_S:
        return "At least 10s"
    return None


def _monitors_problem(
    playlist: PlaylistSettings,
    listed: Sequence[NamedMonitor],
    owners: dict[str, str],
) -> str | None:
    if not playlist.monitors:
        return "No monitor: choose one, or delete the playlist"
    for selector in playlist.monitors:
        monitor = resolve(selector, listed)
        key = monitor.id if monitor is not None else selector.strip().lower()
        first = owners.setdefault(key, playlist.name)
        if first != playlist.name:
            label = monitor.name if monitor is not None else selector
            return f"{label} is also in {first}; a monitor follows one playlist"
    return None


def _resolve(text: str, config_dir: Path) -> Path:
    path = Path(text.strip()).expanduser()
    return path if path.is_absolute() else config_dir / path


# -- monitors ------------------------------------------------------------------------


def resolve[M: NamedMonitor](selector: str, listed: Sequence[M]) -> M | None:
    """
    The listed monitor a selector names: its full id, else its exact name
    (case-insensitive, as core.selection.find_exact); None if it names none,
    or a name several monitors share.
    """
    wanted = selector.strip().lower()
    for matches in (
        [m for m in listed if m.id.lower() == wanted],
        [m for m in listed if m.name.lower() == wanted],
    ):
        if len(matches) == 1:
            return matches[0]
        if matches:
            return None
    return None


def choose_selector(monitor: NamedMonitor, listed: Sequence[NamedMonitor]) -> str:
    """Its name if no other listed monitor has it, else its full id."""
    same = [m for m in listed if m.name.lower() == monitor.name.lower()]
    return monitor.name if len(same) <= 1 else monitor.id


def owner(
    settings: Settings, monitor: NamedMonitor, listed: Sequence[NamedMonitor]
) -> PlaylistSettings | None:
    """The playlist the monitor is in (the first, should several name it)."""
    for playlist in settings.playlists:
        for selector in playlist.monitors:
            found = resolve(selector, listed)
            if found is not None and found.id == monitor.id:
                return playlist
    return None


def assign_monitor(
    settings: Settings,
    monitor: NamedMonitor,
    listed: Sequence[NamedMonitor],
    target: int | None,
) -> Settings:
    """
    Make playlist `target` (a uid; None: no playlist) drive `monitor`,
    taking it out of any other playlist. A playlist that keeps the monitor
    keeps its selector as written; a new one gets `choose_selector`.
    """
    playlists = []
    for playlist in settings.playlists:
        others = tuple(
            s
            for s in playlist.monitors
            if (found := resolve(s, listed)) is None or found.id != monitor.id
        )
        if playlist.uid == target:
            if len(others) == len(playlist.monitors):  # not in it yet
                others += (choose_selector(monitor, listed),)
            else:
                others = playlist.monitors  # in it already: as written
        playlists.append(replace(playlist, monitors=others))
    return replace(settings, playlists=tuple(playlists))


def remove_selector(settings: Settings, uid: int, selector: str) -> Settings:
    """Take a selector out of a playlist (e.g. a monitor not listed now)."""
    playlist = settings.playlist(uid)
    if playlist is None:
        return settings
    kept = tuple(s for s in playlist.monitors if s != selector)
    return settings.replace_playlist(replace(playlist, monitors=kept))


def new_playlist(
    settings: Settings, uid: int, monitors: tuple[str, ...] = ()
) -> PlaylistSettings:
    """A playlist to add: a free name, the usual defaults, no folder yet."""
    taken = {p.name.lower() for p in settings.playlists}
    name, number = "playlist", 1
    while name in taken:
        number += 1
        name = f"playlist-{number}"
    return PlaylistSettings(
        uid=uid,
        origin=None,
        name=name,
        directory="",
        monitors=monitors,
        interval=NEW_INTERVAL,
        order=Order.SHUFFLE,
        mode=ImageMode.FILL,
    )


# -- the file ------------------------------------------------------------------------


def read_file(path: Path) -> str:
    """The file's text exactly (line endings kept); raises OSError, ConfigEditError."""
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as err:
        raise ConfigEditError(f"{path} is not UTF-8 text: {err}") from err


def write_file(path: Path, text: str, expected: str) -> None:
    """
    Replace the file with `text` atomically (temp file + replace), only if
    it still holds `expected` (what was read): else ConfigConflictError,
    and nothing is written.
    """
    try:
        current: str | None = read_file(path)
    except FileNotFoundError:
        current = None
    if current != expected:
        raise ConfigConflictError(
            f"{path.name} changed on disk since it was read here; nothing was " f"saved"
        )
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(text.encode("utf-8"))
    os.replace(temporary, path)
