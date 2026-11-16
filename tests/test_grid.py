"""
The Library's image grid, offscreen: only the tiles on screen are asked
for, a scroll drops the requests left behind, tiles fade in, and with
generated images the thumbnails land on disk.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtGui import QColor
from pytestqt.qtbot import QtBot

from wallpaper_py.app.ui import theme
from wallpaper_py.app.ui.grid import FolderScan, ImageGrid
from wallpaper_py.app.ui.tiles import TileLoader, tile_pixels
from wallpaper_py.core.thumbnail_cache import ThumbnailCache

from .fakes import GONE_IMAGE, TILE_RED, FakeTileDecoder
from .generate_test_images import generate_library

PATHS = [Path(f"/p/img{i}.jpg") for i in range(1000)]
GONE = GONE_IMAGE
RED = TILE_RED


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
    grid.set_images(PATHS)
    on_screen = shown(grid)
    assert 10 <= len(on_screen) <= 40
    qtbot.waitUntil(lambda: loaded(grid, on_screen))
    assert set(decoder.started) == set(on_screen)


def test_a_scroll_drops_the_requests_left_behind(qtbot: QtBot, grids: Grids) -> None:
    decoder = decoder_of(hold_all=True)
    grid = grids(decoder, threads=1)
    grid.set_images(PATHS)
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
    grid.set_images(PATHS[:3])
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
    grid.set_images([GONE, PATHS[0]])
    qtbot.waitUntil(lambda: loaded(grid, [GONE, PATHS[0]]))
    tile = grid.loader.get(GONE)
    assert tile is not None and tile.missing and tile.pixmap is None
    assert rgb(colour_at_tile(grid, 0)) != RED  # its name, on the panel
    assert grid.list_model.data(grid.list_model.index(0)) == "gone.jpg"


def test_the_same_images_keep_the_scroll(qtbot: QtBot, grids: Grids) -> None:
    grid = grids(decoder_of())
    grid.set_images(PATHS)
    grid.scrollToBottom()
    bottom = grid.verticalScrollBar().value()
    assert bottom > 0
    grid.set_images(list(PATHS))  # listed again, nothing new
    assert grid.verticalScrollBar().value() == bottom
    grid.set_images(PATHS[::-1])  # another order: from the top
    assert grid.verticalScrollBar().value() == 0
    qtbot.waitUntil(lambda: loaded(grid, shown(grid)))


def test_a_hidden_grid_wants_nothing(qtbot: QtBot, grids: Grids) -> None:
    decoder = decoder_of(hold_all=True)
    grid = grids(decoder, threads=1)
    grid.set_images(PATHS)
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
    grid.set_images(images)
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


def test_a_folder_is_listed_off_the_ui_thread(qtbot: QtBot, tmp_path: Path) -> None:
    images = generate_library(tmp_path / "walls", 12)
    scan = FolderScan()
    try:
        with qtbot.waitSignal(scan.scanned) as signal:
            scan.scan(tmp_path / "walls")
        assert signal.args == [tmp_path / "walls", images]  # natural order
        received: list[Path] = []
        scan.scanned.connect(lambda folder, _: received.append(folder))
        scan.scan(tmp_path / "walls")
        scan.scan(tmp_path / "other")  # asked for meanwhile: only this one
        qtbot.waitUntil(lambda: scan.asked() is None)
        qtbot.wait(50)
        assert received == [tmp_path / "other"]
    finally:
        scan.close()
