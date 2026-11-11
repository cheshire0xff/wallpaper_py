"""
App-internal state in SQLite: history, shuffle decks, per-playlist and
per-monitor state. Favourites and to-remove are *not* here: they live only
in their .txt files (see core.marks).

The database is the single source of truth, so a one-shot CLI command
(`wallpaper next`) and a running `wallpaper run` in another process see the
same state. WAL mode lets them read while the other writes. State is per OS
(see core.paths) and holds full native paths.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .model import WallpaperError

SCHEMA_VERSION = 1

_SCHEMA = (
    """CREATE TABLE playlist (
        name     TEXT PRIMARY KEY,
        cursor   INTEGER,                 -- history.id shown now, NULL at first
        elapsed  REAL NOT NULL DEFAULT 0, -- active seconds since the last change
        deck_pos REAL NOT NULL DEFAULT -1 -- shuffle: position of the last card drawn
    )""",
    """CREATE TABLE history (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        playlist TEXT NOT NULL,
        path     TEXT NOT NULL,
        shown_at REAL NOT NULL
    )""",
    "CREATE INDEX history_by_playlist ON history (playlist, id)",
    """CREATE TABLE deck (
        playlist TEXT NOT NULL,
        position REAL NOT NULL,
        path     TEXT NOT NULL,
        PRIMARY KEY (playlist, position)
    ) WITHOUT ROWID""",
    """CREATE TABLE applied (
        monitor_id TEXT PRIMARY KEY,      -- lower-case
        playlist   TEXT NOT NULL,
        path       TEXT NOT NULL,         -- the original image, not the render
        applied_at REAL NOT NULL
    )""",
)


@dataclass(frozen=True)
class PlaylistState:
    cursor: int | None
    elapsed: float
    deck_pos: float


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

    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None, timeout=10.0)
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA synchronous = NORMAL")
        self._migrate()

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

    def _migrate(self) -> None:
        with self.transaction():  # IMMEDIATE: two processes can't both create
            (version,) = self._db.execute("PRAGMA user_version").fetchone()
            if version == SCHEMA_VERSION:
                return
            if version != 0:
                raise WallpaperError(
                    f"State database has schema {version}, this version knows "
                    f"{SCHEMA_VERSION}; was it written by a newer wallpaper_py?"
                )
            for statement in _SCHEMA:
                self._db.execute(statement)
            self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # -- playlist ----------------------------------------------------------

    def playlist_state(self, name: str) -> PlaylistState:
        row = self._db.execute(
            "SELECT cursor, elapsed, deck_pos FROM playlist WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return PlaylistState(cursor=None, elapsed=0.0, deck_pos=-1.0)
        return PlaylistState(cursor=row[0], elapsed=row[1], deck_pos=row[2])

    def _ensure_playlist(self, name: str) -> None:
        self._db.execute(
            "INSERT INTO playlist (name) VALUES (?) ON CONFLICT DO NOTHING", (name,)
        )

    def add_elapsed(self, name: str, seconds: float) -> float:
        with self.transaction():
            self._ensure_playlist(name)
            (elapsed,) = self._db.execute(
                "UPDATE playlist SET elapsed = elapsed + ? WHERE name = ? "
                "RETURNING elapsed",
                (seconds, name),
            ).fetchone()
        return float(elapsed)

    def set_cursor(self, name: str, history_id: int) -> None:
        """Show `history_id` now; the interval starts again."""
        with self.transaction():
            self._ensure_playlist(name)
            self._db.execute(
                "UPDATE playlist SET cursor = ?, elapsed = 0 WHERE name = ?",
                (history_id, name),
            )

    def set_deck_pos(self, name: str, position: float) -> None:
        with self.transaction():
            self._ensure_playlist(name)
            self._db.execute(
                "UPDATE playlist SET deck_pos = ? WHERE name = ?", (position, name)
            )

    # -- history -----------------------------------------------------------

    def append_history(self, name: str, path: Path, shown_at: float) -> int:
        cursor = self._db.execute(
            "INSERT INTO history (playlist, path, shown_at) VALUES (?, ?, ?)",
            (name, str(path), shown_at),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def history_entry(self, history_id: int) -> HistoryEntry | None:
        row = self._db.execute(
            "SELECT id, path, shown_at FROM history WHERE id = ?", (history_id,)
        ).fetchone()
        return _history(row) if row else None

    def history_after(
        self, name: str, history_id: int, limit: int = 500
    ) -> list[HistoryEntry]:
        """Entries newer than `history_id`, oldest first."""
        rows = self._db.execute(
            "SELECT id, path, shown_at FROM history "
            "WHERE playlist = ? AND id > ? ORDER BY id LIMIT ?",
            (name, history_id, limit),
        )
        return [_history(row) for row in rows]

    def history_before(
        self, name: str, history_id: int | None = None, limit: int = 500
    ) -> list[HistoryEntry]:
        """Entries older than `history_id` (default: all), newest first."""
        rows = self._db.execute(
            "SELECT id, path, shown_at FROM history "
            "WHERE playlist = ? AND id < ? ORDER BY id DESC LIMIT ?",
            (name, 2**63 - 1 if history_id is None else history_id, limit),
        )
        return [_history(row) for row in rows]

    def latest_history(self, name: str) -> HistoryEntry | None:
        entries = self.history_before(name, limit=1)
        return entries[0] if entries else None

    # -- shuffle deck --------------------------------------------------------

    def deck_paths(self, name: str) -> set[str]:
        """Every card of the current cycle, drawn or not."""
        rows = self._db.execute("SELECT path FROM deck WHERE playlist = ?", (name,))
        return {path for (path,) in rows}

    def deck_after(self, name: str, position: float) -> list[tuple[float, Path]]:
        """Cards not drawn yet, in order."""
        rows = self._db.execute(
            "SELECT position, path FROM deck "
            "WHERE playlist = ? AND position > ? ORDER BY position",
            (name, position),
        )
        return [(pos, Path(path)) for pos, path in rows]

    def new_deck(self, name: str, paths: Sequence[Path]) -> None:
        """Replace the deck with `paths` in this order; nothing drawn yet."""
        with self.transaction():
            self._ensure_playlist(name)
            self._db.execute("DELETE FROM deck WHERE playlist = ?", (name,))
            self._db.executemany(
                "INSERT INTO deck (playlist, position, path) VALUES (?, ?, ?)",
                ((name, float(i), str(p)) for i, p in enumerate(paths)),
            )
            self._db.execute(
                "UPDATE playlist SET deck_pos = -1 WHERE name = ?", (name,)
            )

    def insert_cards(self, name: str, cards: Iterable[tuple[float, Path]]) -> None:
        with self.transaction():
            self._db.executemany(
                "INSERT INTO deck (playlist, position, path) VALUES (?, ?, ?)",
                ((name, pos, str(path)) for pos, path in cards),
            )

    # -- applied images ------------------------------------------------------

    def applied(self, monitor_id: str) -> Path | None:
        row = self._db.execute(
            "SELECT path FROM applied WHERE monitor_id = ?", (monitor_id.lower(),)
        ).fetchone()
        return Path(row[0]) if row else None

    def set_applied(
        self, monitor_id: str, playlist: str, path: Path, applied_at: float
    ) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO applied (monitor_id, playlist, path, applied_at) "
            "VALUES (?, ?, ?, ?)",
            (monitor_id.lower(), playlist, str(path), applied_at),
        )

    def keep_applied(self, monitor_ids: Iterable[str]) -> None:
        """Forget what every *other* monitor shows (they are detached)."""
        keep = {m.lower() for m in monitor_ids}
        rows = self._db.execute("SELECT monitor_id FROM applied").fetchall()
        self._db.executemany(
            "DELETE FROM applied WHERE monitor_id = ?",
            ((m,) for (m,) in rows if m not in keep),
        )

    def forget_applied(self, monitor_id: str | None = None) -> None:
        """Forget what one monitor (or every monitor) shows."""
        if monitor_id is None:
            self._db.execute("DELETE FROM applied")
        else:
            self._db.execute(
                "DELETE FROM applied WHERE monitor_id = ?", (monitor_id.lower(),)
            )


def _history(row: tuple[int, str, float]) -> HistoryEntry:
    return HistoryEntry(id=row[0], path=Path(row[1]), shown_at=row[2])
