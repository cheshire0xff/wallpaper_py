"""
The playlist service: rotates wallpapers, walks history, marks images.

Driven by `tick()` (every few seconds, from the app's timer) and by user
commands (`next`, `previous`, `pick`, `favourite`, `remove`, `hold`,
`resume`, `reset_cycle`, ...). The backend, presence hook, clock and
random generator are injected, so all of it is tested with fakes.

Timing: each playlist counts *active* seconds since its last change, i.e.
time while it has an attached monitor, the session is not locked or idle,
and this process is running. A change happens when that reaches the
interval; a next / previous / pick starts the count again.

A held playlist (and a manual one, without an interval) does not rotate:
its count stands still and ticks never choose a new image for it, not even
a first one. They still give its current image to a monitor that comes
back, and replace a current image that can't be shown any more (listed for
removal, deleted, unreadable).

Pick shows a chosen image, from any directory, as if `next` had chosen it:
it goes into the history right after the current entry.

Stats count what a playlist picks up (`stats`, `list_stats`): its images,
seen and unseen in this shuffle cycle (or sorted walk), skipped; and the
lists. `reset_cycle` starts a shuffle cycle over.

Settings: the service runs on the settings stored in the database, and
`save_settings` stores new ones and uses them at once: a monitor whose
playlist or look changed is rendered again on the next tick.

Safety: an image is first shown, then committed (history, deck). If no
monitor of the playlist is attached the change is postponed, never skipped
and never moved to another monitor. A mirrored group changes while at least
one member is attached; a member that comes back gets the current image.
"""

from __future__ import annotations

import bisect
import logging
import os
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .backend import Backend
from .imaging import check_image
from .library import Library, natural_key, path_key
from .marks import MarkFile, MarkFileBusyError
from .model import (
    Clock,
    ConfigError,
    ImageLoadError,
    Monitor,
    MonitorId,
    MonitorNotAttachedError,
    WallpaperError,
)
from .order import Card, ShuffleDeck, next_sorted, upcoming_sorted
from .presence import Presence
from .selection import resolve_monitor
from .settings import (
    NO_PLAYLISTS,
    Order,
    PlaylistSettings,
    Settings,
    SettingsError,
    check_folders,
    check_list_files,
    check_settings,
)
from .setter import apply_wallpaper
from .store import HistoryEntry, Store

log = logging.getLogger(__name__)

# Gaps between ticks longer than this, and longer than two tick periods
# (sleep, hibernation, a stopped process), are not counted: nobody was
# looking at the wallpaper.
MAX_TICK_GAP = 60.0
# Unreadable images to skip in one go before giving up until the next tick.
MAX_ATTEMPTS = 20


class PlaylistError(WallpaperError):
    """A playlist command that can't be done."""


class UnknownTargetError(PlaylistError):
    """No playlist matches the target, or none was named among several."""


class NoImagesError(PlaylistError):
    """The playlist's directory has nothing to show."""


class NothingShownError(PlaylistError):
    """The playlist has not shown an image yet, so there is no current one."""


class ManualPlaylistError(PlaylistError):
    """Hold / resume on a manual playlist, which never rotates anyway."""


ChangeReason = Literal["scheduled", "next", "previous", "picked", "restored"]


@dataclass(frozen=True)
class Change:
    playlist: str
    image: Path
    reason: ChangeReason
    shown_on: tuple[str, ...]  # monitor names
    missed: tuple[str, ...] = ()  # detached members; they get it when back

    def describe(self) -> str:
        text = f"{self.playlist}: {self.image.name} on {', '.join(self.shown_on)}"
        if self.missed:
            text += f" (later on {', '.join(self.missed)})"
        return text


@dataclass(frozen=True)
class MemberStatus:
    monitor_id: MonitorId  # as the settings spell it
    monitor: Monitor | None  # None: not listed right now


def _members_of(
    member_ids: Sequence[MonitorId], monitors: Sequence[Monitor]
) -> tuple[MemberStatus, ...]:
    """A playlist's monitors, as listed now."""
    listed = {m.id: m for m in monitors}
    return tuple(MemberStatus(i, listed.get(i)) for i in member_ids)


def _attached(members: Sequence[MemberStatus]) -> list[Monitor]:
    """The attached ones; a monitor listed twice once."""
    attached = {
        m.monitor.id: m.monitor
        for m in members
        if m.monitor is not None and m.monitor.attached
    }
    return list(attached.values())


@dataclass(frozen=True)
class PlaylistStatus:
    playlist: PlaylistSettings
    members: tuple[MemberStatus, ...]
    current: Path | None
    elapsed: float
    upcoming: tuple[Path, ...]
    held: bool = False  # never for a manual playlist


@dataclass(frozen=True)
class PlaylistStats:  # pylint: disable=too-many-instance-attributes
    """
    What a playlist picks up from its folder. images = seen + unseen +
    skipped. A sorted playlist's seen / unseen: up to and including the
    walk's image / after it, in name order.
    """

    name: str
    directory: Path
    order: Order
    scan_age: float  # seconds since the folder was scanned (cached up to 5 min)
    images: int  # found in the folder
    seen: int  # drawn in this shuffle cycle (picked ones too)
    unseen: int  # still to come, new files too
    skipped: int  # listed to remove, or failed to load
    favourites_here: int  # favourites entries among the images
    to_remove_here: int  # to-remove entries among the images

    @property
    def percent_seen(self) -> int:
        """Of seen + unseen; 100 only once all are seen."""
        total = self.seen + self.unseen
        if total == 0:
            return 0
        percent = round(100 * self.seen / total)
        return min(percent, 99) if self.unseen else 100


@dataclass(frozen=True)
class ListStats:
    """A list file's entries (lines naming a path), and how many exist."""

    file: Path
    listed: int
    available: int


@dataclass(frozen=True)
class _Candidate:
    image: Path
    history_id: int | None = None  # walking existing history
    card: Card | None = None  # drawn from the shuffle deck


class PlaylistService:
    # pylint: disable=too-many-instance-attributes,too-many-arguments
    # pylint: disable=too-many-public-methods
    def __init__(
        self,
        backend: Backend,
        presence: Presence,
        store: Store,
        render_dir: Path,
        data_dir: Path,
        *,
        clock: Clock = time.time,
        rng: random.Random | None = None,
        library: Library | None = None,
        tick_period: float = 5.0,
    ) -> None:
        """
        Runs on the settings in `store`. `data_dir`: where the list files
        are unless the settings say otherwise. `tick_period`: how often the
        caller calls `tick()`, in seconds.

        Raises:
            ConfigError: a list file can't be used.
        """
        self.settings = store.settings()
        self._backend = backend
        self._presence = presence
        self._store = store
        self._render_dir = render_dir
        self._data_dir = data_dir
        self._clock = clock
        self._rng = rng or random.Random()
        self._library = library or Library(clock)
        self.favourites, self.to_remove = _open_lists(self.settings, data_dir)
        self._broken: set[str] = set()  # path keys that failed to load
        self._waiting_for_removed = False  # logged once while unknown
        self._last_tick: float | None = None
        self._max_tick_gap = max(MAX_TICK_GAP, 2 * tick_period)
        self._pause_reason: str | None = None
        self._postponed: set[str] = set()  # playlist ids: a rename keeps it
        # Each attached monitor's size at the last tick.
        self._sizes: dict[MonitorId, tuple[int, int]] = {}

    # -- scheduling ----------------------------------------------------------

    def restore(self) -> None:
        """
        Forget what each monitor shows, so the next tick puts every
        playlist's current image back (after a reboot, or after another tool
        changed the wallpaper while this one was not running).
        """
        self._store.forget_applied()

    def revision(self) -> int:
        """The stored settings' revision (see `save_settings`)."""
        return self._store.revision()

    def save_settings(self, settings: Settings, revision: int | None) -> int:
        """
        Store `settings` in place of the current ones and run on them from
        now on; returns the new revision. Deleting a playlist deletes its
        state. A monitor whose playlist or look changed is rendered again
        on the next tick (the caller ticks before replying).

        `revision`: the one the edit was made on; None for any (tests).

        Raises:
            SettingsError: a value that can't be used; nothing was saved.
            StaleSettingsError: the stored settings changed since
                `revision`; nothing was saved.
        """
        check_settings(settings)
        check_folders(settings, self.settings)
        check_list_files(settings, self._data_dir)
        old = self.settings
        lists = (self.favourites, self.to_remove)
        if settings.list_files(self._data_dir) != old.list_files(self._data_dir):
            try:
                lists = _open_lists(settings, self._data_dir)
            except ConfigError as err:
                raise SettingsError(str(err)) from err
        new_revision = self._store.save_settings(settings, revision)
        self.settings = settings
        self.favourites, self.to_remove = lists
        self._postponed &= {p.id for p in settings.playlists}
        changed = {
            m.id
            for m in (*old.monitors, *settings.monitors)
            if old.look(m.id) != settings.look(m.id)
            or _playlist_of(old, m.id) != _playlist_of(settings, m.id)
        }
        for monitor_id in changed:
            self._store.forget_applied(monitor_id)
        return new_revision

    def tick(self) -> list[Change]:
        """
        Advance due playlists and fill in returning monitors.

        Raises:
            ConfigError: a list file went bad (see core.marks).
        """
        elapsed = self._seconds_since_last_tick()
        self._update_pause()
        monitors = self._backend.monitors()
        members = self._members(monitors)
        self._store.keep_applied(m.id for m in monitors if m.attached)
        self._forget_resized(monitors)
        try:
            removed = self._removed()
        except MarkFileBusyError as err:
            # Not even a returning monitor gets an image: it could be one
            # listed for removal. Time still counts, so a playlist that
            # comes due meanwhile changes as soon as the list can be read.
            if not self._waiting_for_removed:
                log.warning("%s", err)
                self._waiting_for_removed = True
            for playlist in self.settings.playlists:
                if _attached(members[playlist.id]):
                    self._accrue(playlist, elapsed)
            return []
        if self._waiting_for_removed:
            log.info("%s can be read again: rotating", self.to_remove.file)
            self._waiting_for_removed = False
        changes: list[Change] = []
        for playlist in self.settings.playlists:
            try:
                change = self._tick_playlist(
                    playlist, _attached(members[playlist.id]), removed, elapsed
                )
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
        playlist: PlaylistSettings,
        attached: list[Monitor],
        removed: set[str],
        elapsed: float,
    ) -> Change | None:
        if not attached:
            if playlist.id not in self._postponed:
                log.info("%s: no monitor attached, rotation postponed", playlist.name)
                self._postponed.add(playlist.id)
            return None
        if playlist.id in self._postponed:
            log.info("%s: monitor attached again, rotation resumed", playlist.name)
            self._postponed.discard(playlist.id)

        current = self._current(playlist)
        if self._store.playlist_state(playlist.id).folder_changed:
            # Saved with another folder: show one of its images now (even
            # held or manual: a new folder is the user's own choice).
            change = self._step(
                playlist,
                attached,
                lambda: self._peek_next(playlist, history=False),
                "scheduled",
            )
            if change is not None:
                return change
            # Nothing to show in it (yet): on as before, from the history.
        if current is not None and not self._showable(current.path, removed):
            due = True  # even when held: it must not stay on screen
        elif playlist.interval is None or self.held(playlist):
            due = False  # never rotates by itself, nor shows a first image
        else:
            spent = self._accrue(playlist, elapsed)
            due = current is None or (spent is not None and spent >= playlist.interval)
        if due:
            return self._step(
                playlist, attached, lambda: self._peek_next(playlist), "scheduled"
            )
        if current is None:  # held or manual, nothing shown yet
            return None
        return self._fill_in(playlist, attached, current.path)

    def _forget_resized(self, monitors: Sequence[Monitor]) -> None:
        """
        Forget what a monitor whose resolution changed shows, so it gets its
        image again, rendered for the new size.
        """
        sizes = {m.id: m.rect.size for m in monitors if m.rect is not None}
        for monitor in monitors:
            old = self._sizes.get(monitor.id)
            if old is not None and monitor.id in sizes and sizes[monitor.id] != old:
                log.info("%s changed resolution: rendering again", monitor.name)
                self._store.forget_applied(monitor.id)
        self._sizes = sizes

    def _accrue(self, playlist: PlaylistSettings, elapsed: float) -> float | None:
        """
        Count `elapsed` active seconds towards the playlist's interval: its
        total since the last change, or None if time doesn't count now
        (manual, held, or rotation paused).
        """
        if playlist.interval is None or self.held(playlist) or self._pause_reason:
            return None
        return self._store.add_elapsed(playlist.id, elapsed)

    def _seconds_since_last_tick(self) -> float:
        now = self._clock()
        last, self._last_tick = self._last_tick, now
        if last is None or not 0 <= now - last <= self._max_tick_gap:
            return 0.0
        return now - last

    def _update_pause(self) -> None:
        reason = self.pause_reason()
        if reason != self._pause_reason:
            log.info("Rotation %s", f"paused: {reason}" if reason else "resumed")
            self._pause_reason = reason

    def pause_reason(self) -> str | None:
        """Why rotation is paused right now, or None."""
        if self.settings.pause_when_locked and self._presence.session_locked():
            return "session locked"
        limit = self.settings.pause_when_idle
        idle = self._presence.idle_seconds()
        if limit is not None and idle is not None and idle >= limit:
            return "idle"
        return None

    # -- user commands -------------------------------------------------------

    def find_playlist(self, selector: str | None) -> PlaylistSettings:
        """
        A playlist by name, or the one driving a monitor (name, unique part
        of a name, or id). With one playlist, the selector may be omitted.
        """
        playlists = self.settings.playlists
        if selector is None:
            if len(playlists) == 1:
                return playlists[0]
            if not playlists:
                raise UnknownTargetError(NO_PLAYLISTS)
            names = ", ".join(p.name for p in playlists)
            raise UnknownTargetError(f"Name a playlist or monitor: {names}")
        playlist = self.settings.playlist(selector)
        if playlist is not None:
            return playlist
        monitor = resolve_monitor(self._backend.monitors(), selector)
        for playlist in playlists:
            if monitor.id in self.settings.members(playlist.id):
                return playlist
        raise UnknownTargetError(f"{monitor.name} is not in any playlist")

    def next(self, playlist: PlaylistSettings) -> Change:
        change = self._step(
            playlist,
            self._attached_now(playlist),
            lambda: self._peek_next(playlist),
            "next",
        )
        if change is None:
            raise NoImagesError(f"{playlist.name}: no images in {playlist.directory}")
        return change

    def previous(self, playlist: PlaylistSettings) -> Change | None:
        """None if there is nothing earlier in the history."""
        return self._step(
            playlist,
            self._attached_now(playlist),
            lambda: self._peek_previous(playlist),
            "previous",
        )

    def pick(self, playlist: PlaylistSettings, image: Path) -> Change:
        """
        Show `image` now, as if `next` had chosen it: it goes into the
        history right after the current entry (so `previous` goes back to
        what was shown before), and the interval starts again. It may come
        from any directory; one from the playlist's own directory counts as
        seen in the current shuffle cycle.

        Raises:
            ImageLoadError: `image` is missing or not an image.
            PlaylistError: `image` is listed for removal.
            MonitorNotAttachedError: no member is attached; nothing changed.
        """
        image = self._as_scanned(playlist, image)
        if path_key(image) in self._removed():
            raise PlaylistError(
                f"{image.name} is listed for removal in {self.to_remove.file}; "
                f"take it off the list to show it"
            )
        check_image(image)
        shown, missed = self._show(playlist, image, self._attached_now(playlist))
        self._broken.discard(path_key(image))  # it loads now
        now = self._clock()
        with self._store.transaction():
            cursor = self._store.playlist_state(playlist.id).cursor
            history_id = self._store.insert_history(playlist.id, cursor, image, now)
            if playlist.order is Order.SHUFFLE and self._in_directory(playlist, image):
                images = self._library.images(playlist.directory)
                self._deck(playlist).mark_seen(images, image)
            self._store.set_cursor(playlist.id, history_id)
            for monitor in shown:
                self._store.set_applied(monitor.id, playlist.id, image, now)
        return Change(
            playlist.name,
            image,
            "picked",
            tuple(m.name for m in shown),
            tuple(m.name for m in missed),
        )

    def held(self, playlist: PlaylistSettings) -> bool:
        """Held by `hold` (a manual playlist never is: it doesn't rotate)."""
        return not playlist.manual and self._store.playlist_state(playlist.id).held

    def hold(self, playlist: PlaylistSettings) -> bool:
        """Stop rotating until `resume`; False if it already was held."""
        self._refuse_manual(playlist, "hold")
        return self._store.set_held(playlist.id, True)

    def resume(self, playlist: PlaylistSettings) -> bool:
        """Rotate again, where the count stopped; False if it wasn't held."""
        self._refuse_manual(playlist, "resume")
        return self._store.set_held(playlist.id, False)

    @staticmethod
    def _refuse_manual(playlist: PlaylistSettings, command: str) -> None:
        if playlist.manual:
            raise ManualPlaylistError(
                f"{playlist.name} is manual: it never changes by itself, so "
                f"there is nothing to {command}"
            )

    def current(self, playlist: PlaylistSettings) -> Path | None:
        entry = self._current(playlist)
        return entry.path if entry else None

    def require_current(self, playlist: PlaylistSettings) -> Path:
        """Raises NothingShownError if the playlist has not shown one yet."""
        image = self.current(playlist)
        if image is None:
            raise NothingShownError(f"{playlist.name} has not shown an image yet")
        return image

    # Favourites and to-remove are `self.favourites` / `self.to_remove`; an
    # image listed to remove by any other way than `remove` (a command naming
    # it, an edit by hand) is replaced at the next tick.

    def remove(self, playlist: PlaylistSettings) -> tuple[Path, bool, Change | None]:
        """
        List the current image for removal (only listed and hidden from
        rotation, never deleted) and move on to the next one now. Returns the
        image, whether it was newly listed, and the change: None if no
        monitor is attached (the next tick then moves on).
        """
        image = self.require_current(playlist)
        listed = self.to_remove.add(image)
        try:
            return image, listed, self.next(playlist)
        except MonitorNotAttachedError:
            return image, listed, None

    def lists_stamp(self) -> tuple[object, ...]:
        """Changes with the list files: their paths, mtimes and sizes."""
        return tuple(
            part
            for marks in (self.favourites, self.to_remove)
            for part in (marks.file, marks.stamp())
        )

    def reset_cycle(self, playlist: PlaylistSettings) -> int:
        """
        Start the shuffle cycle over from the folder's current scan: every
        image unseen again. The newest image shown (the one on screen,
        unless we went back) stays, and isn't first. Returns how many are
        unseen now (the skipped ones aren't). Nothing changes if it raises.

        Raises:
            PlaylistError: the playlist is sorted (no cycle).
            NoImagesError: nothing in its folder (maybe not mounted); the
                cycle is kept.
            MarkFileBusyError: the to-remove list has never been read.
        """
        if playlist.order is Order.SORTED:
            raise PlaylistError(
                f"{playlist.name} is sorted: it walks its folder in name order, "
                f"with no shuffle cycle to start over"
            )
        hidden = self.to_remove.keys() | self._broken
        images = self._library.images(playlist.directory)
        if not images:
            raise NoImagesError(f"{playlist.name}: no images in {playlist.directory}")
        latest = self._store.latest_history(playlist.id)
        self._deck(playlist).reset(images, avoid=latest.path if latest else None)
        return sum(path_key(p) not in hidden for p in images)

    def stats(self, playlist: PlaylistSettings, rescan: bool = False) -> PlaylistStats:
        """
        Counted from the folder's cached scan (`rescan`: scanned again
        first), the deck or the walk, and the lists. Changes nothing (new
        files are not shuffled in yet: they count as unseen).

        Raises:
            MarkFileBusyError: a list has never been read (busy).
        """
        images = self._library.images(playlist.directory, refresh=rescan)
        keys = [path_key(p) for p in images]
        hidden = self.to_remove.keys() | self._broken
        left = [p for p, key in zip(images, keys) if key not in hidden]
        seen = self._seen(playlist, left)
        scanned = set(keys)
        return PlaylistStats(
            name=playlist.name,
            directory=playlist.directory,
            order=playlist.order,
            scan_age=self._library.age(playlist.directory),
            images=len(images),
            seen=seen,
            unseen=len(left) - seen,
            skipped=len(images) - len(left),
            favourites_here=_count_in(self.favourites.paths(), scanned),
            to_remove_here=_count_in(self.to_remove.paths(), scanned),
        )

    def _seen(self, playlist: PlaylistSettings, images: Sequence[Path]) -> int:
        """Of `images` (scanned, so in name order): the ones seen."""
        if playlist.order is Order.SORTED:
            after = self._sorted_after(playlist)
            if after is None:
                return 0
            return bisect.bisect_right(images, natural_key(after), key=natural_key)
        drawn = self._store.deck_drawn(playlist.id)
        return sum(str(p) in drawn for p in images)

    def list_stats(self) -> tuple[ListStats, ListStats]:
        """
        Favourites and to-remove: one `stat` per entry.

        Raises:
            MarkFileBusyError: a list has never been read (busy).
        """
        return _list_stats(self.favourites), _list_stats(self.to_remove)

    def upcoming(self, playlist: PlaylistSettings, limit: int) -> list[Path]:
        """
        The next `limit` images in the order they come: forward in the
        history first (after `previous`), then the rest of the shuffle cycle
        (not the next one: it isn't shuffled yet) or the sorted walk. Images
        listed to remove, gone or unreadable are left out.

        Raises:
            MarkFileBusyError: the to-remove list has never been read.
        """
        return self._upcoming(playlist, self._removed(), limit)

    def monitors(self) -> list[Monitor]:
        """Every monitor the backend lists, detached ones included."""
        return self._backend.monitors()

    def status(
        self, upcoming: int = 3, monitors: Sequence[Monitor] | None = None
    ) -> list[PlaylistStatus]:
        """`monitors`: a fresh `monitors()` the caller already has, if any."""
        if monitors is None:
            monitors = self._backend.monitors()
        try:
            removed: set[str] | None = self._removed() if upcoming else set()
        except MarkFileBusyError:
            removed = None  # unknown: no upcoming images (they may be listed)
        members = self._members(monitors)
        result: list[PlaylistStatus] = []
        for playlist in self.settings.playlists:
            result.append(
                PlaylistStatus(
                    playlist=playlist,
                    members=members[playlist.id],
                    current=self.current(playlist),
                    elapsed=self._store.playlist_state(playlist.id).elapsed,
                    upcoming=(
                        ()
                        if removed is None
                        else tuple(self._upcoming(playlist, removed, upcoming))
                    ),
                    held=self.held(playlist),
                )
            )
        return result

    # -- choosing images -----------------------------------------------------

    def _current(self, playlist: PlaylistSettings) -> HistoryEntry | None:
        cursor = self._store.playlist_state(playlist.id).cursor
        return None if cursor is None else self._store.history_entry(cursor)

    def _removed(self) -> set[str]:
        """
        Path keys of the images listed for removal.

        Raises:
            MarkFileBusyError: the list has never been read (its file is
                busy): no image may be chosen, it could be one listed.
        """
        try:
            return self.to_remove.keys()
        except MarkFileBusyError as err:
            raise MarkFileBusyError(
                f"No image is chosen until the to-remove list can be read: {err}"
            ) from err

    def _showable(self, image: Path, removed: set[str]) -> bool:
        key = path_key(image)
        return key not in removed and key not in self._broken and image.is_file()

    def _peek_next(
        self, playlist: PlaylistSettings, history: bool = True
    ) -> _Candidate | None:
        """
        Forward in history if we went back, else a new image. `history`
        False: a new image from the folder (it changed; the history after
        the cursor is from the old one).
        """
        removed = self._removed()
        cursor = self._store.playlist_state(playlist.id).cursor
        if cursor is not None and history:
            for entry in self._store.history_after(playlist.id, cursor):
                if self._showable(entry.path, removed):
                    return _Candidate(entry.path, history_id=entry.id)

        images = self._library.images(playlist.directory)
        scanned = {str(p) for p in images}

        def drawable(image: Path) -> bool:
            return str(image) in scanned and self._showable(image, removed)

        if playlist.order is Order.SORTED:
            image = next_sorted(images, self._sorted_after(playlist), drawable)
            return _Candidate(image) if image else None
        latest = self._store.latest_history(playlist.id)
        avoid = latest.path if latest else None
        card = self._deck(playlist).peek(images, drawable, avoid=avoid)
        return _Candidate(card.path, card=card) if card else None

    def _sorted_after(self, playlist: PlaylistSettings) -> Path | None:
        """
        Where the sorted walk goes on: after the newest history entry from
        the playlist's directory (a pick from elsewhere is a detour; one from
        the directory moves the walk there).
        """
        for entry in self._store.history_before(playlist.id, limit=100):
            if self._in_directory(playlist, entry.path):
                return entry.path
        return None

    @staticmethod
    def _in_directory(playlist: PlaylistSettings, image: Path) -> bool:
        directory = path_key(playlist.directory)
        return path_key(image).startswith(directory.rstrip(os.sep) + os.sep)

    def _as_scanned(self, playlist: PlaylistSettings, image: Path) -> Path:
        """
        `image` spelled as the playlist's scan has it, if it is one of its
        images (so the deck and the history know it); else as given.
        """
        if not self._in_directory(playlist, image):
            return image
        key = path_key(image)
        for refresh in (False, True):  # it may be newer than the cached scan
            images = self._library.images(playlist.directory, refresh=refresh)
            found = next((p for p in images if path_key(p) == key), None)
            if found is not None:
                return found
        return image

    def _peek_previous(self, playlist: PlaylistSettings) -> _Candidate | None:
        removed = self._removed()
        cursor = self._store.playlist_state(playlist.id).cursor
        if cursor is None:
            return None
        for entry in self._store.history_before(playlist.id, cursor):
            if self._showable(entry.path, removed):
                return _Candidate(entry.path, history_id=entry.id)
        return None

    def _upcoming(
        self, playlist: PlaylistSettings, removed: set[str], limit: int
    ) -> list[Path]:
        def showable(image: Path) -> bool:
            return self._showable(image, removed)

        result: list[Path] = []
        cursor = self._store.playlist_state(playlist.id).cursor
        if cursor is not None:
            result = [
                e.path
                for e in self._store.history_after(playlist.id, cursor, limit)
                if showable(e.path)
            ]
        if len(result) >= limit:
            return result[:limit]
        images = self._library.images(playlist.directory)
        if playlist.order is Order.SORTED:
            after = self._sorted_after(playlist)
            more = upcoming_sorted(images, after, showable, limit)
        else:
            more = [
                c.path for c in self._deck(playlist).upcoming(images, showable, limit)
            ]
        return (result + more)[:limit]

    def _deck(self, playlist: PlaylistSettings) -> ShuffleDeck:
        return ShuffleDeck(self._store, playlist.id, self._rng)

    # -- showing images ------------------------------------------------------

    def _members(
        self, monitors: Sequence[Monitor]
    ) -> dict[str, tuple[MemberStatus, ...]]:
        """Every playlist's monitors (by playlist id), as listed now."""
        return {
            playlist.id: _members_of(self.settings.members(playlist.id), monitors)
            for playlist in self.settings.playlists
        }

    def _attached_now(self, playlist: PlaylistSettings) -> list[Monitor]:
        member_ids = self.settings.members(playlist.id)
        attached = _attached(_members_of(member_ids, self._backend.monitors()))
        if not attached:
            if not member_ids:
                raise MonitorNotAttachedError(f"{playlist.name} is on no monitor")
            ids = ", ".join(str(m) for m in member_ids)
            raise MonitorNotAttachedError(
                f"None of {playlist.name}'s monitors is attached ({ids})"
            )
        return attached

    def _step(
        self,
        playlist: PlaylistSettings,
        members: Sequence[Monitor],
        peek: Callable[[], _Candidate | None],
        reason: ChangeReason,
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
        self, playlist: PlaylistSettings, image: Path, members: Sequence[Monitor]
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
                    self._backend,
                    monitor.id,
                    image,
                    self.settings.look(monitor.id),
                    self._render_dir,
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
        self,
        playlist: PlaylistSettings,
        candidate: _Candidate,
        shown: Sequence[Monitor],
    ) -> None:
        now = self._clock()
        with self._store.transaction():
            history_id = candidate.history_id
            if history_id is None:  # new images come after all of the history
                history_id = self._store.append_history(
                    playlist.id, candidate.image, now
                )
                if candidate.card is not None:
                    self._deck(playlist).draw(candidate.card)
            self._store.set_cursor(playlist.id, history_id)
            for monitor in shown:
                self._store.set_applied(monitor.id, playlist.id, candidate.image, now)

    def _fill_in(
        self, playlist: PlaylistSettings, attached: Sequence[Monitor], current: Path
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
                self._store.set_applied(monitor.id, playlist.id, current, now)
        return Change(
            playlist.name,
            current,
            "restored",
            tuple(m.name for m in shown),
            tuple(m.name for m in missed),
        )


def _count_in(entries: Sequence[Path], keys: set[str]) -> int:
    """How many of the list's `entries` name one of these path keys."""
    return sum(path_key(p) in keys for p in entries)


def _list_stats(marks: MarkFile) -> ListStats:
    entries = marks.paths()
    return ListStats(
        file=marks.file,
        listed=len(entries),
        available=sum(p.is_file() for p in entries),
    )


def _playlist_of(settings: Settings, monitor_id: MonitorId) -> str | None:
    return next((m.playlist for m in settings.monitors if m.id == monitor_id), None)


def _open_lists(settings: Settings, data_dir: Path) -> tuple[MarkFile, MarkFile]:
    """
    The favourites and to-remove lists, read once.

    Raises:
        ConfigError: one can't be used (e.g. saved with a byte order mark).
    """
    lists = tuple(MarkFile(file) for file in settings.list_files(data_dir))
    for marks in lists:
        try:
            marks.paths()  # ConfigError now, not on the first tick
        except MarkFileBusyError:
            pass  # logged; unknown until it opens (see _removed)
    favourites, to_remove = lists
    return favourites, to_remove
