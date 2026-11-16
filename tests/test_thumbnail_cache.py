"""
The grid's thumbnails on disk: the image filling the tile (cropped to the
middle), named by the image's path, mtime and size, the tile size and the
style, made once, and pruned.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from PIL import Image

from wallpaper_py.core import thumbnail_cache
from wallpaper_py.core.imaging import load_filled
from wallpaper_py.core.model import ImageLoadError
from wallpaper_py.core.thumbnail_cache import ThumbnailCache

from .generate_test_images import generate_library

TILE = (192, 108)
RED, BLUE = (220, 30, 30), (30, 30, 220)


@pytest.fixture(name="cache")
def fixture_cache(tmp_path: Path) -> ThumbnailCache:
    return ThumbnailCache(tmp_path / "thumbnails", TILE)


def cached_files(cache: ThumbnailCache) -> list[Path]:
    return sorted(cache.directory.rglob(f"*{thumbnail_cache.SUFFIX}"))


# -- filling ---------------------------------------------------------------------


def rgb(image: Image.Image, xy: tuple[int, int]) -> tuple[int, int, int]:
    value = image.getpixel(xy)
    assert isinstance(value, tuple)
    red, green, blue = value[:3]
    return red, green, blue


def three_bands(size: tuple[int, int], vertical: bool) -> Image.Image:
    """Red, blue, red: three equal bands, side by side or (`vertical`) stacked."""
    image = Image.new("RGB", size, RED)
    width, height = size
    middle = (0, height // 3, width, 2 * height // 3)
    image.paste(
        BLUE, (width // 3, 0, 2 * width // 3, height) if not vertical else middle
    )
    return image


def test_an_image_fills_the_tile_cropped_to_the_middle(tmp_path: Path) -> None:
    wide = tmp_path / "wide.png"
    three_bands((6000, 1000), vertical=False).save(wide)  # wider than 16:9
    tall = tmp_path / "tall.png"
    three_bands((1080, 3000), vertical=True).save(tall)
    for source in (wide, tall):
        made = load_filled(source, TILE)
        assert made.size == TILE
        # Only the middle band is left, edge to edge.
        assert {made.getpixel(p) for p in [(0, 54), (191, 54), (96, 0), (96, 107)]} == {
            BLUE
        }


def test_a_small_image_is_enlarged_to_fill(tmp_path: Path) -> None:
    small = tmp_path / "small.png"
    Image.new("RGB", (100, 50), BLUE).save(small)
    made = load_filled(small, TILE)
    assert made.size == TILE and made.getpixel((96, 54)) == BLUE


def test_a_jpeg_is_decoded_small_but_still_sharp(tmp_path: Path) -> None:
    big = tmp_path / "big.jpg"
    three_bands((7680, 4320), vertical=False).save(big, quality=95)
    made = load_filled(big, TILE)
    assert made.size == TILE
    red, _, blue = rgb(made, (10, 54))
    assert red > 150 and blue < 100  # the side bands survive: a 16:9 image


def test_exif_turns_the_thumbnail_upright(tmp_path: Path) -> None:
    source = tmp_path / "turned.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # stored on its side: turned 90 degrees to show
    three_bands((1920, 1080), vertical=False).save(source, exif=exif, quality=95)
    made = load_filled(source, TILE)  # shown as portrait: horizontal bands
    assert made.size == TILE
    red, _, blue = rgb(made, (96, 54))
    assert blue > 150 and red < 100  # the middle band, across the tile


def test_a_broken_file_raises(tmp_path: Path) -> None:
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not a jpeg")
    with pytest.raises(ImageLoadError):
        load_filled(broken, TILE)


# -- the disk cache --------------------------------------------------------------


def test_made_once_then_read_from_disk(
    cache: ThumbnailCache, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (image,) = generate_library(tmp_path / "walls", 1, (1920, 1080))
    made = cache.thumbnail(image)
    assert made.size == TILE and made.mode == "RGB"
    assert cached_files(cache) == [cache.file_for(image)]

    def no_decoding(source: Path, box: tuple[int, int]) -> Image.Image:
        raise AssertionError(f"decoded {source} at {box} again")

    monkeypatch.setattr(thumbnail_cache, "load_filled", no_decoding)
    again = cache.thumbnail(image)
    assert again.size == TILE  # from the disk


def test_named_by_path_mtime_size_tile_size_and_style(
    cache: ThumbnailCache, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = generate_library(tmp_path / "walls", 2)
    name = cache.file_for(first)
    assert name == cache.file_for(first)  # stable
    assert name != cache.file_for(second)
    assert name != ThumbnailCache(cache.directory, (384, 216)).file_for(first)
    monkeypatch.setattr(thumbnail_cache, "STYLE", "fit")
    assert cache.file_for(first) != name  # made another way: another file
    monkeypatch.undo()
    stat = first.stat()
    os.utime(first, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert cache.file_for(first) != name  # edited: a new thumbnail


def test_a_thumbnail_that_doesnt_read_is_made_again(
    cache: ThumbnailCache, tmp_path: Path
) -> None:
    (image,) = generate_library(tmp_path / "walls", 1)
    file = cache.file_for(image)
    file.parent.mkdir(parents=True)
    file.write_bytes(b"half a jpeg")
    assert cache.thumbnail(image).size == TILE
    assert Image.open(file).size == TILE  # replaced


def test_missing_and_broken_images(cache: ThumbnailCache, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        cache.thumbnail(tmp_path / "gone.jpg")
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not a jpeg")
    with pytest.raises(ImageLoadError):
        cache.thumbnail(broken)
    assert not cached_files(cache)


def test_ensure_makes_what_is_missing(cache: ThumbnailCache, tmp_path: Path) -> None:
    image, other = generate_library(tmp_path / "walls", 2)
    file = cache.ensure(image)
    assert file == cache.file_for(image) and file.exists()
    written = file.stat().st_mtime_ns
    assert cache.ensure(image) == file
    assert file.stat().st_mtime_ns == written  # not made again
    other.write_bytes(b"broken now")
    assert cache.ensure(other) == cache.file_for(other)  # named, but no file
    assert not cache.file_for(other).exists()
    assert cache.ensure(tmp_path / "gone.jpg") is None


def test_prune_deletes_old_files_no_image_names(
    cache: ThumbnailCache, tmp_path: Path
) -> None:
    kept, edited, deleted = generate_library(tmp_path / "walls", 3)
    for image in (kept, edited, deleted):
        cache.ensure(image)
    stale = cache.file_for(edited)
    deleted_file = cache.file_for(deleted)
    deleted.unlink()
    stat = edited.stat()
    os.utime(edited, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    started = time.time()
    new = cache.ensure(edited)  # made during the pass: newer than it started
    assert new is not None and new != stale
    old = started - 60
    for file in (cache.file_for(kept), stale, deleted_file):
        os.utime(file, (old, old))

    assert cache.prune([cache.file_for(kept)], started) == 2
    assert cached_files(cache) == sorted([cache.file_for(kept), new])


def test_prune_without_a_cache_does_nothing(cache: ThumbnailCache) -> None:
    assert cache.prune([], time.time()) == 0
