"""
The Library page: what a playlist picks up, as images and in numbers
(docs/plan.md at 268a951, Phase 5).

The title, a playlist drop-down and the open tab's count ("· 20,431 images
· 1,204 seen"; "312 of 20,431" while filtered by name; the seen count, the
daemon's, on every tab), then the tabs: All images, Favourites, To remove,
History, Upcoming (each a `GridTab`, library_grids.py) and Stats
(`StatsTab`, library_stats.py). The drop-down picks the playlist for every
tab but Favourites and To remove, which show the whole list (with *Only
<playlist>*: the entries in its folder). The playlist, the tab, each grid
tab's sort and chips are remembered in `ui-state.json` (ui_state.py),
never in the settings.

What each tab shows, read while it is shown (and again when it may have
changed):

- All images: the playlist's folder, read here off the main thread; *Hide
  removed* leaves out what to-remove.txt lists (History has it too).
- Favourites, To remove: the lists, read here off the main thread whenever
  the page is shown and on `marks_changed`; every grid's badges come from
  them, and *On screen* from the overview's current images.
- History: read here from the state database (read-only), again on its
  playlist's `changed` and on `overview_changed` (a save may delete it).
- Upcoming and Stats: asked from the daemon (`upcoming`, `stats`), one
  request at a time, again on `overview_changed`, `marks_changed` and a
  `changed` of the playlist. Stats is asked on every tab (the header's
  seen count). *Rescan* asks with `rescan`; *Reset cycle…* asks first, then
  sends `reset_cycle`.

A click on a tile opens it in the `EnlargedView` (enlarged.py), over the
whole page, with the grid's images in their order to go through; the
page keeps its marks and playlists up to date, and back on the grid
scrolls to the image last shown.

The page owns the grids' thumbnails (tiles.py): the `TileLoader` and the
`Pregenerator`, which fills the disk cache while the window is open (the
chosen playlist's folder first, then the others and the lists, as the
overview names them); `shutdown()` stops their threads and the readers'
(the enlarged view's too).
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import override

from PySide6.QtCore import Slot
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core import paths
from ...core.library import path_key
from ...core.service import Change
from ...core.settings import PlaylistId
from ...core.thumbnail_cache import ThumbnailCache
from ...protocol import (
    ErrorReply,
    Overview,
    Reply,
    ResetCycleRequest,
    StatsRequest,
    StatsResult,
    UpcomingRequest,
    UpcomingResult,
)
from ..client import Client, OneAtATime, ReplyTo
from . import theme
from .enlarged import EnlargedView
from .entries import (
    Read,
    Sort,
    in_folder,
    lists_failed,
    read_failed,
    read_folder,
    read_history,
    read_lists,
    upcoming_entries,
)
from .grid import Marks
from .jobs import LatestRead
from .library_grids import ADDED_SORTS, SHOWN_SORTS, GridTab
from .library_stats import StatsTab, count
from .settings_form import combo
from .thumbnails import sharpest_ratio
from .tiles import TILE, Pregenerator, Sources, TileLoader, tile_pixels
from .ui_state import GridName, LibraryTab, load_ui_state, save_ui_state

TABS: tuple[tuple[LibraryTab, str], ...] = (
    ("all", "All images"),
    ("favourites", "Favourites"),
    ("to_remove", "To remove"),
    ("history", "History"),
    ("upcoming", "Upcoming"),
    ("stats", "Stats"),
)
GRIDS: tuple[GridName, ...] = ("all", "favourites", "to_remove", "history", "upcoming")
PLAYLIST_GRIDS: tuple[GridName, ...] = ("all", "history", "upcoming")  # one playlist's
UPCOMING = 500  # images Upcoming shows (it asks one more: "500+")
NO_PLAYLISTS = "No playlists yet: add one in Settings."
COMBO_WIDTH = theme.space(28)

# What a grid tab has: sorts and the default, its chip's default (None:
# no chip), the line when it has nothing.
_GRID_SPECS: dict[GridName, tuple[dict[Sort, str], Sort | None, bool | None, str]] = {
    "all": (dict(ADDED_SORTS), "name_az", True, "No images in the playlist's folder."),
    "favourites": (dict(ADDED_SORTS), "newest", False, "No favourites."),
    "to_remove": (dict(ADDED_SORTS), "newest", False, "Nothing is listed to remove."),
    "history": (dict(SHOWN_SORTS), "newest", True, "Nothing shown yet."),
    "upcoming": ({}, None, None, "Nothing comes next."),
}

# Asks before a reset: (parent, question) -> True to go ahead.
Confirm = Callable[[QWidget, str], bool]


def confirm_reset(parent: QWidget, question: str) -> bool:
    buttons = QMessageBox.StandardButton
    box = QMessageBox(
        QMessageBox.Icon.Question,
        "Reset cycle",
        question,
        buttons.Cancel,
        parent,
    )
    reset = box.addButton("Reset cycle", QMessageBox.ButtonRole.AcceptRole)
    box.setDefaultButton(buttons.Cancel)
    box.exec()
    return box.clickedButton() is reset


class LibraryPage(QWidget):
    """See the module docstring."""

    # pylint: disable=too-many-instance-attributes,too-many-public-methods

    def __init__(  # pylint: disable=too-many-arguments
        self,
        client: Client,
        *,
        confirm: Confirm = confirm_reset,
        state_file: Path | None = None,
        database: Path | None = None,
        thumbnails: ThumbnailCache | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._confirm = confirm
        self._state_file = state_file if state_file is not None else paths.ui_state()
        self._database = database if database is not None else paths.state_db()
        self.state = load_ui_state(self._state_file)
        self._overview: Overview | None = None
        self.stats: StatsResult | None = None  # of the playlist chosen
        self._stats_asks = OneAtATime()
        self._upcoming_asks = OneAtATime()
        self._rescan = False  # the next stats request scans again
        self._resetting = False
        # What the grid tabs show, as read (None: not yet, or not any more).
        self._folder: tuple[Path, Read] | None = None  # All images: the folder's
        self._lists: tuple[Read, Read] | None = None  # favourites, to-remove
        self._history: tuple[PlaylistId, Read] | None = None
        self._upcoming: tuple[PlaylistId, Read] | None = None

        self.playlist_combo = combo(self, "Playlist")
        self.playlist_combo.setFixedWidth(COMBO_WIDTH)
        self.playlist_combo.setFont(
            theme.scaled_font(self.playlist_combo.font(), theme.CONTROL_TEXT)
        )
        self.count = theme.muted("", self)
        self.banner = theme.Banner(self)
        self.tabs = theme.Tabs(self)
        self.stack = QStackedWidget(self)
        self.stats_tab = StatsTab(self)
        if thumbnails is None:
            thumbnails = ThumbnailCache(
                paths.thumbnail_dir(), tile_pixels(sharpest_ratio())
            )
        self.tiles = TileLoader(
            thumbnails.thumbnail,
            ratio=thumbnails.size[0] / TILE.width(),
            parent=self,
        )
        self.pregenerator = Pregenerator(thumbnails, self.tiles.idle, parent=self)
        self.folder_read = LatestRead(self)
        self.lists_read = LatestRead(self)
        self.history_read = LatestRead(self)
        self.grids = {name: self._grid_tab(name) for name in GRIDS}
        for name in ("all", "history"):
            self.grids[name].set_switch_text("Hide removed")
        self.browse = QWidget(self)  # the header and the tabs
        self.enlarged = EnlargedView(client, self)
        self.layers = QStackedWidget(self)  # the browse, or the enlarged view
        self._viewing: GridTab | None = None  # the grid tab it was opened from
        self._build()

        index = next(
            i for i, (tab, _) in enumerate(TABS) if tab == self.state.library_tab
        )
        self.tabs.set_current_index(index)
        self.stack.setCurrentIndex(index)
        self._connect()
        if client.overview is not None:
            self.show_overview(client.overview)
        else:
            self.stats_tab.show_message("Connecting to wallpaper_py…")

    def _connect(self) -> None:
        self.tabs.current_changed.connect(self._tab_chosen)
        self.playlist_combo.currentIndexChanged.connect(self._playlist_chosen)
        self.stats_tab.rescan_requested.connect(self._rescan_clicked)
        self.stats_tab.reset_requested.connect(self._reset_clicked)
        for grid in self.grids.values():
            grid.sort_changed.connect(self._sort_changed)
            grid.switched.connect(self._switched)
            grid.badges_toggled.connect(self._badges_toggled)
            grid.counted.connect(self._show_count)
            grid.open_requested.connect(self._open_image)
        self.enlarged.closed.connect(self._close_image)
        self.folder_read.done.connect(self._folder_read)
        self.lists_read.done.connect(self._lists_read)
        self.history_read.done.connect(self._history_read)
        client = self._client
        client.overview_changed.connect(self.show_overview)
        client.changed.connect(self._changed)
        client.marks_changed.connect(self._marks_changed)
        client.failed.connect(self.show_error)

    def _grid_tab(self, name: GridName) -> GridTab:
        """A grid tab, with its sort and chips as remembered."""
        sorts, sort, switch, empty = _GRID_SPECS[name]
        if switch is not None:
            switch = self.state.library_switches.get(name, switch)
        return GridTab(
            name,
            self.tiles,
            sorts=sorts,
            sort=self.state.library_sorts.get(name, sort),
            switch=switch,
            badges=self.state.library_badges.get(name, True),
            empty=empty,
            parent=self,
        )

    def _build(self) -> None:
        for _, label in TABS:
            self.tabs.add(label)
        for name in GRIDS:
            self.stack.addWidget(self.grids[name])
        self.stack.addWidget(theme.scrolling(self.stats_tab, self))

        title = QHBoxLayout()
        title.setSpacing(theme.space(2))
        title.addWidget(theme.title("Library", self))
        title.addWidget(self.playlist_combo)
        title.addWidget(self.count, 1)
        header = QWidget(self.browse)
        header_layout = theme.page_layout(header)
        header_layout.setSpacing(theme.space(1.5))
        header_layout.addLayout(title)
        header_layout.addWidget(self.banner)
        header_layout.addWidget(self.tabs)

        browse = QVBoxLayout(self.browse)
        browse.setContentsMargins(0, 0, 0, 0)
        browse.setSpacing(0)
        browse.addWidget(header)
        browse.addWidget(self.stack, 1)
        self.layers.addWidget(self.browse)
        self.layers.addWidget(self.enlarged)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.layers)

    # -- what is shown -------------------------------------------------------------

    def playlist(self) -> PlaylistId | None:
        """The id of the playlist chosen (None: there is none)."""
        data = self.playlist_combo.currentData()
        return data if isinstance(data, str) else None

    def playlist_name(self, playlist_id: PlaylistId) -> str:
        """A playlist's name as the overview has it (the id if it is gone)."""
        playlists = self._overview.playlists if self._overview else ()
        return next((p.name for p in playlists if p.id == playlist_id), playlist_id)

    def tab(self) -> LibraryTab:
        return TABS[self.tabs.current_index()][0]

    def _shown(self, tab: LibraryTab) -> bool:
        return self.isVisible() and self.tab() == tab

    def _remember(self, **changes: object) -> None:
        self.state = self.state.model_copy(update=changes)
        save_ui_state(self._state_file, self.state)

    def directory(self) -> Path | None:
        """The chosen playlist's folder, as the overview has it."""
        chosen = self.playlist()
        overview = self._overview
        if chosen is None or overview is None:
            return None
        return next((p.directory for p in overview.playlists if p.id == chosen), None)

    @Slot(int)
    def _tab_chosen(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self._remember(library_tab=self.tab())
        self._show_count()
        self._read_shown(stats=False)

    @Slot(int)
    def _playlist_chosen(self, index: int) -> None:
        del index
        self._remember(library_playlist=self.playlist())
        self._playlist_changed()
        self._read_shown(stats=True)
        self._pregenerate()

    def _playlist_changed(self) -> None:
        """Another playlist: nothing of the last one stays."""
        self._show_stats(None)
        self._folder = self._history = self._upcoming = None
        for name in GRIDS:
            self._fill(name, top=True)
        self._name_switches()

    def _fill_playlists(self, playlists: list[tuple[PlaylistId, str]]) -> None:
        """
        The drop-down's playlists (id, name), in the settings' order; the one
        chosen stays if it still exists, else the remembered one, else the
        first. A rename keeps the choice.
        """
        box = self.playlist_combo
        before = self.playlist()
        listed = [(box.itemData(i), box.itemText(i)) for i in range(box.count())]
        ids = [i for i, _ in playlists]
        chosen = next(
            (i for i in (before, self.state.library_playlist) if i in ids),
            ids[0] if ids else None,
        )
        box.blockSignals(True)
        if listed != playlists:
            box.clear()
            for playlist_id, name in playlists:
                box.addItem(name, playlist_id)
        box.setCurrentIndex(ids.index(chosen) if chosen is not None else -1)
        box.blockSignals(False)
        box.setEnabled(bool(ids))
        if self.playlist() != before:
            self._playlist_changed()

    def _name_switches(self) -> None:
        playlist = self.playlist()
        text = (
            f"Only {self.playlist_name(playlist)}" if playlist else "Only this playlist"
        )
        for name in ("favourites", "to_remove"):
            self.grids[name].set_switch_text(text)

    @override
    def showEvent(self, event: QShowEvent) -> None:  # pylint: disable=invalid-name
        super().showEvent(event)
        self._read_lists()
        self._read_shown(stats=True)

    def shutdown(self) -> None:
        """The window closes: stop the threads (waits for them)."""
        self.enlarged.shutdown()
        self.pregenerator.close()
        self.tiles.close()
        for reader in (self.folder_read, self.lists_read, self.history_read):
            reader.close()

    # -- from the daemon ------------------------------------------------------------

    @Slot(object)
    def show_overview(self, overview: Overview) -> None:
        before = self._overview
        self._overview = overview
        # Saved settings, not only a new image: History and Upcoming may differ.
        settled = before is not None and _playlists(before) == _playlists(overview)
        error = f"Error: {overview.error}" if overview.error else None
        self.banner.set_message(error)
        # The enlarged view's too, but cleared there only if it says the last
        # error: else it says what was just done there.
        was = f"Error: {before.error}" if before is not None and before.error else None
        if error or (was is not None and self.enlarged.banner.text() == was):
            self.enlarged.banner.set_message(error)
        playlists = [(p.id, p.name) for p in overview.playlists]
        self._fill_playlists(playlists)
        self.enlarged.set_playlists(playlists)
        self._name_switches()
        if not overview.playlists:
            self.stats_tab.show_message(None if overview.error else NO_PLAYLISTS)
            for name in PLAYLIST_GRIDS:
                self._fill(name)
        self._show_marks()
        if self._lists_files(before) != self._lists_files(overview):
            self._lists = None
            self._read_lists()
        folder = self.directory()
        if self._folder is not None and self._folder[0] != folder:
            self._folder = None  # its folder changed
            self._fill("all", top=True)
        for name in ("favourites", "to_remove"):
            self._fill(name)  # Only <playlist>: its folder may have changed
        self._ask_stats()
        if not settled:
            self._ask_upcoming()
            if self._shown("history"):
                self._read_history()  # a save may have deleted it
        if self._folder is None and self.folder_read.asked() != folder:
            self._read_folder()
        self._pregenerate()

    @Slot(object)
    def _changed(self, change: Change) -> None:
        if change.playlist_id == self.playlist():
            self._ask_stats()
            self._ask_upcoming()
            if self._shown("history"):
                self._read_history()

    @Slot()
    def _marks_changed(self) -> None:
        self._ask_stats()
        self._ask_upcoming()
        self._read_lists()

    @Slot(str)
    def show_error(self, message: str) -> None:
        self.banner.set_message(f"Error: {message}")
        self.enlarged.banner.set_message(f"Error: {message}")  # if it is open

    # -- the grid tabs -------------------------------------------------------------

    def _read_shown(self, *, stats: bool) -> None:
        """
        Read (or ask for) what the tab shown now shows; the stats too if
        `stats` (they are on every tab: the header's seen count).
        """
        tab = self.tab()
        if not self.isVisible() or self.playlist() is None:
            return
        if tab == "all":
            self._read_folder()
        elif tab == "history":
            self._read_history()
        elif tab == "upcoming":
            self._ask_upcoming()
        if stats or tab == "stats":
            self._ask_stats()

    def _read_folder(self) -> None:
        folder = self.directory()
        if folder is not None and self._shown("all"):
            self.folder_read.read(folder, partial(read_folder, folder), read_failed)

    @Slot(object, object)
    def _folder_read(self, folder: Path, read: Read) -> None:
        if folder == self.directory():
            self._folder = (folder, read)
            self._fill("all")

    def _read_history(self) -> None:
        playlist = self.playlist()
        if playlist is not None:
            work = partial(read_history, self._database, playlist)
            self.history_read.read(playlist, work, read_failed)

    @Slot(object, object)
    def _history_read(self, playlist: PlaylistId, read: Read) -> None:
        if playlist == self.playlist():
            self._history = (playlist, read)
            self._fill("history")

    @staticmethod
    def _lists_files(overview: Overview | None) -> tuple[Path, Path] | None:
        if overview is None:
            return None
        favourites, to_remove = overview.favourites_file, overview.to_remove_file
        if favourites is None or to_remove is None:
            return None  # the daemon has an error: no lists known
        return favourites, to_remove

    def _read_lists(self) -> None:
        files = self._lists_files(self._overview)
        if files is not None and self.isVisible():
            self.lists_read.read(files, partial(read_lists, *files), lists_failed)

    @Slot(object, object)
    def _lists_read(self, files: tuple[Path, Path], lists: tuple[Read, Read]) -> None:
        if files != self._lists_files(self._overview):
            return
        self._lists = lists
        self._show_marks()
        for name in ("all", "favourites", "to_remove", "history"):
            self._fill(name)

    def _ask_upcoming(self) -> None:
        """One request at a time; asked again if more came meanwhile."""
        playlist = self.playlist()
        overview = self._overview
        if (
            not self._shown("upcoming")
            or playlist is None
            or overview is None
            or overview.error
            or not self._upcoming_asks.ask()
        ):
            return
        request = UpcomingRequest(target=playlist, limit=UPCOMING + 1)
        self._client.run(request, ReplyTo(self, LibraryPage.upcoming_replied, playlist))

    def upcoming_replied(self, asked: PlaylistId, reply: Reply) -> None:
        """What `asked` (its id) shows next."""
        if asked == self.playlist():
            if isinstance(reply, UpcomingResult):
                self._upcoming = (asked, upcoming_entries(reply.images, UPCOMING))
            elif isinstance(reply, ErrorReply):
                why = f"Can't list what comes next: {reply.error.message}"
                self._upcoming = (asked, Read(error=why))
            self._fill("upcoming")
        if self._upcoming_asks.done():
            self._ask_upcoming()

    def _source(self, name: GridName) -> Read | None:
        """A grid tab's entries as read (None: not yet)."""
        if name == "all":
            return self._folder[1] if self._folder else None
        if name in ("favourites", "to_remove"):
            if self._lists is None:
                return None
            return self._lists[0 if name == "favourites" else 1]
        if name == "history":
            return self._history[1] if self._history else None
        return self._upcoming[1] if self._upcoming else None

    def _fill(self, name: GridName, *, top: bool = False) -> None:
        """A grid tab's entries, narrowed by its switch."""
        grid = self.grids[name]
        overview = self._overview
        if name in PLAYLIST_GRIDS and overview and not overview.playlists:
            grid.show_message("" if overview.error else NO_PLAYLISTS)
            return
        read = self._source(name)
        if read is None:
            grid.set_entries(None, top=top)
            return
        if read.error is not None:
            grid.show_message(read.error)
            return
        entries = read.entries
        if name in ("all", "history") and grid.switched_on():
            if self._lists is None:  # not known yet: nothing rather than a flash
                grid.set_entries(None, top=top)
                return
            to_remove = self._lists[1]
            if to_remove.error is not None:  # else it would quietly hide nothing
                grid.show_message(f"Can't hide the removed images: {to_remove.error}")
                return
            removed = to_remove.keys()
            entries = tuple(e for e in entries if e.key not in removed)
        elif name in ("favourites", "to_remove") and grid.switched_on():
            folder = self.directory()
            entries = tuple(in_folder(entries, folder)) if folder else ()
        grid.set_entries(entries, top=top)

    def _show_marks(self) -> None:
        """The badges: the lists' entries, and the images on screen now."""
        playlists = self._overview.playlists if self._overview else ()
        on_screen = frozenset(path_key(p.current) for p in playlists if p.current)
        lists = self._lists
        marks = Marks(
            favourites=lists[0].keys() if lists else frozenset(),
            to_remove=lists[1].keys() if lists else frozenset(),
            on_screen=on_screen,
        )
        for grid in self.grids.values():
            grid.grid.set_marks(marks)
        self.enlarged.set_marks(marks if lists is not None else None)

    @Slot(object)
    def _sort_changed(self, grid: GridTab) -> None:
        sorts = {**self.state.library_sorts}
        sort = grid.sort()
        if sort is not None:
            sorts[grid.name] = sort
        self._remember(library_sorts=sorts)

    @Slot(object)
    def _switched(self, grid: GridTab) -> None:
        switches = {**self.state.library_switches, grid.name: grid.switched_on()}
        self._remember(library_switches=switches)
        self._fill(grid.name, top=True)

    @Slot(object)
    def _badges_toggled(self, grid: GridTab) -> None:
        badges = {**self.state.library_badges, grid.name: grid.badges_shown()}
        self._remember(library_badges=badges)

    @Slot(object, int)
    def _open_image(self, tab: GridTab, row: int) -> None:
        """A tile was clicked: it enlarged, with the grid's images to go through."""
        self._viewing = tab
        self.layers.setCurrentWidget(self.enlarged)  # first: its size, the focus
        self.enlarged.open_entries(tab.grid.list_model.entries(), row, self.playlist())

    @Slot()
    def _close_image(self) -> None:
        """Back to the grid, scrolled to the image last shown."""
        self.layers.setCurrentWidget(self.browse)
        tab, entry = self._viewing, self.enlarged.current()
        self._viewing = None
        if tab is not None and entry is not None:
            tab.grid.reveal(self.enlarged.row(), entry.path)
            tab.grid.setFocus()

    def enlarged_shown(self) -> bool:
        return self.layers.currentWidget() is self.enlarged

    def _pregenerate(self) -> None:
        """
        Thumbnails for every playlist's folder, the chosen one first, then
        for the lists. Not while the daemon has an error: its overview names
        no playlists then, and a pass over nothing would prune everything.
        """
        overview = self._overview
        if overview is None or overview.error or not overview.playlists:
            return
        chosen = self.playlist()
        playlists = sorted(overview.playlists, key=lambda p: p.id != chosen)
        folders = tuple(dict.fromkeys(p.directory for p in playlists))
        lists = tuple(
            f
            for f in (overview.favourites_file, overview.to_remove_file)
            if f is not None
        )
        self.pregenerator.start(Sources(folders, lists))

    # -- stats -------------------------------------------------------------------

    def _wants_stats(self) -> bool:
        overview = self._overview
        return (
            self.isVisible()
            and self.playlist() is not None
            and overview is not None
            and not overview.error
        )

    def _ask_stats(self) -> None:
        """One request at a time; asked again if more came meanwhile."""
        playlist = self.playlist()
        if not self._wants_stats() or playlist is None or not self._stats_asks.ask():
            return
        rescan, self._rescan = self._rescan, False
        request = StatsRequest(target=playlist, rescan=rescan)
        self._client.run(request, ReplyTo(self, LibraryPage.stats_replied, playlist))

    def stats_replied(self, asked: PlaylistId, reply: Reply) -> None:
        """The stats of `asked` (its id)."""
        if asked == self.playlist():  # else another was chosen meanwhile
            if isinstance(reply, StatsResult) and reply.playlists:
                self._show_stats(reply)
            elif isinstance(reply, ErrorReply):
                self._show_stats(None)
                self.stats_tab.show_message(
                    f"Can't count {self.playlist_name(asked)}'s images: "
                    f"{reply.error.message}"
                )
        if self._stats_asks.done():
            self._ask_stats()

    def _show_stats(self, stats: StatsResult | None) -> None:
        self.stats = stats
        if stats is None:
            self.stats_tab.clear()
        else:
            self.stats_tab.show_stats(
                stats.playlists[0], stats.favourites, stats.to_remove
            )
        self._show_count()

    @Slot()
    def _show_count(self) -> None:
        """The open tab's count, and the cycle's seen count."""
        parts: list[str] = []
        tab = self.tab()
        if tab == "stats":
            if self.stats is not None:
                parts.append(images_text(self.stats.playlists[0].images))
        else:
            counted = self.grids[tab].count()
            if counted is not None:
                shown, of = counted
                source = self._source(tab)
                more = source is not None and source.more
                if self.grids[tab].filtered():
                    parts.append(f"{count(shown)} of {count(of)}{'+' if more else ''}")
                else:
                    parts.append(images_text(of, more=more))
        if self.stats is not None:
            parts.append(f"{count(self.stats.playlists[0].seen)} seen")
        self.count.setText("".join(f"· {p} " for p in parts).strip())

    @Slot()
    def _rescan_clicked(self) -> None:
        self.stats_tab.show_notice(None)
        self._rescan = True
        self._ask_stats()

    @Slot()
    def _reset_clicked(self) -> None:
        playlist = self.playlist()
        if self.stats is None or playlist is None or self._resetting:
            return
        stats = self.stats.playlists[0]
        question = (
            f"Start {stats.name}'s shuffle cycle over? All "
            f"{count(stats.seen + stats.unseen)} images become unseen."
        )
        self.stats_tab.show_notice(None)
        if not self._confirm(self, question):
            return
        self._resetting = True
        self.stats_tab.set_resetting(True)
        request = ResetCycleRequest(target=playlist)
        self._client.run(request, ReplyTo(self, LibraryPage.reset_replied))

    def reset_replied(self, reply: Reply) -> None:
        self._resetting = False
        self.stats_tab.set_resetting(False)
        if isinstance(reply, ErrorReply):
            self.stats_tab.show_notice(f"Can't reset the cycle: {reply.error.message}")
        self._ask_stats()


def _playlists(overview: Overview) -> list[tuple[PlaylistId, str, Path]]:
    """What a settings save changes of the playlists (not their images)."""
    return [(p.id, p.name, p.directory) for p in overview.playlists]


def images_text(number: int, *, more: bool = False) -> str:
    """ "1 image", "12 images"; `more`: "500+ images" (there are more)."""
    if more:
        return f"{count(number)}+ images"
    return f"{count(number)} image{'' if number == 1 else 's'}"
