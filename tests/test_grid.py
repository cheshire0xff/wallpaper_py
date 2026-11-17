"""
The Library's image grid, offscreen: only the tiles on screen are asked
for, a scroll drops the requests left behind, tiles fade in, badges show
(and hide), captions show the match while filtered, a click selects
nothing, a list read again keeps the scroll, the fast-scroll bar moves the
grid, and with generated images the thumbnails land on disk.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from wallpaper_py.app.ui import theme
from wallpaper_py.app.ui.entries import Entry
from wallpaper_py.app.ui.grid import (
    FastScroll,
    ImageGrid,
    Marks,
    fit_match,
    split_match,
)
from wallpaper_py.app.ui.tiles import TileLoader, tile_pixels
from wallpaper_py.core.thumbnail_cache import ThumbnailCache

from .fakes import GONE_IMAGE, TILE_RED, FakeTileDecoder
from .generate_test_images import generate_library

PATHS = [Path(f"/p/img{i}.jpg") for i in range(1000)]
GONE = GONE_IMAGE
RED = TILE_RED


def entries_of(paths: list[Path]) -> list[Entry]:
    return [Entry(p, i) for i, p in enumerate(paths)]


def decoder_of(hold_all: bool = False) -> FakeTileDecoder:
    return FakeTileDecoder(tile_pixels(1.0), hold_all=hold_all)


class Grids:
    """Makes shown grids, and closes their loaders after the test."""

    def __init__(self, qtbot: QtBot) -> None:
        self._qtbot = qtbot
        self.loaders: list[TileLoader] = []

    def __call__(self, decoder: FakeTileDecoder, threads: int = 2) -> ImageGrid:
        loader = TileLoader(decoder, threads=threads)
        self.loaders.append(loader)
        grid = ImageGrid(loader)
        self._qtbot.addWidget(grid)
        grid.resize(1040, 500)  # 5 columns, about 4 rows
        grid.show()
        self._qtbot.waitExposed(grid)
        return grid


@pytest.fixture(name="grids")
def fixture_grids(qtbot: QtBot) -> Iterator[Grids]:
    grids = Grids(qtbot)
    yield grids
    for loader in grids.loaders:
        loader.close()


def shown(grid: ImageGrid) -> list[Path]:
    return [grid.images()[r] for r in grid.visible_rows()]


def loaded(grid: ImageGrid, paths: list[Path]) -> bool:
    return all(grid.loader.get(p) is not None for p in paths)


def rgb(colour: QColor) -> tuple[int, int, int]:
    return colour.red(), colour.green(), colour.blue()


def colour_at_tile(grid: ImageGrid, row: int) -> QColor:
    image = grid.viewport().grab().toImage()
    centre = grid.visualRect(grid.list_model.index(row)).center()
    return image.pixelColor(centre)


def test_only_the_tiles_on_screen_are_asked_for(qtbot: QtBot, grids: Grids) -> None:
    decoder = decoder_of()
    grid = grids(decoder)
    grid.set_entries(entries_of(PATHS))
    on_screen = shown(grid)
    assert 10 <= len(on_screen) <= 40
    qtbot.waitUntil(lambda: loaded(grid, on_screen))
    assert set(decoder.started) == set(on_screen)


def test_a_scroll_drops_the_requests_left_behind(qtbot: QtBot, grids: Grids) -> None:
    decoder = decoder_of(hold_all=True)
    grid = grids(decoder, threads=1)
    grid.set_entries(entries_of(PATHS))
    first_screen = shown(grid)
    qtbot.waitUntil(lambda: len(decoder.started) == 1)  # the worker waits
    decoding = decoder.started[0]
    grid.scrollToBottom()
    last_screen = shown(grid)
    assert last_screen[-1] == PATHS[-1]
    grid.viewport().repaint()  # the bottom tiles ask for theirs
    decoder.release()
    qtbot.waitUntil(lambda: loaded(grid, last_screen))
    assert set(decoder.started) == {decoding, *last_screen}
    assert not set(decoder.started) & (set(first_screen) - {decoding})


def test_a_tile_fades_in_when_it_arrives(qtbot: QtBot, grids: Grids) -> None:
    decoder = decoder_of(hold_all=True)
    grid = grids(decoder, threads=1)
    grid.set_entries(entries_of(PATHS[:3]))
    qtbot.waitUntil(lambda: len(decoder.started) == 1)
    assert colour_at_tile(grid, 0) == theme.tones().panel  # nothing yet
    with qtbot.waitSignal(grid.loader.loaded) as signal:
        decoder.release()
    (first,) = signal.args
    assert grid.opacity(first) < 1.0  # fading in
    qtbot.waitUntil(lambda: grid.opacity(first) == 1.0)
    qtbot.waitUntil(lambda: loaded(grid, PATHS[:3]))
    assert rgb(colour_at_tile(grid, 0)) == RED


def test_a_missing_image_is_a_tile_with_its_name(qtbot: QtBot, grids: Grids) -> None:
    grid = grids(decoder_of())
    grid.set_entries(entries_of([GONE, PATHS[0]]))
    qtbot.waitUntil(lambda: loaded(grid, [GONE, PATHS[0]]))
    tile = grid.loader.get(GONE)
    assert tile is not None and tile.missing and tile.pixmap is None
    assert rgb(colour_at_tile(grid, 0)) != RED  # its name, on the panel
    assert grid.list_model.data(grid.list_model.index(0)) == "gone.jpg"


def test_the_same_images_keep_the_scroll(qtbot: QtBot, grids: Grids) -> None:
    grid = grids(decoder_of())
    grid.set_entries(entries_of(PATHS))
    grid.scrollToBottom()
    bottom = grid.verticalScrollBar().value()
    assert bottom > 0
    grid.set_entries(entries_of(list(PATHS)), top=False)  # listed again, nothing new
    assert grid.verticalScrollBar().value() == bottom
    grid.set_entries(entries_of(PATHS[::-1]))  # sorted the other way: from the top
    assert grid.verticalScrollBar().value() == 0
    qtbot.waitUntil(lambda: loaded(grid, shown(grid)))


def test_a_hidden_grid_wants_nothing(qtbot: QtBot, grids: Grids) -> None:
    decoder = decoder_of(hold_all=True)
    grid = grids(decoder, threads=1)
    grid.set_entries(entries_of(PATHS))
    qtbot.waitUntil(lambda: len(decoder.started) == 1)
    assert len(grid.loader.pending()) > 1
    grid.hide()
    assert grid.loader.pending() == set(decoder.started)  # only the one decoding
    decoder.release()
    qtbot.waitUntil(lambda: not grid.loader.pending())
    assert len(decoder.started) == 1


def test_generated_images_end_up_on_disk(
    qtbot: QtBot, grids: Grids, tmp_path: Path
) -> None:
    images = generate_library(tmp_path / "walls", 40, (1280, 720))
    cache = ThumbnailCache(tmp_path / "thumbnails", tile_pixels(1.0))
    loader = TileLoader(cache.thumbnail)
    grids.loaders.append(loader)
    grid = ImageGrid(loader)
    qtbot.addWidget(grid)
    grid.resize(1040, 500)
    grid.show()
    qtbot.waitExposed(grid)
    grid.set_entries(entries_of(images))
    on_screen = shown(grid)
    qtbot.waitUntil(lambda: loaded(grid, on_screen))
    for path in on_screen:
        tile = loader.get(path)
        assert tile is not None and tile.pixmap is not None
        assert cache.file_for(path).exists()
    portrait = loader.get(images[4])  # every fifth is portrait: it fills too
    assert portrait is not None and portrait.pixmap is not None
    size = (portrait.pixmap.width(), portrait.pixmap.height())
    assert size == tile_pixels(1.0)


def test_a_list_read_again_keeps_the_scroll(qtbot: QtBot, grids: Grids) -> None:
    grid = grids(decoder_of())
    grid.set_entries(entries_of(PATHS))
    qtbot.waitUntil(lambda: grid.verticalScrollBar().maximum() > 2000)  # laid out
    grid.verticalScrollBar().setValue(2000)
    grid.set_entries(entries_of(PATHS[:-1]), top=False)  # one fewer at the end
    assert grid.verticalScrollBar().value() == 2000
    grid.set_entries(entries_of(PATHS[:-1]))  # the same, from the top
    assert grid.verticalScrollBar().value() == 0
    qtbot.waitUntil(lambda: loaded(grid, shown(grid)))


def test_badges_are_drawn_on_the_tiles(qtbot: QtBot, grids: Grids) -> None:
    grid = grids(decoder_of())
    grid.set_entries(entries_of(PATHS[:2]))
    qtbot.waitUntil(lambda: loaded(grid, PATHS[:2]))
    qtbot.waitUntil(lambda: grid.opacity(PATHS[0]) == 1.0)

    def corner(row: int) -> tuple[int, int, int]:
        rect = grid.visualRect(grid.list_model.index(row))
        image = grid.viewport().grab().toImage()
        return rgb(image.pixelColor(rect.right() - 6 - 11, rect.top() + 6 + 11))

    assert corner(0) == RED
    favourite = Entry(PATHS[0], 0).key
    grid.set_marks(Marks(favourites=frozenset({favourite})))
    assert corner(0) != RED  # the star on its disc
    assert corner(1) == RED
    grid.set_badges(False)
    assert corner(0) == RED
    grid.set_badges(True)
    assert corner(0) != RED


def test_a_click_selects_nothing(grids: Grids) -> None:
    grid = grids(decoder_of())
    grid.set_entries(entries_of(PATHS[:2]))
    rect = grid.visualRect(grid.list_model.index(0))
    QTest.mouseClick(grid.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert not grid.selectionModel().hasSelection()


def test_while_filtered_tiles_show_where_the_name_matches(
    qtbot: QtBot, grids: Grids
) -> None:
    grid = grids(decoder_of())
    grid.set_entries(entries_of(PATHS[:2]))
    qtbot.waitUntil(lambda: loaded(grid, PATHS[:2]))
    qtbot.waitUntil(lambda: grid.opacity(PATHS[0]) == 1.0)

    def bottom_left() -> tuple[int, int, int]:
        rect = grid.visualRect(grid.list_model.index(0))
        image = grid.viewport().grab().toImage()
        return rgb(image.pixelColor(rect.left() + 4, rect.bottom() - 4))

    assert bottom_left() == RED
    grid.set_needle("img0")
    assert bottom_left() != RED  # the caption's strip
    grid.set_needle("")
    assert bottom_left() == RED


def test_the_match_is_found_ignoring_case() -> None:
    assert split_match("Dragon_Sky.jpg", "SKY") == ("Dragon_", "Sky", ".jpg")
    assert split_match("Straße.png", "STRASSE") == ("Straße.png", "", "")
    assert split_match("Straße.png", "ße") == ("Stra", "ße", ".png")


@pytest.mark.usefixtures("qapp")  # for the font metrics
def test_a_long_name_keeps_the_match_and_cuts_around_it() -> None:
    font = QFont()
    plain, bold = QFontMetrics(font), QFontMetrics(theme.scaled_font(font, bold=True))
    parts = split_match("a" * 60 + "_dragon_" + "b" * 60 + ".jpg", "dragon")
    before, match, after = fit_match(parts, plain, bold, 200)
    assert match == "dragon"
    assert before.startswith("…") and after.endswith("…")
    width = sum(m.horizontalAdvance(t) for m, t in zip((plain, bold, plain), parts))
    assert fit_match(parts, plain, bold, width + 1) == parts  # it fits: whole
    tiny = fit_match(parts, plain, bold, bold.horizontalAdvance("drag"))
    assert tiny[0] == tiny[2] == "" and tiny[1].endswith("…")


def test_the_fast_scroll_bar_jumps_and_drags(qtbot: QtBot, grids: Grids) -> None:
    grid = grids(decoder_of())
    fast = FastScroll(grid.verticalScrollBar())
    qtbot.addWidget(fast)
    fast.resize(16, 500)
    fast.show()
    assert fast.handle() is None  # nothing to scroll yet
    grid.set_entries(entries_of(PATHS))
    scroll = grid.verticalScrollBar()
    qtbot.waitUntil(lambda: scroll.maximum() > 0)  # laid out
    handle = fast.handle()
    assert handle is not None and handle.top() == 0
    QTest.mousePress(
        fast, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(8, 499)
    )
    assert scroll.value() == scroll.maximum()  # the track pressed: there at once
    QTest.mouseMove(fast, QPoint(8, 0))
    assert scroll.value() == 0  # dragged back up
    QTest.mouseRelease(
        fast, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, QPoint(8, 0)
    )
    assert fast.dragging()  # only the left button ends it
    QTest.mouseRelease(
        fast, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(8, 0)
    )
    assert not fast.dragging()
    qtbot.waitUntil(lambda: loaded(grid, shown(grid)))
