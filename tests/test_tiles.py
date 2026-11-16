"""
The grid's thumbnail pipeline: newest request first, requests for tiles
off screen dropped before decoding, a memory budget that never drops a
tile on screen, and the pre-generation that fills the disk cache (and
prunes it) while the grid has nothing to decode.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from wallpaper_py.app.ui.tiles import (
    TILE,
    PassResult,
    Pregenerator,
    Sources,
    TileLoader,
    tile_pixels,
)
from wallpaper_py.core.thumbnail_cache import ThumbnailCache

from .fakes import BROKEN_IMAGE, GONE_IMAGE, FakeTileDecoder
from .generate_test_images import generate_library

A, B, C, D = (Path(f"/p/{n}.jpg") for n in "abcd")
GONE, BROKEN = GONE_IMAGE, BROKEN_IMAGE


def decoder_of(*hold: Path) -> FakeTileDecoder:
    """Thumbnails at a display scale of 2."""
    return FakeTileDecoder(tile_pixels(2.0), *hold)


class Loaders:
    """Makes loaders, and closes them after the test."""

    def __init__(self) -> None:
        self.made: list[TileLoader] = []

    def __call__(
        self, decoder: FakeTileDecoder, threads: int = 2, budget: int = 2**30
    ) -> TileLoader:
        loader = TileLoader(decoder, ratio=2.0, threads=threads, budget=budget)
        self.made.append(loader)
        return loader


@pytest.fixture(name="loaders")
def fixture_loaders() -> Iterator[Loaders]:
    loaders = Loaders()
    yield loaders
    for loader in loaders.made:
        loader.close()


def wait_loaded(qtbot: QtBot, loader: TileLoader, *paths: Path) -> None:
    qtbot.waitUntil(lambda: all(loader.get(p) is not None for p in paths))


# -- the tile loader ----------------------------------------------------------------


def test_newest_request_first(qtbot: QtBot, loaders: Loaders) -> None:
    decoder = decoder_of(A)
    loader = loaders(decoder, threads=1)
    loader.set_wanted([A, B, C, D])
    loader.request(A)
    qtbot.waitUntil(lambda: decoder.started == [A])  # the one thread waits on A
    for path in (B, C, D):
        loader.request(path)
    decoder.release()
    wait_loaded(qtbot, loader, A, B, C, D)
    assert decoder.started == [A, D, C, B]


def test_a_tile_scrolled_away_is_dropped_before_decoding(
    qtbot: QtBot, loaders: Loaders
) -> None:
    decoder = decoder_of(A)
    loader = loaders(decoder, threads=1)
    loader.request(A)
    qtbot.waitUntil(lambda: decoder.started == [A])
    loader.request(B)
    loader.request(C)
    loader.set_wanted([A, C])  # B scrolled out of view
    assert loader.pending() == {A, C}
    decoder.release()
    wait_loaded(qtbot, loader, A, C)
    assert B not in decoder.started and loader.get(B) is None
    assert not loader.pending()
    loader.request(B)  # back on screen: asked again
    wait_loaded(qtbot, loader, B)


def test_tiles_arrive_on_the_ui_thread_as_pixmaps(
    qtbot: QtBot, loaders: Loaders
) -> None:
    decoder = decoder_of()
    loader = loaders(decoder)
    arrived: list[tuple[Path, int]] = []
    loader.loaded.connect(lambda p: arrived.append((p, threading.get_ident())))
    for path in (A, GONE, BROKEN):
        loader.request(path)
    wait_loaded(qtbot, loader, A, GONE, BROKEN)
    assert {t for _, t in arrived} == {threading.get_ident()}
    assert threading.get_ident() not in decoder.threads
    tile = loader.get(A)
    assert tile is not None and tile.pixmap is not None
    assert tile.pixmap.devicePixelRatio() == 2.0
    assert (tile.pixmap.width(), tile.pixmap.height()) == tile_pixels(2.0)
    gone, broken = loader.get(GONE), loader.get(BROKEN)
    assert gone is not None and gone.pixmap is None and gone.missing
    assert broken is not None and broken.pixmap is None and not broken.missing
    assert broken.error and "Cannot load" in broken.error


def test_a_loaded_tile_is_not_asked_for_again(qtbot: QtBot, loaders: Loaders) -> None:
    decoder = decoder_of()
    loader = loaders(decoder)
    loader.request(A)
    loader.request(A)  # painted twice before it arrived
    wait_loaded(qtbot, loader, A)
    loader.request(A)
    assert decoder.started == [A]


def test_the_memory_budget_keeps_the_tiles_on_screen(
    qtbot: QtBot, loaders: Loaders
) -> None:
    one_tile = tile_pixels(2.0)[0] * tile_pixels(2.0)[1] * 4
    loader = loaders(decoder_of(), budget=2 * one_tile)
    loader.set_wanted([A, B, C])
    for path in (A, B, C):
        loader.request(path)
    wait_loaded(qtbot, loader, A, B, C)  # over budget, but all on screen
    assert loader.memory_used() == 3 * one_tile
    loader.set_wanted([D])  # scrolled on
    loader.request(D)
    wait_loaded(qtbot, loader, D)
    assert loader.memory_used() <= 2 * one_tile
    assert loader.get(D) is not None and loader.get(A) is None  # oldest first


def test_idle_while_nothing_waits_to_be_decoded(qtbot: QtBot, loaders: Loaders) -> None:
    decoder = decoder_of(A)
    loader = loaders(decoder, threads=1)
    assert loader.idle.is_set()
    loader.request(A)
    assert not loader.idle.is_set()
    decoder.release()
    wait_loaded(qtbot, loader, A)
    assert loader.idle.is_set()


def test_closed_it_takes_no_requests(qtbot: QtBot, loaders: Loaders) -> None:
    decoder = decoder_of(A)
    loader = loaders(decoder, threads=1)
    loader.request(A)
    qtbot.waitUntil(lambda: decoder.started == [A])
    loader.request(B)
    decoder.release()
    loader.close()  # waits for A's decode; B is dropped
    loader.request(C)
    qtbot.wait(50)
    assert decoder.started == [A] and loader.get(A) is None


# -- pre-generation ------------------------------------------------------------------


class Pregenerators:
    """Makes pre-generators over one cache, and closes them after the test."""

    def __init__(self, tmp_path: Path) -> None:
        self.cache = ThumbnailCache(tmp_path / "thumbnails", tile_pixels(1.0))
        self.idle = threading.Event()
        self.idle.set()
        self.made: list[Pregenerator] = []
        self.results: list[PassResult] = []

    def __call__(self) -> Pregenerator:
        pregenerator = Pregenerator(self.cache, self.idle)
        pregenerator.pass_done.connect(self.results.append)
        self.made.append(pregenerator)
        return pregenerator

    def files(self) -> set[Path]:
        return set(self.cache.directory.rglob("*.jpg"))


@pytest.fixture(name="pregenerators")
def fixture_pregenerators(tmp_path: Path) -> Iterator[Pregenerators]:
    pregenerators = Pregenerators(tmp_path)
    yield pregenerators
    for pregenerator in pregenerators.made:
        pregenerator.close()


def test_a_pass_makes_every_thumbnail(
    qtbot: QtBot, pregenerators: Pregenerators, tmp_path: Path
) -> None:
    konachan = generate_library(tmp_path / "konachan", 6)
    fantasy = generate_library(tmp_path / "fantasy", 3)
    elsewhere = generate_library(tmp_path / "elsewhere", 2)
    favourites = tmp_path / "favourites.txt"
    lines = [*elsewhere, konachan[0], tmp_path / "gone.jpg"]
    favourites.write_text("".join(f"{p}\n" for p in lines), encoding="utf-8")
    pregenerator = pregenerators()
    sources = Sources((tmp_path / "konachan", tmp_path / "fantasy"), (favourites,))
    pregenerator.start(sources)
    qtbot.waitUntil(lambda: bool(pregenerators.results))
    cache = pregenerators.cache
    made = {cache.file_for(p) for p in konachan + fantasy + elsewhere}
    assert pregenerators.files() == made
    # Each image once (konachan's first is also a favourite); the gone one
    # is listed but has no image.
    assert pregenerators.results == [PassResult(12, 0, True)]
    assert not pregenerator.running()
    pregenerator.start(sources)  # the same again: no new pass
    qtbot.wait(100)
    assert len(pregenerators.results) == 1


def test_a_pass_prunes_what_no_image_names(
    qtbot: QtBot, pregenerators: Pregenerators, tmp_path: Path
) -> None:
    walls = generate_library(tmp_path / "walls", 3)
    old_size = ThumbnailCache(pregenerators.cache.directory, tile_pixels(1.5))
    stale = old_size.ensure(walls[0])  # before the pass started
    assert stale is not None and stale.exists()
    pregenerators().start(Sources((tmp_path / "walls",), ()))
    qtbot.waitUntil(lambda: bool(pregenerators.results))
    assert pregenerators.results == [PassResult(3, 1, True)]
    assert not stale.exists()
    cache = pregenerators.cache
    assert pregenerators.files() == {cache.file_for(p) for p in walls}


def test_no_pruning_unless_every_source_was_read(
    qtbot: QtBot, pregenerators: Pregenerators, tmp_path: Path
) -> None:
    walls = generate_library(tmp_path / "walls", 2)
    other = generate_library(tmp_path / "other", 1)
    kept = pregenerators.cache.ensure(other[0])  # of a folder not mounted now
    assert kept is not None
    os.utime(kept, (0, 0))
    for image in other:
        image.unlink()
    (tmp_path / "other").rmdir()
    sources = Sources((tmp_path / "walls", tmp_path / "other"), ())
    pregenerators().start(sources)
    qtbot.waitUntil(lambda: bool(pregenerators.results))
    assert pregenerators.results == [PassResult(len(walls), 0, False)]
    assert kept.exists()


def test_a_pass_waits_while_the_grid_decodes(
    qtbot: QtBot, pregenerators: Pregenerators, tmp_path: Path
) -> None:
    generate_library(tmp_path / "walls", 2)
    pregenerators.idle.clear()  # the grid has tiles to decode
    pregenerator = pregenerators()
    pregenerator.start(Sources((tmp_path / "walls",), ()))
    qtbot.wait(300)
    assert not pregenerators.files() and pregenerator.running()
    pregenerators.idle.set()
    qtbot.waitUntil(lambda: bool(pregenerators.results))
    assert len(pregenerators.files()) == 2


def test_new_sources_replace_the_pass(
    qtbot: QtBot, pregenerators: Pregenerators, tmp_path: Path
) -> None:
    generate_library(tmp_path / "first", 2)
    second = generate_library(tmp_path / "second", 2)
    pregenerators.idle.clear()  # holds the first pass at its first image
    pregenerator = pregenerators()
    pregenerator.start(Sources((tmp_path / "first",), ()))
    pregenerator.start(Sources((tmp_path / "second",), ()))
    assert pregenerator.sources() == Sources((tmp_path / "second",), ())
    pregenerators.idle.set()
    qtbot.waitUntil(lambda: bool(pregenerators.results))
    qtbot.waitUntil(lambda: not pregenerator.running())
    # Only the second pass finished; the first stopped before it made any.
    assert pregenerators.results == [PassResult(2, 0, True)]
    cache = pregenerators.cache
    assert pregenerators.files() == {cache.file_for(p) for p in second}


def test_closing_stops_a_pass(
    qtbot: QtBot, pregenerators: Pregenerators, tmp_path: Path
) -> None:
    generate_library(tmp_path / "walls", 2)
    pregenerators.idle.clear()
    pregenerator = pregenerators()
    pregenerator.start(Sources((tmp_path / "walls",), ()))
    qtbot.waitUntil(pregenerator.running)
    pregenerator.close()
    assert not pregenerator.running() and not pregenerators.results
    pregenerator.start(Sources((tmp_path / "other",), ()))  # closed: ignored
    assert not pregenerator.running()


def test_tile_sizes_follow_the_display_scale() -> None:
    assert tile_pixels(1.0) == (TILE.width(), TILE.height())
    assert tile_pixels(1.5) == (288, 162)
