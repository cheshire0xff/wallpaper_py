"""
What the Library's grid tabs show, without Qt (docs/plan.md at 268a951,
"Grid tabs"): an `Entry` per tile, and `arrange`, which narrows them by
name and sorts them.

Each tab reads its own entries, off the main thread (`jobs.LatestRead`):
All images the playlist's folder (`read_folder`: each image's added time,
`library.scan_files`), Favourites and To remove the lists (`read_list`:
the line order is the added order, as entries are appended), History the
state database (`read_history`, read-only). Upcoming comes from the
daemon's `upcoming` reply (`upcoming_entries`).

An entry works out its path key, name key and folded name when it is made,
so on the reader's thread: for 20k images that takes ~80 ms, after which
sorting takes ~12 ms and narrowing under 1 ms, on the main thread.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ...core.library import natural_key, path_key, scan_files
from ...core.marks import MarkFile, MarkFileBusyError
from ...core.model import ConfigError, WallpaperError
from ...core.store import read_history as read_history_rows

# Name A–Z, Z–A; newest, oldest added (History: shown).
Sort = Literal["name_az", "name_za", "newest", "oldest"]


@dataclass(frozen=True, slots=True)
class Entry:
    """A tile: an image, and what sorts it."""

    path: Path
    # Its place for newest / oldest: when it was added (a timestamp, or a
    # line number in a list), when it was shown (History), or its place in
    # what comes next (Upcoming). Larger is newer.
    order: float
    shown_at: float | None = None  # History: when (the tile's caption)
    key: str = field(init=False, compare=False, repr=False)  # library.path_key
    name_key: tuple[str | int, ...] = field(init=False, compare=False, repr=False)
    folded: str = field(init=False, compare=False, repr=False)  # the name, casefolded

    def __post_init__(self) -> None:
        name = self.path.name
        object.__setattr__(self, "key", path_key(self.path))
        object.__setattr__(self, "name_key", natural_key(name))
        object.__setattr__(self, "folded", name.casefold())


@dataclass(frozen=True)
class Read:
    """A tab's entries as read, or why they couldn't be."""

    entries: tuple[Entry, ...] = ()
    error: str | None = None
    more: bool = False  # there are more than these (Upcoming: "500+")

    def keys(self) -> frozenset[str]:
        return frozenset(e.key for e in self.entries)


def read_failed(error: Exception) -> Read:
    """A read that raised (`LatestRead`): why, in place of the tab's entries."""
    return Read(error=f"Can't read this: {error}")


def lists_failed(error: Exception) -> tuple[Read, Read]:
    """`read_lists` raised: both lists say why."""
    return read_failed(error), read_failed(error)


def read_folder(folder: Path) -> Read:
    """
    A playlist's images, each added when the file came (`scan_files`). A
    folder that isn't there has none (a drive not mounted yet), as for the
    daemon.
    """
    return Read(tuple(Entry(f.path, f.added) for f in scan_files(folder)))


def read_list(file: Path) -> Read:
    """A list's entries, in line order (the last added last)."""
    try:
        paths = MarkFile(file).paths()
    except (ConfigError, MarkFileBusyError) as err:
        return Read(error=str(err))
    return Read(tuple(Entry(p, i) for i, p in enumerate(paths)))


def read_lists(favourites: Path, to_remove: Path) -> tuple[Read, Read]:
    return read_list(favourites), read_list(to_remove)


def read_history(database: Path, playlist: str) -> Read:
    """Every time the playlist (by its id) showed an image, newest first."""
    try:
        rows = read_history_rows(database, playlist)
    except WallpaperError as err:
        return Read(error=str(err))
    return Read(tuple(Entry(r.path, r.shown_at, r.shown_at) for r in rows))


def upcoming_entries(images: Sequence[Path], limit: int) -> Read:
    """
    What a playlist shows next, the next one first: the first `limit` of
    `images` (asked one more than that, to know whether there are more).
    """
    entries = tuple(Entry(p, -i) for i, p in enumerate(images[:limit]))
    return Read(entries, more=len(images) > limit)


def in_folder(entries: Iterable[Entry], folder: Path) -> list[Entry]:
    """The entries inside `folder` (or a folder in it), as `path_key` compares."""
    prefix = path_key(folder).rstrip(os.sep) + os.sep
    return [e for e in entries if e.key.startswith(prefix)]


def arrange(entries: Sequence[Entry], sort: Sort | None, text: str = "") -> list[Entry]:
    """
    The entries whose file name has `text` in it (ignoring case), in the
    sort's order; None keeps theirs (Upcoming).
    """
    needle = text.strip().casefold()
    kept = [e for e in entries if needle in e.folded] if needle else list(entries)
    if sort in ("name_az", "name_za"):
        kept.sort(key=_by_name, reverse=sort == "name_za")
    elif sort in ("newest", "oldest"):
        kept.sort(key=_by_order, reverse=sort == "newest")
    return kept


def _by_name(entry: Entry) -> tuple[tuple[str | int, ...], str]:
    return entry.name_key, entry.key


def _by_order(entry: Entry) -> tuple[float, tuple[str | int, ...]]:
    return entry.order, entry.name_key
