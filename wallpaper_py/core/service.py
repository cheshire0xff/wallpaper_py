"""
The playlist service: rotates wallpapers, walks history, marks images.

Driven by `tick()` (every few seconds, from `wallpaper run` now and a Qt
timer later) and by user commands (`next`, `previous`, `favourite`,
`remove`). The backend, presence hook, clock and random generator are
injected, so all of it is tested with fakes.

Timing: each playlist counts *active* seconds since its last change, i.e.
time while it has an attached monitor, the session is not locked or idle,
and this process is running. A change happens when that reaches the
interval; a manual next / previous starts the count again.

Safety: an image is first shown, then committed (history, deck). If no
monitor of the playlist is attached the change is postponed, never skipped
and never moved to another monitor. A mirrored group changes while at least
one member is attached; a member that comes back gets the current image.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .backend import Backend
from .config import Config, Order, PlaylistConfig
from .library import Library, path_key
from .marks import MarkFile
from .model import (
    Clock,
    ImageLoadError,
    Monitor,
    MonitorNotAttachedError,
    WallpaperError,
)
from .order import Card, ShuffleDeck, next_sorted, upcoming_sorted
from .presence import Presence
from .selection import find_exact, resolve_monitor
from .setter import apply_wallpaper
from .store import HistoryEntry, Store

log = logging.getLogger(__name__)

# Longer gaps between ticks (sleep, hibernation, a stopped process) are not
# counted: nobody was looking at the wallpaper.
MAX_TICK_GAP = 60.0
# Unreadable images to skip in one go before giving up until the next tick.
MAX_ATTEMPTS = 20


class PlaylistError(WallpaperError):
    """Unknown playlist, or nothing to show."""


@dataclass(frozen=True)
class Change:
    playlist: str
    image: Path
    reason: str  # "scheduled", "next", "previous", "restored"
    shown_on: tuple[str, ...]  # monitor names
    missed: tuple[str, ...] = ()  # detached members; they get it when back

    def describe(self) -> str:
        text = f"{self.playlist}: {self.image.name} on {', '.join(self.shown_on)}"
        if self.missed:
            text += f" (later on {', '.join(self.missed)})"
        return text


@dataclass(frozen=True)
class MemberStatus:
    selector: str
    monitor: Monitor | None  # None: not listed right now


@dataclass(frozen=True)
class PlaylistStatus:
    playlist: PlaylistConfig
    members: tuple[MemberStatus, ...]
    current: Path | None
    elapsed: float
    upcoming: tuple[Path, ...]


@dataclass(frozen=True)
class _Candidate:
    image: Path
    history_id: int | None = None  # walking existing history
    card: Card | None = None  # drawn from the shuffle deck


class PlaylistService:
    # pylint: disable=too-many-instance-attributes,too-many-arguments
    def __init__(
        self,
        config: Config,
        backend: Backend,
        presence: Presence,
        store: Store,
        render_dir: Path,
        *,
        clock: Clock = time.time,
        rng: random.Random | None = None,
        library: Library | None = None,
    ) -> None:
        self.config = config
        self._backend = backend
        self._presence = presence
        self._store = store
        self._render_dir = render_dir
        self._clock = clock
        self._rng = rng or random.Random()
        self._library = library or Library(clock)
        self.favourites = MarkFile(config.favourites_file)
        self.to_remove = MarkFile(config.to_remove_file)
        self._broken: set[str] = set()  # path keys that failed to load
        self._last_tick: float | None = None
        self._pause_reason: str | None = None
        self._postponed: set[str] = set()

    # -- scheduling ----------------------------------------------------------

    def restore(self) -> None:
        """
        Forget what each monitor shows, so the next tick puts every
        playlist's current image back (after a reboot, or after another tool
        changed the wallpaper while this one was not running).
        """
        self._store.forget_applied()

    def tick(self) -> list[Change]:
        """Advance due playlists and fill in returning monitors."""
        elapsed = self._seconds_since_last_tick()
        self._update_pause()
        monitors = self._backend.monitors()
        self._store.keep_applied(m.id for m in monitors if m.attached)
        removed = self.to_remove.keys()
        changes: list[Change] = []
        for playlist in self.config.playlists:
            try:
                change = self._tick_playlist(playlist, monitors, removed, elapsed)
            except MonitorNotAttachedError as err:  # detached since the snapshot
                log.info("%s", err)
                continue
            except WallpaperError as err:
                log.warning("%s: %s", playlist.name, err)
                continue
            if change is not None:
                changes.append(change)
        return changes

    def _tick_playlist(
        self,
        playlist: PlaylistConfig,
        monitors: Sequence[Monitor],
        removed: set[str],
        elapsed: float,
    ) -> Change | None:
        attached = self._attached(playlist, monitors)
        if not attached:
            if playlist.name not in self._postponed:
                log.info("%s: no monitor attached, rotation postponed", playlist.name)
                self._postponed.add(playlist.name)
            return None
        if playlist.name in self._postponed:
            log.info("%s: monitor attached again, rotation resumed", playlist.name)
            self._postponed.discard(playlist.name)

        current = self._current(playlist)
        due = current is None or not self._showable(current.path, removed)
        if self._pause_reason is None:
            due |= self._store.add_elapsed(playlist.name, elapsed) >= playlist.interval
        if due:
            return self._step(
                playlist, attached, lambda: self._peek_next(playlist), "scheduled"
            )
        assert current is not None
        return self._fill_in(playlist, attached, current.path)

    def _seconds_since_last_tick(self) -> float:
        now = self._clock()
        last, self._last_tick = self._last_tick, now
        if last is None or not 0 <= now - last <= MAX_TICK_GAP:
            return 0.0
        return now - last

    def _update_pause(self) -> None:
        reason = self.pause_reason()
        if reason != self._pause_reason:
            log.info("Rotation %s", f"paused: {reason}" if reason else "resumed")
            self._pause_reason = reason

    def pause_reason(self) -> str | None:
        """Why rotation is paused right now, or None."""
        if self.config.pause_when_locked and self._presence.session_locked():
            return "session locked"
        limit = self.config.pause_when_idle
        idle = self._presence.idle_seconds()
        if limit is not None and idle is not None and idle >= limit:
            return "idle"
        return None

    # -- user commands -------------------------------------------------------

    def find_playlist(self, selector: str | None) -> PlaylistConfig:
        """
        A playlist by name, or the one driving a monitor (name, unique part
        of a name, or id). With one playlist, the selector may be omitted.
        """
        if selector is None:
            if len(self.config.playlists) == 1:
                return self.config.playlists[0]
            names = ", ".join(p.name for p in self.config.playlists)
            raise PlaylistError(f"Name a playlist or monitor: {names}")
        playlist = self.config.playlist(selector)
        if playlist is not None:
            return playlist
        monitor = resolve_monitor(self._backend.monitors(), selector)
        for playlist in self.config.playlists:
            if any(find_exact([monitor], s) for s in playlist.monitors):
                return playlist
        raise PlaylistError(f"{monitor.name} is not in any playlist")

    def next(self, playlist: PlaylistConfig) -> Change:
        change = self._step(
            playlist,
            self._attached_now(playlist),
            lambda: self._peek_next(playlist),
            "next",
        )
        if change is None:
            raise PlaylistError(f"{playlist.name}: no images in {playlist.directory}")
        return change

    def previous(self, playlist: PlaylistConfig) -> Change | None:
        """None if there is nothing earlier in the history."""
        return self._step(
            playlist,
            self._attached_now(playlist),
            lambda: self._peek_previous(playlist),
            "previous",
        )

    def current(self, playlist: PlaylistConfig) -> Path | None:
        entry = self._current(playlist)
        return entry.path if entry else None

    def favourite(self, playlist: PlaylistConfig) -> tuple[Path, bool]:
        """Mark the current image; returns it and False if already marked."""
        image = self._require_current(playlist)
        return image, self.favourites.add(image)

    def remove(self, playlist: PlaylistConfig) -> tuple[Path, Change | None]:
        """
        Put the current image on the to-remove list (it is only listed and
        hidden from rotation, never deleted) and move on to the next one.
        The change is None if no monitor is attached; the next tick then
        moves on.
        """
        image = self._require_current(playlist)
        self.to_remove.add(image)
        try:
            return image, self.next(playlist)
        except MonitorNotAttachedError:
            return image, None

    def status(self, upcoming: int = 3) -> list[PlaylistStatus]:
        monitors = self._backend.monitors()
        removed = self.to_remove.keys()
        result: list[PlaylistStatus] = []
        for playlist in self.config.playlists:
            members = tuple(
                MemberStatus(s, find_exact(monitors, s)) for s in playlist.monitors
            )
            result.append(
                PlaylistStatus(
                    playlist=playlist,
                    members=members,
                    current=self.current(playlist),
                    elapsed=self._store.playlist_state(playlist.name).elapsed,
                    upcoming=tuple(self._upcoming(playlist, removed, upcoming)),
                )
            )
        return result

    # -- choosing images -----------------------------------------------------

    def _current(self, playlist: PlaylistConfig) -> HistoryEntry | None:
        cursor = self._store.playlist_state(playlist.name).cursor
        return None if cursor is None else self._store.history_entry(cursor)

    def _require_current(self, playlist: PlaylistConfig) -> Path:
        image = self.current(playlist)
        if image is None:
            raise PlaylistError(f"{playlist.name} has not shown an image yet")
        return image

    def _showable(self, image: Path, removed: set[str]) -> bool:
        key = path_key(image)
        return key not in removed and key not in self._broken and image.is_file()

    def _peek_next(self, playlist: PlaylistConfig) -> _Candidate | None:
        """Forward in history if we went back, else a new image."""
        removed = self.to_remove.keys()
        cursor = self._store.playlist_state(playlist.name).cursor
        if cursor is not None:
            for entry in self._store.history_after(playlist.name, cursor):
                if self._showable(entry.path, removed):
                    return _Candidate(entry.path, history_id=entry.id)

        images = self._library.images(playlist.directory)
        scanned = {str(p) for p in images}

        def drawable(image: Path) -> bool:
            return str(image) in scanned and self._showable(image, removed)

        latest = self._store.latest_history(playlist.name)
        latest_path = latest.path if latest else None
        if playlist.order is Order.SORTED:
            image = next_sorted(images, latest_path, drawable)
            return _Candidate(image) if image else None
        card = self._deck(playlist).peek(images, drawable, avoid=latest_path)
        return _Candidate(card.path, card=card) if card else None

    def _peek_previous(self, playlist: PlaylistConfig) -> _Candidate | None:
        removed = self.to_remove.keys()
        cursor = self._store.playlist_state(playlist.name).cursor
        if cursor is None:
            return None
        for entry in self._store.history_before(playlist.name, cursor):
            if self._showable(entry.path, removed):
                return _Candidate(entry.path, history_id=entry.id)
        return None

    def _upcoming(
        self, playlist: PlaylistConfig, removed: set[str], limit: int
    ) -> list[Path]:
        def showable(image: Path) -> bool:
            return self._showable(image, removed)

        result: list[Path] = []
        cursor = self._store.playlist_state(playlist.name).cursor
        if cursor is not None:
            result = [
                e.path
                for e in self._store.history_after(playlist.name, cursor, limit)
                if showable(e.path)
            ]
        if len(result) >= limit:
            return result[:limit]
        images = self._library.images(playlist.directory)
        if playlist.order is Order.SORTED:
            latest = self._store.latest_history(playlist.name)
            more = upcoming_sorted(
                images, latest.path if latest else None, showable, limit
            )
        else:
            more = [
                c.path for c in self._deck(playlist).upcoming(images, showable, limit)
            ]
        return (result + more)[:limit]

    def _deck(self, playlist: PlaylistConfig) -> ShuffleDeck:
        return ShuffleDeck(self._store, playlist.name, self._rng)

    # -- showing images ------------------------------------------------------

    def _attached(
        self, playlist: PlaylistConfig, monitors: Sequence[Monitor]
    ) -> list[Monitor]:
        members = (find_exact(monitors, s) for s in playlist.monitors)
        return [m for m in members if m is not None and m.attached]

    def _attached_now(self, playlist: PlaylistConfig) -> list[Monitor]:
        attached = self._attached(playlist, self._backend.monitors())
        if not attached:
            raise MonitorNotAttachedError(
                f"None of {playlist.name}'s monitors is attached "
                f"({', '.join(playlist.monitors)})"
            )
        return attached

    def _step(
        self,
        playlist: PlaylistConfig,
        members: Sequence[Monitor],
        peek: Callable[[], _Candidate | None],
        reason: str,
    ) -> Change | None:
        """
        Show the first candidate that loads, then commit it.

        Raises:
            MonitorNotAttachedError: every member detached meanwhile;
                nothing was consumed.
        """
        for _ in range(MAX_ATTEMPTS):
            candidate = peek()
            if candidate is None:
                return None
            try:
                shown, missed = self._show(playlist, candidate.image, members)
            except ImageLoadError as err:
                log.warning("%s: skipping %s", playlist.name, err)
                self._broken.add(path_key(candidate.image))
                continue
            self._commit(playlist, candidate, shown)
            return Change(
                playlist.name,
                candidate.image,
                reason,
                tuple(m.name for m in shown),
                tuple(m.name for m in missed),
            )
        return None

    def _show(
        self, playlist: PlaylistConfig, image: Path, members: Sequence[Monitor]
    ) -> tuple[list[Monitor], list[Monitor]]:
        """
        Set `image` on each member (re-checked by the setter right before).

        Raises:
            ImageLoadError: the image can't be rendered.
            MonitorNotAttachedError: it reached no member at all.
        """
        shown: list[Monitor] = []
        missed: list[Monitor] = []
        for monitor in members:
            try:
                apply_wallpaper(
                    self._backend, monitor.id, image, playlist.mode, self._render_dir
                )
            except MonitorNotAttachedError as err:
                log.info("%s: %s", playlist.name, err)
                missed.append(monitor)
            else:
                shown.append(monitor)
        if not shown:
            raise MonitorNotAttachedError(
                f"{playlist.name}: no monitor attached; change postponed"
            )
        return shown, missed

    def _commit(
        self, playlist: PlaylistConfig, candidate: _Candidate, shown: Sequence[Monitor]
    ) -> None:
        now = self._clock()
        with self._store.transaction():
            history_id = candidate.history_id
            if history_id is None:
                history_id = self._store.append_history(
                    playlist.name, candidate.image, now
                )
                if candidate.card is not None:
                    self._deck(playlist).draw(candidate.card)
            self._store.set_cursor(playlist.name, history_id)
            for monitor in shown:
                self._store.set_applied(monitor.id, playlist.name, candidate.image, now)

    def _fill_in(
        self, playlist: PlaylistConfig, attached: Sequence[Monitor], current: Path
    ) -> Change | None:
        """Give the current image to members that don't show it (came back)."""
        stale = [m for m in attached if self._store.applied(m.id) != current]
        if not stale:
            return None
        try:
            shown, missed = self._show(playlist, current, stale)
        except ImageLoadError as err:
            log.warning("%s: %s", playlist.name, err)
            self._broken.add(path_key(current))
            return self._step(
                playlist, attached, lambda: self._peek_next(playlist), "scheduled"
            )
        now = self._clock()
        with self._store.transaction():
            for monitor in shown:
                self._store.set_applied(monitor.id, playlist.name, current, now)
        return Change(
            playlist.name,
            current,
            "restored",
            tuple(m.name for m in shown),
            tuple(m.name for m in missed),
        )
