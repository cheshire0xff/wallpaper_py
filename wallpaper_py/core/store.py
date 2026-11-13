"""
App-internal state in SQLite: history, shuffle decks, per-playlist and
per-monitor state. Favourites and to-remove are *not* here: they live only
in their .txt files (see core.marks).

The database is the single source of truth, so a one-shot CLI command
(`wallpaper next`) and a running `wallpaper run` in another process see the
same state. WAL mode lets them read while the other writes. State is per OS
and per config file (see core.paths.state_db) and holds full native paths.

Playlists are keyed by their `id` from the config, not their name: a
renamed playlist keeps its state with nothing to move.

History is in `position` order (not by id): a picked image goes right after
the cursor, between older entries.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .model import MonitorId, WallpaperError

# Pre-release: the schema changes in place, without migrations. A database
# of another version is refused (see Store._check_version).
SCHEMA_VERSION = 4

_SCHEMA = (
    """CREATE TABLE playlist (
        id       TEXT PRIMARY KEY,     -- the config's id
        cursor   INTEGER,                  -- history.id shown now, NULL at first
        elapsed  REAL NOT NULL DEFAULT 0,  -- active seconds since the last change
        deck_pos REAL NOT NULL DEFAULT -1, -- shuffle: position of the last card drawn
        held     INTEGER NOT NULL DEFAULT 0 -- 1: ticks don't advance it
    )""",
    """CREATE TABLE history (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        playlist TEXT NOT NULL,
        position REAL NOT NULL,           -- the order (a pick goes after the cursor)
        path     TEXT NOT NULL,
        shown_at REAL NOT NULL
    )""",
    "CREATE INDEX history_by_position ON history (playlist, position)",
    """CREATE TABLE deck (
        playlist TEXT NOT NULL,
        position REAL NOT NULL,
        path     TEXT NOT NULL,
        PRIMARY KEY (playlist, position)
    ) WITHOUT ROWID""",
    """CREATE TABLE applied (
        monitor_id TEXT PRIMARY KEY,      -- MonitorId.key
        playlist   TEXT NOT NULL,
        path       TEXT NOT NULL,         -- the original image, not the render
        applied_at REAL NOT NULL
    )""",
)

# Where a playlist's id is stored: (table, column).
_PLAYLIST_COLUMNS = (
    ("playlist", "id"),
    ("history", "playlist"),
    ("deck", "playlist"),
    ("applied", "playlist"),
)

# Positions closer than this are renumbered before inserting between them
# (each pick at the same place halves the gap).
_MIN_GAP = 1e-6


@dataclass(frozen=True)
class PlaylistState:
    cursor: int | None
    elapsed: float
    deck_pos: float
    held: bool = False


@dataclass(frozen=True)
class HistoryEntry:
    id: int
    path: Path
    shown_at: float


class Store:
    """
    Thin typed wrapper around the queries; no policy lives here.

    Single statements commit on their own; group related writes with
    `transaction()`.
    """

    # pylint: disable=too-many-public-methods  # one per query

    def __init__(self, path: Path | str) -> None:
        self._path = path
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None, timeout=10.0)
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA synchronous = NORMAL")
        self._create()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._db.in_transaction:  # nested: join the outer one
            yield
            return
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    def _create(self) -> None:
        """Create the schema in a new database; refuse one of another version."""
        with self.transaction():  # IMMEDIATE: two processes can't both create
            (version,) = self._db.execute("PRAGMA user_version").fetchone()
            if version == SCHEMA_VERSION:
                return
            if version != 0:
                raise WallpaperError(
                    f"The state database is from another version of "
                    f"wallpaper_py (schema {version}, this one uses "
                    f"{SCHEMA_VERSION}); delete {self._path} to start afresh "
                    f"(only the history and shuffle state are lost)"
                )
            for statement in _SCHEMA:
                self._db.execute(statement)
            self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # -- playlist ----------------------------------------------------------

    def playlist_state(self, playlist: str) -> PlaylistState:
        row = self._db.execute(
            "SELECT cursor, elapsed, deck_pos, held FROM playlist WHERE id = ?",
            (playlist,),
        ).fetchone()
        if row is None:
            return PlaylistState(cursor=None, elapsed=0.0, deck_pos=-1.0)
        return PlaylistState(
            cursor=row[0], elapsed=row[1], deck_pos=row[2], held=bool(row[3])
        )

    def _ensure_playlist(self, playlist: str) -> None:
        self._db.execute(
            "INSERT INTO playlist (id) VALUES (?) ON CONFLICT DO NOTHING", (playlist,)
        )

    def add_elapsed(self, playlist: str, seconds: float) -> float:
        with self.transaction():
            self._ensure_playlist(playlist)
            (elapsed,) = self._db.execute(
                "UPDATE playlist SET elapsed = elapsed + ? WHERE id = ? "
                "RETURNING elapsed",
                (seconds, playlist),
            ).fetchone()
        return float(elapsed)

    def set_cursor(self, playlist: str, history_id: int) -> None:
        """Show `history_id` now; the interval starts again."""
        with self.transaction():
            self._ensure_playlist(playlist)
            self._db.execute(
                "UPDATE playlist SET cursor = ?, elapsed = 0 WHERE id = ?",
                (history_id, playlist),
            )

    def set_held(self, playlist: str, held: bool) -> bool:
        """Hold or resume the playlist; False if it already was so."""
        with self.transaction():
            self._ensure_playlist(playlist)
            cursor = self._db.execute(
                "UPDATE playlist SET held = ? WHERE id = ? AND held != ?",
                (held, playlist, held),
            )
        return cursor.rowcount > 0

    def set_deck_pos(self, playlist: str, position: float) -> None:
        with self.transaction():
            self._ensure_playlist(playlist)
            self._db.execute(
                "UPDATE playlist SET deck_pos = ? WHERE id = ?", (position, playlist)
            )

    def prune_playlists(self, keep: Iterable[str]) -> list[str]:
        """Drop the state of every playlist not in `keep`; returns their ids."""
        kept = set(keep)
        with self.transaction():
            # A playlist may have rows in some tables only.
            known = " UNION ".join(
                f"SELECT {column} FROM {table}" for table, column in _PLAYLIST_COLUMNS
            )
            rows = self._db.execute(known).fetchall()
            stale = sorted(playlist for (playlist,) in rows if playlist not in kept)
            for playlist in stale:
                for table, column in _PLAYLIST_COLUMNS:
                    self._db.execute(
                        f"DELETE FROM {table} WHERE {column} = ?", (playlist,)
                    )
        return stale

    # -- history -----------------------------------------------------------

    def append_history(self, playlist: str, path: Path, shown_at: float) -> int:
        """A new entry after all others of the playlist."""
        (last,) = self._db.execute(
            "SELECT max(position) FROM history WHERE playlist = ?", (playlist,)
        ).fetchone()
        return self._add_history(playlist, path, shown_at, (last or 0.0) + 1)

    def insert_history(
        self, playlist: str, after: int | None, path: Path, shown_at: float
    ) -> int:
        """
        A new entry right after entry `after`, before the entries that
        followed it; at the end if `after` is None or the last entry.
        """
        with self.transaction():
            bounds = None if after is None else self._gap_after(playlist, after)
            if bounds is not None and bounds[1] - bounds[0] < _MIN_GAP:
                assert after is not None
                self._renumber_history(playlist)
                bounds = self._gap_after(playlist, after)
            if bounds is None:
                return self.append_history(playlist, path, shown_at)
            low, high = bounds
            return self._add_history(playlist, path, shown_at, (low + high) / 2)

    def _gap_after(self, playlist: str, history_id: int) -> tuple[float, float] | None:
        """The positions of entry `history_id` and of the one after it, if any."""
        row = self._db.execute(
            "SELECT h.position, (SELECT min(position) FROM history "
            "WHERE playlist = h.playlist AND position > h.position) "
            "FROM history h WHERE h.id = ? AND h.playlist = ?",
            (history_id, playlist),
        ).fetchone()
        if row is None or row[1] is None:
            return None
        return float(row[0]), float(row[1])

    def _renumber_history(self, playlist: str) -> None:
        """Positions 1, 2, 3, ... in the same order."""
        self._db.execute(
            "WITH ranked AS (SELECT id, row_number() OVER "
            "(ORDER BY position, id) AS n FROM history WHERE playlist = ?) "
            "UPDATE history SET position = ranked.n FROM ranked "
            "WHERE history.id = ranked.id",
            (playlist,),
        )

    def _add_history(
        self, playlist: str, path: Path, shown_at: float, position: float
    ) -> int:
        cursor = self._db.execute(
            "INSERT INTO history (playlist, path, shown_at, position) "
            "VALUES (?, ?, ?, ?)",
            (playlist, str(path), shown_at, position),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def history_entry(self, history_id: int) -> HistoryEntry | None:
        row = self._db.execute(
            "SELECT id, path, shown_at FROM history WHERE id = ?", (history_id,)
        ).fetchone()
        return _history(row) if row else None

    def history_after(
        self, playlist: str, history_id: int, limit: int = 500
    ) -> list[HistoryEntry]:
        """Entries after `history_id` in the history's order, nearest first."""
        rows = self._db.execute(
            "SELECT id, path, shown_at FROM history WHERE playlist = ? AND "
            "position > (SELECT position FROM history WHERE id = ?) "
            "ORDER BY position LIMIT ?",
            (playlist, history_id, limit),
        )
        return [_history(row) for row in rows]

    def history_before(
        self, playlist: str, history_id: int | None = None, limit: int = 500
    ) -> list[HistoryEntry]:
        """Entries before `history_id` (default: all), nearest first."""
        if history_id is None:
            rows = self._db.execute(
                "SELECT id, path, shown_at FROM history WHERE playlist = ? "
                "ORDER BY position DESC LIMIT ?",
                (playlist, limit),
            )
        else:
            rows = self._db.execute(
                "SELECT id, path, shown_at FROM history WHERE playlist = ? AND "
                "position < (SELECT position FROM history WHERE id = ?) "
                "ORDER BY position DESC LIMIT ?",
                (playlist, history_id, limit),
            )
        return [_history(row) for row in rows]

    def latest_history(self, playlist: str) -> HistoryEntry | None:
        entries = self.history_before(playlist, limit=1)
        return entries[0] if entries else None

    # -- shuffle deck --------------------------------------------------------

    def deck_paths(self, playlist: str) -> set[str]:
        """Every card of the current cycle, drawn or not."""
        rows = self._db.execute("SELECT path FROM deck WHERE playlist = ?", (playlist,))
        return {path for (path,) in rows}

    def deck_after(self, playlist: str, position: float) -> list[tuple[float, Path]]:
        """Cards not drawn yet, in order."""
        rows = self._db.execute(
            "SELECT position, path FROM deck "
            "WHERE playlist = ? AND position > ? ORDER BY position",
            (playlist, position),
        )
        return [(pos, Path(path)) for pos, path in rows]

    def new_deck(self, playlist: str, paths: Sequence[Path]) -> None:
        """Replace the deck with `paths` in this order; nothing drawn yet."""
        with self.transaction():
            self._ensure_playlist(playlist)
            self._db.execute("DELETE FROM deck WHERE playlist = ?", (playlist,))
            self._db.executemany(
                "INSERT INTO deck (playlist, position, path) VALUES (?, ?, ?)",
                ((playlist, float(i), str(p)) for i, p in enumerate(paths)),
            )
            self._db.execute(
                "UPDATE playlist SET deck_pos = -1 WHERE id = ?", (playlist,)
            )

    def insert_cards(self, playlist: str, cards: Iterable[tuple[float, Path]]) -> None:
        with self.transaction():
            self._db.executemany(
                "INSERT INTO deck (playlist, position, path) VALUES (?, ?, ?)",
                ((playlist, pos, str(path)) for pos, path in cards),
            )

    def card_position(self, playlist: str, path: Path) -> float | None:
        """Where `path` is in the deck, if it is."""
        row = self._db.execute(
            "SELECT position FROM deck WHERE playlist = ? AND path = ?",
            (playlist, str(path)),
        ).fetchone()
        return None if row is None else float(row[0])

    def first_card_position(self, playlist: str) -> float | None:
        (position,) = self._db.execute(
            "SELECT min(position) FROM deck WHERE playlist = ?", (playlist,)
        ).fetchone()
        return None if position is None else float(position)

    def move_card(self, playlist: str, path: Path, position: float) -> None:
        self._db.execute(
            "UPDATE deck SET position = ? WHERE playlist = ? AND path = ?",
            (position, playlist, str(path)),
        )

    # -- applied images ------------------------------------------------------

    def applied(self, monitor_id: MonitorId) -> Path | None:
        row = self._db.execute(
            "SELECT path FROM applied WHERE monitor_id = ?", (monitor_id.key,)
        ).fetchone()
        return Path(row[0]) if row else None

    def set_applied(
        self, monitor_id: MonitorId, playlist: str, path: Path, applied_at: float
    ) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO applied (monitor_id, playlist, path, applied_at) "
            "VALUES (?, ?, ?, ?)",
            (monitor_id.key, playlist, str(path), applied_at),
        )

    def keep_applied(self, monitor_ids: Iterable[MonitorId]) -> None:
        """Forget what every *other* monitor shows (they are detached)."""
        keep = {m.key for m in monitor_ids}
        rows = self._db.execute("SELECT monitor_id FROM applied").fetchall()
        self._db.executemany(
            "DELETE FROM applied WHERE monitor_id = ?",
            ((m,) for (m,) in rows if m not in keep),
        )

    def forget_applied(self, monitor_id: MonitorId | None = None) -> None:
        """Forget what one monitor (or every monitor) shows."""
        if monitor_id is None:
            self._db.execute("DELETE FROM applied")
        else:
            self._db.execute(
                "DELETE FROM applied WHERE monitor_id = ?", (monitor_id.key,)
            )


def _history(row: tuple[int, str, float]) -> HistoryEntry:
    return HistoryEntry(id=row[0], path=Path(row[1]), shown_at=row[2])
