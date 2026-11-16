"""
The Library page and its Overview tab, through a fake Client (never a real
app): when it asks for stats, what it shows, Rescan and Reset cycle, and
the playlist and tab remembered in ui-state.json.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from wallpaper_py.app.ui.library import LibraryPage
from wallpaper_py.app.ui.tiles import Sources
from wallpaper_py.app.ui.ui_state import UiState, load_ui_state
from wallpaper_py.core.service import Change, ListStats, PlaylistStats
from wallpaper_py.core.settings import Order
from wallpaper_py.protocol import (
    ErrorReply,
    Overview,
    PlaylistView,
    ResetCycleRequest,
    ResetCycleResult,
    StatsRequest,
    StatsResult,
)

from .fake_client import FakeClient
from .generate_test_images import generate_library
from .test_settings import answer, requests


class Confirm:
    """The reset question, answered by the test (and recorded)."""

    def __init__(self) -> None:
        self.answer = True
        self.asked: list[str] = []

    def __call__(self, _parent: object, question: str) -> bool:
        self.asked.append(question)
        return self.answer


def overview(
    *names: str, error: str | None = None, root: Path = Path("/pictures")
) -> Overview:
    """Playlists named `names`, each on the folder of its name in `root`."""
    return Overview(
        playlists=tuple(
            PlaylistView(
                id=f"{n}-id",
                name=n,
                directory=root / n,
                monitors=(),
                current=None,
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


class Pages:
    """Makes pages, and shuts them down after the test (their threads)."""

    def __init__(self, qtbot: QtBot, client: FakeClient, state_file: Path) -> None:
        self._qtbot = qtbot
        self._client = client
        self._state_file = state_file
        self.made: list[LibraryPage] = []

    def __call__(self, confirm: Confirm | None = None) -> LibraryPage:
        page = (
            LibraryPage(self._client, state_file=self._state_file)
            if confirm is None
            else LibraryPage(self._client, confirm=confirm, state_file=self._state_file)
        )
        self._qtbot.addWidget(page)
        self.made.append(page)
        return page


@pytest.fixture(name="pages")
def fixture_pages(
    qtbot: QtBot, client: FakeClient, state_file: Path
) -> Iterator[Pages]:
    pages = Pages(qtbot, client, state_file)
    yield pages
    for page in pages.made:
        page.shutdown()


@pytest.fixture(name="page")
def fixture_page(pages: Pages, confirm: Confirm) -> LibraryPage:
    return pages(confirm)


# -- asking ----------------------------------------------------------------------


def test_asks_only_while_the_overview_tab_is_shown(
    page: LibraryPage, client: FakeClient
) -> None:
    assert not asked(client)  # the page isn't shown
    page.show()
    assert asked(client) == [StatsRequest(target="konachan")]
    answer(client, StatsRequest, stats_of())
    page.tabs.tab(1).click()  # All images
    client.changed.emit(Change("konachan", Path("/x.jpg"), "next", ("Main",)))
    assert len(asked(client)) == 1
    page.tabs.tab(0).click()  # back on Overview: asked again
    assert len(asked(client)) == 2


def test_asks_one_at_a_time_and_again_on_its_playlists_changes(
    page: LibraryPage, client: FakeClient
) -> None:
    page.show()
    other = Change("fantasy", Path("/f.jpg"), "next", ("Side",))
    client.changed.emit(other)
    client.changed.emit(Change("konachan", Path("/x.jpg"), "next", ("Main",)))
    client.publish(overview("konachan", "fantasy"))
    assert len(asked(client)) == 1  # the first is still running
    answer(client, StatsRequest, stats_of())
    assert len(asked(client)) == 2  # once more for what came meanwhile
    answer(client, StatsRequest, stats_of())
    client.changed.emit(other)
    assert len(asked(client)) == 2  # another playlist's change
    client.marks_changed.emit()  # favourites or to-remove: its counts too
    assert len(asked(client)) == 3


def test_nothing_is_asked_while_the_app_has_an_error(
    page: LibraryPage, client: FakeClient
) -> None:
    client.publish(overview("konachan", error="favourites.txt has a BOM"))
    page.show()
    assert not asked(client)
    assert page.banner.text() == "Error: favourites.txt has a BOM"
    client.failed.emit("gone")
    assert page.banner.text() == "Error: gone"


# -- what it shows -------------------------------------------------------------


def test_shows_a_shuffle_cycle(page: LibraryPage, client: FakeClient) -> None:
    page.show()
    answer(client, StatsRequest, stats_of())
    tab = page.overview_tab
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
    assert page.count.text() == "· 20,431 images"
    page.tabs.tab(3).click()  # To remove: its own count (later), not this one
    assert not page.count.text()


def test_shows_a_sorted_walk(page: LibraryPage, client: FakeClient) -> None:
    page.show()
    answer(client, StatsRequest, stats_of(order=Order.SORTED))
    tab = page.overview_tab
    assert tab.heading.text() == "This walk"
    assert tab.progress.text() == "at 1,205 of 20,383"
    assert not tab.reset_button.isVisible()


def test_an_error_shows_instead_of_the_numbers(
    page: LibraryPage, client: FakeClient
) -> None:
    page.show()
    answer(client, StatsRequest, ErrorReply.make("failed", "to-remove.txt is busy"))
    tab = page.overview_tab
    assert tab.message.text() == "Can't count konachan's images: to-remove.txt is busy"
    assert not tab.content.isVisible()


def test_no_playlists(page: LibraryPage, client: FakeClient) -> None:
    client.publish(overview())
    page.show()
    assert not asked(client)
    assert not page.playlist_combo.isEnabled()
    assert page.overview_tab.message.text() == "No playlists yet: add one in Settings."


# -- buttons -------------------------------------------------------------------


def test_rescan_asks_with_rescan(page: LibraryPage, client: FakeClient) -> None:
    page.show()
    answer(client, StatsRequest, stats_of())
    page.overview_tab.rescan_button.click()
    assert asked(client)[-1] == StatsRequest(target="konachan", rescan=True)
    answer(client, StatsRequest, stats_of())
    client.changed.emit(Change("konachan", Path("/x.jpg"), "next", ("Main",)))
    assert asked(client)[-1] == StatsRequest(target="konachan")  # only once


def test_reset_cycle_asks_first(
    page: LibraryPage, client: FakeClient, confirm: Confirm
) -> None:
    page.show()
    answer(client, StatsRequest, stats_of())
    button = page.overview_tab.reset_button
    confirm.answer = False
    button.click()
    assert confirm.asked == [
        "Start konachan's shuffle cycle over? All 20,383 images become unseen."
    ]
    assert not requests(client, ResetCycleRequest)

    confirm.answer = True
    button.click()
    assert [r for r, _ in requests(client, ResetCycleRequest)] == [
        ResetCycleRequest(target="konachan")
    ]
    assert not button.isEnabled()  # until the reply
    answer(client, ResetCycleRequest, ResetCycleResult(playlist="konachan", unseen=1))
    assert button.isEnabled()
    assert len(asked(client)) == 2  # the new numbers


def test_a_failed_reset_says_why_beside_the_button(
    page: LibraryPage, client: FakeClient
) -> None:
    page.show()
    answer(client, StatsRequest, stats_of())
    page.overview_tab.reset_button.click()
    answer(client, ResetCycleRequest, ErrorReply.make("no_images", "nothing in it"))
    answer(client, StatsRequest, stats_of())  # asked again after the reset
    notice = page.overview_tab.notice
    assert notice.isVisible()
    assert notice.text() == "Can't reset the cycle: nothing in it"
    page.overview_tab.rescan_button.click()
    assert not notice.isVisible()


# -- remembered ----------------------------------------------------------------


def test_the_playlist_and_tab_are_remembered(
    page: LibraryPage, pages: Pages, state_file: Path
) -> None:
    page.show()
    page.playlist_combo.setCurrentIndex(1)
    page.tabs.tab(4).click()  # History
    assert load_ui_state(state_file) == UiState(
        library_playlist="fantasy", library_tab="history"
    )

    again = pages()
    assert again.playlist() == "fantasy"
    assert again.tab() == "history"
    assert again.stack.currentIndex() == 4


def test_choosing_another_playlist_shows_its_numbers(
    page: LibraryPage, client: FakeClient
) -> None:
    page.show()
    page.playlist_combo.setCurrentIndex(1)  # while konachan's are asked
    answer(client, StatsRequest, stats_of())  # konachan's: no longer wanted
    assert page.overview_tab.stats is None
    assert asked(client)[-1] == StatsRequest(target="fantasy")
    answer(client, StatsRequest, stats_of("fantasy"))
    stats = page.overview_tab.stats
    assert stats is not None and stats.name == "fantasy"


def test_a_playlist_gone_falls_back_to_the_first(
    page: LibraryPage, client: FakeClient
) -> None:
    page.playlist_combo.setCurrentIndex(1)
    client.publish(overview("konachan", "walls"))
    assert page.playlist() == "konachan"
    assert [page.playlist_combo.itemText(i) for i in range(2)] == ["konachan", "walls"]


@pytest.mark.parametrize("text", ["not json", '{"library_tab": "nope"}'])
def test_an_unreadable_state_file_gives_the_defaults(
    pages: Pages, state_file: Path, text: str
) -> None:
    state_file.write_text(text, encoding="utf-8")
    page = pages()
    assert (page.playlist(), page.tab()) == ("konachan", "overview")


# -- All images and the thumbnails --------------------------------------------------


def test_all_images_shows_the_playlists_folder(
    qtbot: QtBot, page: LibraryPage, client: FakeClient, tmp_path: Path
) -> None:
    konachan = generate_library(tmp_path / "konachan", 8)
    fantasy = generate_library(tmp_path / "fantasy", 3)
    client.publish(overview("konachan", "fantasy", root=tmp_path))
    page.show()
    page.tabs.tab(1).click()  # All images
    assert page.all_grid.isVisible()
    qtbot.waitUntil(lambda: page.all_grid.images() == konachan)
    on_screen = [konachan[r] for r in page.all_grid.visible_rows()]
    qtbot.waitUntil(lambda: all(page.tiles.get(p) is not None for p in on_screen))
    page.playlist_combo.setCurrentIndex(1)
    assert page.all_grid.images() != konachan  # not konachan's while listing
    qtbot.waitUntil(lambda: page.all_grid.images() == fantasy)


def test_the_folder_is_listed_only_while_all_images_is_shown(
    qtbot: QtBot, page: LibraryPage, client: FakeClient, tmp_path: Path
) -> None:
    konachan = generate_library(tmp_path / "konachan", 2)
    client.publish(overview("konachan", "fantasy", root=tmp_path))
    page.show()  # on Overview
    qtbot.wait(50)
    assert not page.all_grid.images() and page.folder_scan.asked() is None
    page.tabs.tab(1).click()
    qtbot.waitUntil(lambda: page.all_grid.images() == konachan)
    (added,) = generate_library(tmp_path / "konachan" / "new", 1)
    page.tabs.tab(0).click()
    page.tabs.tab(1).click()  # shown again: listed again
    qtbot.waitUntil(lambda: page.all_grid.images() == [*konachan, added])


def test_thumbnails_are_made_for_every_playlist_and_list(
    page: LibraryPage, client: FakeClient, tmp_path: Path
) -> None:
    client.publish(overview("konachan", "fantasy", root=tmp_path))
    lists = (tmp_path / "favourites.txt", tmp_path / "to-remove.txt")
    folders = (tmp_path / "konachan", tmp_path / "fantasy")
    assert page.pregenerator.sources() == Sources(folders, lists)
    page.playlist_combo.setCurrentIndex(1)  # fantasy's first now
    assert page.pregenerator.sources() == Sources(folders[::-1], lists)
    # The app's service stopped: its overview names no playlists, which is
    # no reason to stop (or to prune everything).
    client.publish(overview(error="favourites.txt is not UTF-8"))
    assert page.pregenerator.sources() == Sources(folders[::-1], lists)
