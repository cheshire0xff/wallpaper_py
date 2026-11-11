"""
Thumbnails: read from the files off the UI thread, cached by path, and a
result for an image that is no longer wanted is dropped.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
from pytestqt.qtbot import QtBot

from wallpaper_py.app.ui.thumbnails import (
    Decoded,
    ImageInfo,
    ThumbnailLoader,
    read_thumbnail,
)

BOX = QSize(480, 270)
A, B, C = Path("/p/a.jpg"), Path("/p/b.jpg"), Path("/p/c.jpg")


class Decoder:
    """Records where it ran; images in `blocked` wait for `release()`."""

    def __init__(self, *blocked: Path) -> None:
        self.blocked = set(blocked)
        self._gate = threading.Event()
        self.started: list[Path] = []
        self.threads: set[int] = set()
        self._lock = threading.Lock()

    def __call__(self, path: Path, box: QSize) -> Decoded:
        with self._lock:
            self.started.append(path)
            self.threads.add(threading.get_ident())
        if path in self.blocked:
            assert self._gate.wait(10)
        image = QImage(box.width() // 2, box.height() // 2, QImage.Format.Format_RGB32)
        image.fill(0x804020)
        return Decoded(ImageInfo(3840, 2160, 1234), image)

    def release(self) -> None:
        self._gate.set()


class Loaded:
    """
    `loaded` signals, and whether each arrived on the UI thread (a plain
    callable runs in the emitting thread, so this sees where it was emitted).
    """

    def __init__(self, loader: ThumbnailLoader) -> None:
        self.paths: list[Path] = []
        self.on_ui_thread: list[bool] = []
        self._ui_thread = threading.get_ident()
        loader.loaded.connect(self.add)

    def add(self, path: Path) -> None:
        self.paths.append(path)
        self.on_ui_thread.append(threading.get_ident() == self._ui_thread)


@pytest.fixture(name="decoder")
def fixture_decoder() -> Decoder:
    return Decoder()


def make_loader(decoder: Decoder, threads: int = 2) -> ThumbnailLoader:
    return ThumbnailLoader(BOX, decoder, threads=threads)


@pytest.fixture(name="loaders")
def fixture_loaders() -> Iterator[list[ThumbnailLoader]]:
    loaders: list[ThumbnailLoader] = []
    yield loaders
    for loader in loaders:
        loader.close()


def test_decodes_off_the_ui_thread_and_caches(
    qtbot: QtBot, decoder: Decoder, loaders: list[ThumbnailLoader]
) -> None:
    loader = make_loader(decoder)
    loaders.append(loader)
    loaded = Loaded(loader)
    loader.want([A])
    assert loader.get(A) is None and loader.pending() == {A}
    qtbot.waitUntil(lambda: loaded.paths == [A])
    assert threading.get_ident() not in decoder.threads  # never the UI thread
    assert loaded.on_ui_thread == [True]  # delivered back on it
    thumbnail = loader.get(A)
    assert thumbnail is not None and thumbnail.pixmap is not None
    assert thumbnail.info == ImageInfo(3840, 2160, 1234)
    assert not loader.pending()
    loader.want([A])  # cached: no second decode
    assert decoder.started == [A] and not loader.pending()


def test_a_stale_result_is_dropped(
    qtbot: QtBot, loaders: list[ThumbnailLoader]
) -> None:
    decoder = Decoder(A)
    loader = make_loader(decoder)
    loaders.append(loader)
    loaded = Loaded(loader)
    loader.want([A])
    qtbot.waitUntil(lambda: decoder.started == [A])  # A is being decoded
    loader.want([B])  # the card moved on to B meanwhile
    qtbot.waitUntil(lambda: loaded.paths == [B])
    decoder.release()  # A finishes now, too late
    loader.close()  # waits for it, and runs nothing after
    qtbot.wait(50)
    assert loaded.paths == [B]
    assert loader.get(A) is None and loader.get(B) is not None


def test_queued_work_for_an_image_no_longer_wanted_is_cancelled(
    qtbot: QtBot, loaders: list[ThumbnailLoader]
) -> None:
    decoder = Decoder(A)
    loader = make_loader(decoder, threads=1)
    loaders.append(loader)
    loaded = Loaded(loader)
    loader.want([A])
    qtbot.waitUntil(lambda: decoder.started == [A])  # the one thread is busy
    loader.want([A, B])  # B waits in the queue
    assert loader.pending() == {A, B}
    loader.want([A, C])  # B is no longer wanted: off the queue
    decoder.release()
    qtbot.waitUntil(lambda: set(loaded.paths) == {A, C})
    assert decoder.started == [A, C]  # B was never decoded
    assert loader.get(B) is None


def test_wanting_others_after_a_job_ran_but_before_its_result_arrived(
    qtbot: QtBot, loaders: list[ThumbnailLoader]
) -> None:
    # The pool deletes a job once it has run; its result is still queued
    # for this thread. Wanting other images now must not touch that job.
    decoder = Decoder(A)
    loader = make_loader(decoder)
    loaders.append(loader)
    loaded = Loaded(loader)
    loader.want([A])
    qtbot.waitUntil(lambda: decoder.started == [A])  # held in the decoder
    decoder.release()
    loader._pool.waitForDone()  # pylint: disable=protected-access
    loader.want([B])  # A ran, but no events ran since: its result waits
    qtbot.waitUntil(lambda: loaded.paths == [B])
    assert loader.get(A) is None


def test_the_cache_is_bounded(qtbot: QtBot, decoder: Decoder) -> None:
    loader = ThumbnailLoader(BOX, decoder, cache_entries=2)
    try:
        loaded = Loaded(loader)
        for path in (A, B, C):
            loader.want([path])
            qtbot.waitUntil(lambda p=path: p in loaded.paths)  # type: ignore[misc]
        assert loader.get(A) is None
        assert loader.get(B) is not None and loader.get(C) is not None
    finally:
        loader.close()


def test_read_thumbnail_reads_the_header_and_decodes_scaled(tmp_path: Path) -> None:
    path = tmp_path / "big.jpg"
    Image.new("RGB", (3840, 2160), (200, 90, 40)).save(path)
    result = read_thumbnail(path, BOX)
    assert result.error is None
    assert result.info == ImageInfo(3840, 2160, path.stat().st_size)
    assert (result.image.width(), result.image.height()) == (480, 270)

    small = tmp_path / "small.png"
    Image.new("RGB", (100, 150)).save(small)
    result = read_thumbnail(small, BOX)
    assert result.info == ImageInfo(100, 150, small.stat().st_size)
    assert (result.image.width(), result.image.height()) == (100, 150)  # no upscale


def test_read_thumbnail_follows_the_exif_orientation(tmp_path: Path) -> None:
    path = tmp_path / "turned.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # stored landscape, shown rotated 90 degrees
    Image.new("RGB", (1600, 900)).save(path, exif=exif)
    result = read_thumbnail(path, BOX)
    assert result.info is not None
    assert (result.info.width, result.info.height) == (900, 1600)
    # Fitted into the box as shown (portrait in a landscape box).
    assert result.image.height() == 270 and result.image.width() in (151, 152)


def test_read_thumbnail_errors(tmp_path: Path) -> None:
    missing = read_thumbnail(tmp_path / "missing.jpg", BOX)
    assert missing.info is None and missing.image.isNull() and missing.error
    fake = tmp_path / "fake.jpg"
    fake.write_text("not an image", encoding="utf-8")
    broken = read_thumbnail(fake, BOX)
    assert broken.image.isNull() and broken.error
