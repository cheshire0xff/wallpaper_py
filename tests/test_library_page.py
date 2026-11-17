"""
The Library page, through a fake Client (never a real app): the tabs and
what each shows (the folder, the lists, the history, what comes next, the
numbers), the switches, the badges, the header's count, Rescan and Reset
cycle, and what is remembered in ui-state.json.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from wallpaper_py.app.ui.library import UPCOMING, LibraryPage
from wallpaper_py.app.ui.library_grids import GridTab
from wallpaper_py.app.ui.tiles import Sources
from wallpaper_py.app.ui.ui_state import UiState, load_ui_state
from wallpaper_py.core.library import path_key
from wallpaper_py.core.service import Change, ListStats, PlaylistStats
from wallpaper_py.core.settings import Order
from wallpaper_py.core.store import Store
from wallpaper_py.protocol import (
    ErrorReply,
    Overview,
    PlaylistView,
    ResetCycleRequest,
    ResetCycleResult,
    StatsRequest,
    StatsResult,
    UpcomingRequest,
    UpcomingResult,
)

from .fake_client import FakeClient
from .fakes import add_playlists
from .generate_test_images import generate_library
from .test_settings import answer, requests

# The tabs, in order.
ALL, FAVOURITES, TO_REMOVE, HISTORY, UPCOMING_TAB, STATS = range(6)


class Confirm:
    """The reset question, answered by the test (and recorded)."""

    def __init__(self) -> None:
        self.answer = True
        self.asked: list[str] = []

    def __call__(self, _parent: object, question: str) -> bool:
        self.asked.append(question)
        return self.answer


def overview(
    *names: str,
    error: str | None = None,
    root: Path = Path("/pictures"),
    current: dict[str, Path] | None = None,
) -> Overview:
    """Playlists named `names`, each on the folder of its name in `root`."""
    current = current or {}
    return Overview(
        playlists=tuple(
            PlaylistView(
                id=f"{n}-id",
                name=n,
                directory=root / n,
                monitors=(),
                current=current.get(n),
                favourite=False,
            )
            for n in names
        ),
        error=error,
        favourites_file=None if error else root / "favourites.txt",
        to_remove_file=None if error else root / "to-remove.txt",
    )


def stats_of(name: str = "konachan", order: Order = Order.SHUFFLE) -> StatsResult:
    playlist = PlaylistStats(
        name=name,
        directory=Path("/pictures") / name,
        order=order,
        scan_age=150.0,
        images=20_431,
        seen=1_204 if order is Order.SHUFFLE else 1_205,
        unseen=19_179 if order is Order.SHUFFLE else 19_178,
        skipped=48,
        favourites_here=120,
        to_remove_here=48,
    )
    return StatsResult(
        playlists=(playlist,),
        favourites=ListStats(Path("/data/favourites.txt"), listed=312, available=298),
        to_remove=ListStats(Path("/data/to-remove.txt"), listed=60, available=48),
    )


def asked(client: FakeClient) -> list[StatsRequest]:
    return [r for r, _ in requests(client, StatsRequest)]


def write_list(file: Path, *paths: Path) -> None:
    file.write_text("".join(f"{p}\n" for p in paths), encoding="utf-8")


def shown(tab: GridTab) -> list[Path]:
    return tab.grid.images()


@pytest.fixture(name="client")
def fixture_client() -> FakeClient:
    client = FakeClient()
    client.publish(overview("konachan", "fantasy"))
    return client


@pytest.fixture(name="confirm")
def fixture_confirm() -> Confirm:
    return Confirm()


@pytest.fixture(name="state_file")
def fixture_state_file(tmp_path: Path) -> Path:
    return tmp_path / "ui-state.json"


@pytest.fixture(name="database")
def fixture_database(tmp_path: Path) -> Path:
    return tmp_path / "state.sqlite3"


class Pages:
    """Makes pages, and shuts them down after the test (their threads)."""

    def __init__(
        self, qtbot: QtBot, client: FakeClient, state_file: Path, database: Path
    ) -> None:
        self._qtbot = qtbot
        self._client = client
        self._state_file = state_file
        self._database = database
        self.made: list[LibraryPage] = []

    def __call__(self, confirm: Confirm | None = None) -> LibraryPage:
        page = LibraryPage(
            self._client,
            confirm=confirm or Confirm(),
            state_file=self._state_file,
            database=self._database,
        )
        self._qtbot.addWidget(page)
        page.resize(1100, 700)
        self.made.append(page)
        return page


@pytest.fixture(name="pages")
def fixture_pages(
    qtbot: QtBot, client: FakeClient, state_file: Path, database: Path
) -> Iterator[Pages]:
    pages = Pages(qtbot, client, state_file, database)
    yield pages
    for page in pages.made:
        page.shutdown()


@pytest.fixture(name="page")
def fixture_page(pages: Pages, confirm: Confirm) -> LibraryPage:
    return pages(confirm)


@pytest.fixture(name="stats_page")
def fixture_stats_page(page: LibraryPage) -> LibraryPage:
    """The page on its Stats tab."""
    page.tabs.tab(STATS).click()
    return page


# -- the tabs --------------------------------------------------------------------


def test_all_images_first_and_stats_last(page: LibraryPage) -> None:
    labels = [page.tabs.tab(i).text() for i in range(6)]
    assert labels == [
        "All images",
        "Favourites",
        "To remove",
        "History",
        "Upcoming",
        "Stats",
    ]
    assert page.tab() == "all"  # where a new user lands


def test_each_tab_has_its_toolbar(page: LibraryPage) -> None:
    grids = page.grids
    assert grids["all"].switch.text() == "Hide removed"
    assert grids["all"].switched_on()  # on by default
    assert grids["favourites"].switch.text() == "Only konachan"
    assert not grids["favourites"].switched_on()
    assert grids["history"].sort_box.itemText(2) == "Newest shown"
    assert grids["all"].sort_box.itemText(2) == "Newest added"
    assert grids["history"].switch.text() == "Hide removed"
    assert grids["history"].switched_on()  # on by default, as on All images
    assert grids["upcoming"].switch.isHidden()
    assert grids["upcoming"].sort_box.isHidden() and grids["upcoming"].sort() is None
    page.playlist_combo.setCurrentIndex(1)
    assert grids["to_remove"].switch.text() == "Only fantasy"


# -- asking for the numbers ------------------------------------------------------


def test_stats_are_asked_while_shown_on_any_tab(
    page: LibraryPage, client: FakeClient
) -> None:
    assert not asked(client)  # the page isn't shown
    page.show()  # on All images: the header's seen count
    assert asked(client) == [StatsRequest(target="konachan-id")]
    answer(client, StatsRequest, stats_of())
    page.tabs.tab(FAVOURITES).click()
    assert len(asked(client)) == 1  # nothing new to count
    page.tabs.tab(STATS).click()  # shown: asked again
    assert len(asked(client)) == 2


def test_asks_one_at_a_time_and_again_on_its_playlists_changes(
    page: LibraryPage, client: FakeClient
) -> None:
    page.show()
    other = Change("fantasy-id", "fantasy", Path("/f.jpg"), "next", ("Side",))
    client.changed.emit(other)
    client.changed.emit(
        Change("konachan-id", "konachan", Path("/x.jpg"), "next", ("Main",))
    )
    client.publish(overview("konachan", "fantasy"))
    assert len(asked(client)) == 1  # the first is still running
    answer(client, StatsRequest, stats_of())
    assert len(asked(client)) == 2  # once more for what came meanwhile
    answer(client, StatsRequest, stats_of())
    client.changed.emit(other)
    assert len(asked(client)) == 2  # another playlist's change
    client.marks_changed.emit()  # favourites or to-remove: its counts too
    assert len(asked(client)) == 3


def test_nothing_is_asked_while_the_daemon_has_an_error(
    page: LibraryPage, client: FakeClient
) -> None:
    client.publish(overview("konachan", error="favourites.txt has a BOM"))
    page.show()
    assert not asked(client)
    assert page.banner.text() == "Error: favourites.txt has a BOM"
    client.failed.emit("gone")
    assert page.banner.text() == "Error: gone"


# -- the Stats tab -----------------------------------------------------------------


def test_shows_a_shuffle_cycle(stats_page: LibraryPage, client: FakeClient) -> None:
    page = stats_page
    page.show()
    answer(client, StatsRequest, stats_of())
    tab = page.stats_tab
    assert tab.content.isVisible() and not tab.message.isVisible()
    assert tab.heading.text() == "This cycle"
    assert tab.progress.text() == "6 %"
    assert (tab.progress_bar.value(), tab.progress_bar.maximum()) == (1_204, 20_383)
    numbers = (tab.images, tab.seen, tab.unseen, tab.skipped)
    assert [n.text() for n in numbers] == ["20,431", "1,204", "19,179", "48"]
    images = tab.images_row.description.text()
    assert images == f"{Path('/pictures/konachan')}, scanned 2 min ago"
    assert tab.seen_row.description.text() == "shown in this shuffle cycle"
    assert tab.reset_button.isVisible()
    assert tab.favourites.text() == "312 listed · 298 available · 120 here"
    assert tab.to_remove.text() == "60 listed · 48 available · 48 here"
    assert tab.to_remove_row.description.text() == "to-remove.txt"
    assert page.count.text() == "· 20,431 images · 1,204 seen"


def test_shows_a_sorted_walk(stats_page: LibraryPage, client: FakeClient) -> None:
    stats_page.show()
    answer(client, StatsRequest, stats_of(order=Order.SORTED))
    tab = stats_page.stats_tab
    assert tab.heading.text() == "This walk"
    assert tab.progress.text() == "at 1,205 of 20,383"
    assert not tab.reset_button.isVisible()


def test_an_error_shows_instead_of_the_numbers(
    stats_page: LibraryPage, client: FakeClient
) -> None:
    stats_page.show()
    answer(client, StatsRequest, ErrorReply.make("failed", "to-remove.txt is busy"))
    tab = stats_page.stats_tab
    assert tab.message.text() == "Can't count konachan's images: to-remove.txt is busy"
    assert not tab.content.isVisible()


def test_no_playlists(
    qtbot: QtBot, stats_page: LibraryPage, client: FakeClient, tmp_path: Path
) -> None:
    client.publish(overview(root=tmp_path))
    stats_page.show()
    assert not asked(client)
    assert not stats_page.playlist_combo.isEnabled()
    message = "No playlists yet: add one in Settings."
    assert stats_page.stats_tab.message.text() == message
    qtbot.waitUntil(lambda: stats_page.lists_read.asked() is None)  # lists read
    assert stats_page.grids["all"].message.text() == message


def test_rescan_asks_with_rescan(stats_page: LibraryPage, client: FakeClient) -> None:
    stats_page.show()
    answer(client, StatsRequest, stats_of())
    stats_page.stats_tab.rescan_button.click()
    assert asked(client)[-1] == StatsRequest(target="konachan-id", rescan=True)
    answer(client, StatsRequest, stats_of())
    client.changed.emit(
        Change("konachan-id", "konachan", Path("/x.jpg"), "next", ("Main",))
    )
    assert asked(client)[-1] == StatsRequest(target="konachan-id")  # only once


def test_reset_cycle_asks_first(
    stats_page: LibraryPage, client: FakeClient, confirm: Confirm
) -> None:
    stats_page.show()
    answer(client, StatsRequest, stats_of())
    button = stats_page.stats_tab.reset_button
    confirm.answer = False
    button.click()
    assert confirm.asked == [
        "Start konachan's shuffle cycle over? All 20,383 images become unseen."
    ]
    assert not requests(client, ResetCycleRequest)

    confirm.answer = True
    button.click()
    assert [r for r, _ in requests(client, ResetCycleRequest)] == [
        ResetCycleRequest(target="konachan-id")
    ]
    assert not button.isEnabled()  # until the reply
    answer(client, ResetCycleRequest, ResetCycleResult(playlist="konachan", unseen=1))
    assert button.isEnabled()
    assert len(asked(client)) == 2  # the new numbers


def test_a_failed_reset_says_why_beside_the_button(
    stats_page: LibraryPage, client: FakeClient
) -> None:
    stats_page.show()
    answer(client, StatsRequest, stats_of())
    stats_page.stats_tab.reset_button.click()
    answer(client, ResetCycleRequest, ErrorReply.make("no_images", "nothing in it"))
    answer(client, StatsRequest, stats_of())  # asked again after the reset
    notice = stats_page.stats_tab.notice
    assert notice.isVisible()
    assert notice.text() == "Can't reset the cycle: nothing in it"
    stats_page.stats_tab.rescan_button.click()
    assert not notice.isVisible()


# -- remembered ----------------------------------------------------------------


def test_the_playlist_tab_sorts_and_chips_are_remembered(
    page: LibraryPage, pages: Pages, state_file: Path
) -> None:
    page.show()
    assert all(g.badges_shown() and g.grid.badges for g in page.grids.values())
    page.playlist_combo.setCurrentIndex(1)
    page.tabs.tab(HISTORY).click()
    page.grids["history"].sort_box.setCurrentIndex(3)  # oldest shown
    page.grids["all"].switch.click()  # Hide removed: off
    page.grids["favourites"].badges.click()  # off on this tab only
    assert not page.grids["favourites"].grid.badges
    assert page.grids["all"].grid.badges
    assert load_ui_state(state_file) == UiState(
        library_playlist="fantasy-id",
        library_tab="history",
        library_sorts={"history": "oldest"},
        library_switches={"all": False},
        library_badges={"favourites": False},
    )

    again = pages()
    assert again.playlist() == "fantasy-id"
    assert again.tab() == "history"
    assert again.stack.currentIndex() == HISTORY
    assert again.grids["history"].sort() == "oldest"
    assert again.grids["favourites"].sort() == "newest"  # its default
    assert not again.grids["all"].switched_on()
    assert not again.grids["favourites"].badges_shown()
    assert not again.grids["favourites"].grid.badges
    assert again.grids["to_remove"].badges_shown()


def test_choosing_another_playlist_shows_its_numbers(
    page: LibraryPage, client: FakeClient
) -> None:
    page.show()
    page.playlist_combo.setCurrentIndex(1)  # while konachan's are asked
    answer(client, StatsRequest, stats_of())  # konachan's: no longer wanted
    assert page.stats_tab.stats is None
    assert asked(client)[-1] == StatsRequest(target="fantasy-id")
    answer(client, StatsRequest, stats_of("fantasy"))
    stats = page.stats_tab.stats
    assert stats is not None and stats.name == "fantasy"


def test_a_playlist_gone_falls_back_to_the_first(
    page: LibraryPage, client: FakeClient
) -> None:
    page.playlist_combo.setCurrentIndex(1)
    client.publish(overview("konachan", "walls"))
    assert page.playlist() == "konachan-id"
    assert [page.playlist_combo.itemText(i) for i in range(2)] == ["konachan", "walls"]


def test_a_stored_name_is_not_an_id_so_the_first_is_chosen(
    pages: Pages, state_file: Path
) -> None:
    state_file.write_text('{"library_playlist": "fantasy"}', encoding="utf-8")
    assert pages().playlist() == "konachan-id"


def test_a_rename_keeps_the_playlist_chosen(
    page: LibraryPage, client: FakeClient
) -> None:
    page.playlist_combo.setCurrentIndex(1)
    before = overview("konachan", "fantasy")
    renamed = before.playlists[1].model_copy(update={"name": "dragons"})
    client.publish(
        before.model_copy(update={"playlists": (before.playlists[0], renamed)})
    )
    assert page.playlist() == "fantasy-id"
    assert page.playlist_combo.currentText() == "dragons"
    assert page.playlist_name("fantasy-id") == "dragons"
    assert page.grids["favourites"].switch.text() == "Only dragons"


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"library_tab": "nope"}',
        # Before the grid tabs: the defaults once, no migration.
        '{"library_playlist": "fantasy-id", "library_tab": "overview"}',
    ],
)
def test_an_unreadable_state_file_gives_the_defaults(
    pages: Pages, state_file: Path, text: str
) -> None:
    state_file.write_text(text, encoding="utf-8")
    page = pages()
    assert (page.playlist(), page.tab()) == ("konachan-id", "all")


# -- All images and the thumbnails --------------------------------------------------


def test_all_images_shows_the_playlists_folder(
    qtbot: QtBot, page: LibraryPage, client: FakeClient, tmp_path: Path
) -> None:
    konachan = generate_library(tmp_path / "konachan", 8)
    fantasy = generate_library(tmp_path / "fantasy", 3)
    client.publish(overview("konachan", "fantasy", root=tmp_path))
    page.show()
    grid = page.grids["all"].grid
    qtbot.waitUntil(lambda: grid.images() == konachan)
    assert grid.isVisible()
    on_screen = [konachan[r] for r in grid.visible_rows()]
    qtbot.waitUntil(lambda: all(page.tiles.get(p) is not None for p in on_screen))
    page.playlist_combo.setCurrentIndex(1)
    assert grid.images() != konachan  # not konachan's while listing
    qtbot.waitUntil(lambda: grid.images() == fantasy)


def test_the_folder_is_listed_only_while_all_images_is_shown(
    qtbot: QtBot, stats_page: LibraryPage, client: FakeClient, tmp_path: Path
) -> None:
    page = stats_page
    konachan = generate_library(tmp_path / "konachan", 2)
    client.publish(overview("konachan", "fantasy", root=tmp_path))
    page.show()  # on Stats
    qtbot.wait(50)
    grid = page.grids["all"].grid
    assert not grid.images() and page.folder_read.asked() is None
    page.tabs.tab(ALL).click()
    qtbot.waitUntil(lambda: grid.images() == konachan)
    (added,) = generate_library(tmp_path / "konachan" / "new", 1)
    page.tabs.tab(STATS).click()
    page.tabs.tab(ALL).click()  # shown again: listed again
    qtbot.waitUntil(lambda: added in grid.images())
    assert len(grid.images()) == 3


def test_hide_removed_leaves_out_what_to_remove_lists(
    qtbot: QtBot, page: LibraryPage, client: FakeClient, tmp_path: Path
) -> None:
    images = generate_library(tmp_path / "konachan", 4)
    write_list(tmp_path / "to-remove.txt", images[1])
    client.publish(overview("konachan", root=tmp_path))
    page.show()
    tab = page.grids["all"]
    kept = [images[0], *images[2:]]
    qtbot.waitUntil(lambda: shown(tab) == kept)
    assert page.count.text() == "· 3 images"
    tab.switch.click()  # off: every image, the removed one marked
    assert shown(tab) == images
    assert tab.grid.marks.to_remove == {path_key(images[1])}
    write_list(tmp_path / "to-remove.txt", images[1], images[3])
    tab.switch.click()
    client.marks_changed.emit()  # read again
    qtbot.waitUntil(lambda: shown(tab) == images[::2])


def test_filtering_by_name_narrows_and_counts(
    qtbot: QtBot, page: LibraryPage, client: FakeClient, tmp_path: Path
) -> None:
    images = generate_library(tmp_path / "konachan", 12)
    client.publish(overview("konachan", root=tmp_path))
    page.show()
    answer(client, StatsRequest, stats_of())
    tab = page.grids["all"]
    qtbot.waitUntil(lambda: len(shown(tab)) == 12)
    assert page.count.text() == "· 12 images · 1,204 seen"
    tab.filter.setText(images[10].stem.upper())
    assert page.count.text() == "· 12 images · 1,204 seen"  # not narrowed yet
    qtbot.waitUntil(lambda: shown(tab) == [images[10]])  # after a pause
    assert page.count.text() == "· 1 of 12 · 1,204 seen"
    assert tab.grid.needle == images[10].stem.upper()  # the tiles' captions
    tab.filter.setText("no such name")
    qtbot.waitUntil(lambda: not shown(tab))
    assert tab.message.isVisible()
    assert tab.message.text() == "No file name has “no such name” in it."
    tab.filter.clear()
    qtbot.waitUntil(lambda: len(shown(tab)) == 12)
    assert tab.grid.needle == ""  # no captions
    tab.sort_box.setCurrentIndex(1)  # name Z–A
    assert shown(tab) == images[::-1]


def test_thumbnails_are_made_for_every_playlist_and_list(
    page: LibraryPage, client: FakeClient, tmp_path: Path
) -> None:
    client.publish(overview("konachan", "fantasy", root=tmp_path))
    lists = (tmp_path / "favourites.txt", tmp_path / "to-remove.txt")
    folders = (tmp_path / "konachan", tmp_path / "fantasy")
    assert page.pregenerator.sources() == Sources(folders, lists)
    page.playlist_combo.setCurrentIndex(1)  # fantasy's first now
    assert page.pregenerator.sources() == Sources(folders[::-1], lists)
    # The daemon's service stopped: its overview names no playlists, which is
    # no reason to stop (or to prune everything).
    client.publish(overview(error="favourites.txt is not UTF-8"))
    assert page.pregenerator.sources() == Sources(folders[::-1], lists)


# -- Favourites and To remove ------------------------------------------------------


def test_favourites_show_the_whole_list_newest_first(
    qtbot: QtBot,
    page: LibraryPage,
    client: FakeClient,
    tmp_path: Path,
    state_file: Path,
) -> None:
    mine = tmp_path / "konachan" / "a.jpg"
    elsewhere = tmp_path / "elsewhere" / "b.jpg"
    gone = tmp_path / "konachan" / "gone.jpg"
    write_list(tmp_path / "favourites.txt", mine, elsewhere, gone)
    client.publish(overview("konachan", root=tmp_path, current={"konachan": mine}))
    page.tabs.tab(FAVOURITES).click()
    page.show()
    tab = page.grids["favourites"]
    qtbot.waitUntil(lambda: shown(tab) == [gone, elsewhere, mine])
    marks = tab.grid.marks
    assert marks.favourites == {path_key(p) for p in (mine, elsewhere, gone)}
    assert marks.on_screen == {path_key(mine)}
    tab.switch.click()  # Only konachan
    assert shown(tab) == [gone, mine]
    assert load_ui_state(state_file).library_switches == {"favourites": True}


def test_a_list_that_cant_be_read_says_why(
    qtbot: QtBot, page: LibraryPage, client: FakeClient, tmp_path: Path
) -> None:
    (tmp_path / "to-remove.txt").write_bytes(b"\xef\xbb\xbf/x.jpg\n")
    client.publish(overview("konachan", root=tmp_path))
    page.tabs.tab(TO_REMOVE).click()
    page.show()
    tab = page.grids["to_remove"]
    qtbot.waitUntil(lambda: "BOM" in tab.message.text())
    assert tab.message.isVisible() and not shown(tab)
    page.tabs.tab(HISTORY).click()  # Hide removed is on: it can't
    history = page.grids["history"]
    qtbot.waitUntil(lambda: "Can't hide the removed images" in history.message.text())
    assert "BOM" in history.message.text()


def test_an_empty_list_says_so(
    qtbot: QtBot, page: LibraryPage, client: FakeClient, tmp_path: Path
) -> None:
    client.publish(overview("konachan", root=tmp_path))
    page.tabs.tab(FAVOURITES).click()
    page.show()
    tab = page.grids["favourites"]
    qtbot.waitUntil(lambda: tab.message.text() == "No favourites.")
    assert page.count.text() == "· 0 images"


# -- History and Upcoming ----------------------------------------------------------


def test_history_is_read_from_the_database_and_again_on_a_change(
    qtbot: QtBot, page: LibraryPage, client: FakeClient, database: Path
) -> None:
    first, second = Path("/p/a.jpg").absolute(), Path("/p/b.jpg").absolute()
    with add_playlists(Store(database), "konachan-id") as store:
        store.append_history("konachan-id", first, 100.0)
    page.tabs.tab(HISTORY).click()
    page.show()
    tab = page.grids["history"]
    qtbot.waitUntil(lambda: shown(tab) == [first])
    (entry,) = tab.grid.list_model.entries()
    assert entry.shown_at == 100.0  # its caption: when
    with Store(database) as store:
        store.append_history("konachan-id", second, 200.0)
        store.append_history("konachan-id", first, 300.0)
    client.changed.emit(Change("konachan-id", "konachan", first, "next", ("Main",)))
    qtbot.waitUntil(lambda: shown(tab) == [first, second, first])  # repeats kept


def test_history_can_hide_removed_too(
    qtbot: QtBot,
    page: LibraryPage,
    client: FakeClient,
    database: Path,
    tmp_path: Path,
) -> None:
    first, second = Path("/p/a.jpg").absolute(), Path("/p/b.jpg").absolute()
    write_list(tmp_path / "to-remove.txt", second)
    client.publish(overview("konachan", root=tmp_path))
    with add_playlists(Store(database), "konachan-id") as store:
        store.append_history("konachan-id", first, 100.0)
        store.append_history("konachan-id", second, 200.0)
    page.tabs.tab(HISTORY).click()
    page.show()
    tab = page.grids["history"]
    qtbot.waitUntil(lambda: shown(tab) == [first])  # on by default
    tab.switch.click()  # off: all of it, the removed one marked
    assert shown(tab) == [second, first]
    tab.switch.click()
    write_list(tmp_path / "to-remove.txt")
    client.marks_changed.emit()  # the list read again: nothing to hide
    qtbot.waitUntil(lambda: shown(tab) == [second, first])


def test_upcoming_asks_the_daemon_and_keeps_its_order(
    page: LibraryPage, client: FakeClient
) -> None:
    page.tabs.tab(UPCOMING_TAB).click()
    page.show()
    (request,) = [r for r, _ in requests(client, UpcomingRequest)]
    assert request == UpcomingRequest(target="konachan-id", limit=UPCOMING + 1)
    images = (Path("/p/z.jpg").absolute(), Path("/p/a.jpg").absolute())
    answer(client, UpcomingRequest, UpcomingResult(playlist="konachan", images=images))
    tab = page.grids["upcoming"]
    assert shown(tab) == list(images)
    client.changed.emit(Change("konachan-id", "konachan", images[0], "next", ("Main",)))
    assert len(requests(client, UpcomingRequest)) == 2  # asked again
    answer(client, UpcomingRequest, ErrorReply.make("failed", "no images"))
    assert tab.message.text() == "Can't list what comes next: no images"


def test_upcoming_says_when_more_come_after_it(
    page: LibraryPage, client: FakeClient
) -> None:
    page.tabs.tab(UPCOMING_TAB).click()
    page.show()
    images = tuple(Path(f"/p/{n}.jpg").absolute() for n in range(UPCOMING + 1))
    answer(client, UpcomingRequest, UpcomingResult(playlist="konachan", images=images))
    assert shown(page.grids["upcoming"]) == list(images[:UPCOMING])
    assert f"{UPCOMING}+ images" in page.count.text()
    client.changed.emit(Change("konachan-id", "konachan", images[0], "next", ("Main",)))
    answer(
        client, UpcomingRequest, UpcomingResult(playlist="konachan", images=images[1:])
    )
    assert f"{UPCOMING} images" in page.count.text()  # exactly that many left


def test_upcoming_is_not_asked_on_every_new_image(
    page: LibraryPage, client: FakeClient
) -> None:
    page.tabs.tab(UPCOMING_TAB).click()
    page.show()
    answer(client, UpcomingRequest, UpcomingResult(playlist="konachan", images=()))
    current = {"fantasy": Path("/pictures/fantasy/x.jpg")}
    client.publish(overview("konachan", "fantasy", current=current))
    assert len(requests(client, UpcomingRequest)) == 1  # another playlist's image
    client.publish(overview("konachan", "fantasy", "walls"))  # saved settings
    assert len(requests(client, UpcomingRequest)) == 2
