"""
The Library page: what a playlist picks up, in numbers (the Overview tab)
and as images (All images; the other grid tabs come in later steps)
(docs/plan.md, Phase 5; docs/design/library.png).

The title, a playlist drop-down and the open tab's count, then the tabs.
The drop-down picks the playlist for Overview and All images (and later
History and Upcoming); the playlist and the tab are remembered in
`ui-state.json` (ui_state.py), never in the settings.

All images is the playlist's folder in an `ImageGrid` (grid.py), listed
off the main thread (`FolderScan`) when the tab is shown and when the
playlist or its folder changes. The page owns the grids' thumbnails
(tiles.py): the `TileLoader` and the `Pregenerator`, which fills the disk
cache while the window is open (the chosen playlist's folder first, then
the others and the lists, as the overview names them); `shutdown()`
stops their threads.

Overview asks the daemon (`stats`) when it is shown, and again on
`overview_changed`, `marks_changed` and a `changed` of its playlist
while it is: one
request at a time, asked again if more came meanwhile. *Rescan* asks with
`rescan`; *Reset cycle…* asks first, then sends `reset_cycle`. Everything
comes through `Client`; nothing is read here yet.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import override

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core import paths
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
)
from ..client import Client, OneAtATime, ReplyTo
from . import theme
from .grid import FolderScan, ImageGrid
from .library_overview import OverviewTab, count
from .settings_form import combo
from .thumbnails import sharpest_ratio
from .tiles import TILE, Pregenerator, Sources, TileLoader, tile_pixels
from .ui_state import LibraryTab, load_ui_state, save_ui_state

TABS: tuple[tuple[LibraryTab, str], ...] = (
    ("overview", "Overview"),
    ("all", "All images"),
    ("favourites", "Favourites"),
    ("to_remove", "To remove"),
    ("history", "History"),
    ("upcoming", "Upcoming"),
)
COMBO_WIDTH = theme.space(28)

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

    # pylint: disable=too-many-instance-attributes

    def __init__(
        self,
        client: Client,
        *,
        confirm: Confirm = confirm_reset,
        state_file: Path | None = None,
        thumbnails: ThumbnailCache | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._confirm = confirm
        self._state_file = state_file if state_file is not None else paths.ui_state()
        self.state = load_ui_state(self._state_file)
        self._overview: Overview | None = None
        self.stats: StatsResult | None = None  # of the playlist chosen
        self._stats_asks = OneAtATime()
        self._rescan = False  # the next stats request scans again
        self._resetting = False
        self._grid_folder: Path | None = None  # All images shows its images

        self.playlist_combo = combo(self, "Playlist")
        self.playlist_combo.setFixedWidth(COMBO_WIDTH)
        self.count = theme.muted("", self)
        self.banner = theme.Banner(self)
        self.tabs = theme.Tabs(self)
        self.stack = QStackedWidget(self)
        self.overview_tab = OverviewTab(self)
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
        self.folder_scan = FolderScan(self)
        self.all_grid = ImageGrid(self.tiles, self)
        self._build()

        index = next(
            i for i, (tab, _) in enumerate(TABS) if tab == self.state.library_tab
        )
        self.tabs.set_current_index(index)
        self.stack.setCurrentIndex(index)
        self.tabs.current_changed.connect(self._tab_chosen)
        self.playlist_combo.currentIndexChanged.connect(self._playlist_chosen)
        self.overview_tab.rescan_requested.connect(self._rescan_clicked)
        self.overview_tab.reset_requested.connect(self._reset_clicked)
        self.folder_scan.scanned.connect(self._folder_scanned)
        client.overview_changed.connect(self.show_overview)
        client.changed.connect(self._changed)
        client.marks_changed.connect(self._marks_changed)
        client.failed.connect(self.show_error)
        if client.overview is not None:
            self.show_overview(client.overview)
        else:
            self.overview_tab.show_message("Connecting to wallpaper_py…")

    def _build(self) -> None:
        for _, label in TABS:
            self.tabs.add(label)
        self.stack.addWidget(theme.scrolling(self.overview_tab, self))
        grid_page = QWidget(self)
        grid_layout = QVBoxLayout(grid_page)
        grid_layout.setContentsMargins(
            theme.PAGE_MARGIN, theme.space(2), theme.PAGE_MARGIN, 0
        )
        grid_layout.addWidget(self.all_grid)
        self.stack.addWidget(grid_page)
        for _ in TABS[2:]:
            later = theme.muted("This tab comes in a later version.", self)
            later.setAlignment(Qt.AlignmentFlag.AlignTop)
            later.setContentsMargins(theme.PAGE_MARGIN, theme.space(2), 0, 0)
            self.stack.addWidget(later)

        title = QHBoxLayout()
        title.setSpacing(theme.space(2))
        title.addWidget(theme.title("Library", self))
        title.addWidget(self.playlist_combo)
        title.addWidget(self.count, 1)
        header = QWidget(self)
        header_layout = theme.page_layout(header)
        header_layout.setSpacing(theme.space(1.5))
        header_layout.addLayout(title)
        header_layout.addWidget(self.banner)
        header_layout.addWidget(self.tabs)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(header)
        outer.addWidget(self.stack, 1)

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
        self._ask_stats()
        self._list_folder()

    @Slot(int)
    def _playlist_chosen(self, index: int) -> None:
        del index
        self._remember(library_playlist=self.playlist())
        self._show_stats(None)
        self._ask_stats()
        self._list_folder()
        self._pregenerate()

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
            self._show_stats(None)

    @override
    def showEvent(self, event: QShowEvent) -> None:  # pylint: disable=invalid-name
        super().showEvent(event)
        self._ask_stats()
        self._list_folder()

    def shutdown(self) -> None:
        """The window closes: stop the thumbnails' threads (waits for them)."""
        self.pregenerator.close()
        self.tiles.close()
        self.folder_scan.close()

    # -- from the daemon ------------------------------------------------------------

    @Slot(object)
    def show_overview(self, overview: Overview) -> None:
        self._overview = overview
        self.banner.set_message(f"Error: {overview.error}" if overview.error else None)
        self._fill_playlists([(p.id, p.name) for p in overview.playlists])
        if not overview.playlists:
            self.overview_tab.show_message(
                None if overview.error else "No playlists yet: add one in Settings."
            )
        self._ask_stats()
        if self.directory() not in (self._grid_folder, self.folder_scan.asked()):
            self._list_folder()  # its folder changed
        self._pregenerate()

    @Slot(object)
    def _changed(self, change: Change) -> None:
        if change.playlist_id == self.playlist():
            self._ask_stats()

    @Slot()
    def _marks_changed(self) -> None:
        self._ask_stats()

    @Slot(str)
    def show_error(self, message: str) -> None:
        self.banner.set_message(f"Error: {message}")

    # -- images ------------------------------------------------------------------

    def _list_folder(self) -> None:
        """All images, while shown: the chosen playlist's folder, listed again."""
        folder = self.directory()
        if folder != self._grid_folder:
            self._grid_folder = None
            self.all_grid.set_images(())
        if folder is not None and self.isVisible() and self.tab() == "all":
            self.folder_scan.scan(folder)

    @Slot(object, object)
    def _folder_scanned(self, folder: Path, images: list[Path]) -> None:
        if folder == self.directory():
            self._grid_folder = folder
            self.all_grid.set_images(images)

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
            and self.tab() == "overview"
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
                self.overview_tab.show_message(
                    f"Can't count {self.playlist_name(asked)}'s images: "
                    f"{reply.error.message}"
                )
        if self._stats_asks.done():
            self._ask_stats()

    def _show_stats(self, stats: StatsResult | None) -> None:
        self.stats = stats
        if stats is None:
            self.overview_tab.clear()
        else:
            self.overview_tab.show_stats(
                stats.playlists[0], stats.favourites, stats.to_remove
            )
        self._show_count()

    def _show_count(self) -> None:
        text = ""
        if self.tab() == "overview" and self.stats is not None:
            images = self.stats.playlists[0].images
            text = f"· {count(images)} image{'' if images == 1 else 's'}"
        self.count.setText(text)

    @Slot()
    def _rescan_clicked(self) -> None:
        self.overview_tab.show_notice(None)
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
        self.overview_tab.show_notice(None)
        if not self._confirm(self, question):
            return
        self._resetting = True
        self.overview_tab.set_resetting(True)
        request = ResetCycleRequest(target=playlist)
        self._client.run(request, ReplyTo(self, LibraryPage.reset_replied))

    def reset_replied(self, reply: Reply) -> None:
        self._resetting = False
        self.overview_tab.set_resetting(False)
        if isinstance(reply, ErrorReply):
            self.overview_tab.show_notice(
                f"Can't reset the cycle: {reply.error.message}"
            )
        self._ask_stats()
