import os
import sys
import time
from pathlib import Path

import pytest

from wallpaper_py.core.library import (
    Library,
    natural_key,
    path_key,
    scan,
    scan_files,
)

from .fakes import FakeClock, make_images


def test_natural_order_ignores_case() -> None:
    names = ["img10.jpg", "IMG2.jpg", "img1.jpg", "b.jpg", "A.jpg"]
    assert sorted(names, key=natural_key) == [
        "A.jpg",
        "b.jpg",
        "img1.jpg",
        "IMG2.jpg",
        "img10.jpg",
    ]


def test_path_key_is_absolute_and_normalised(tmp_path: Path) -> None:
    assert path_key(tmp_path / "a" / ".." / "b.jpg") == path_key(tmp_path / "b.jpg")


def test_scan_is_recursive_filters_suffixes_and_skips_hidden_dirs(
    tmp_path: Path,
) -> None:
    make_images(
        tmp_path,
        "b10.png",
        "b2.JPG",
        "sub/a.webp",
        "notes.txt",
        ".thumbs/x.jpg",
    )
    assert [p.relative_to(tmp_path).as_posix() for p in scan(tmp_path)] == [
        "b2.JPG",
        "b10.png",
        "sub/a.webp",
    ]


def test_scan_of_missing_directory_is_empty(tmp_path: Path) -> None:
    assert not scan(tmp_path / "not-mounted")
    assert not scan_files(tmp_path / "not-mounted")


MONTH = 30 * 24 * 3600


def test_scan_files_says_when_each_image_was_added(tmp_path: Path) -> None:
    """
    The creation time where there is one (Windows): a copy keeps its old
    mtime, but came into the library now. Else (Linux) the mtime.
    """
    images = make_images(tmp_path, "b10.png", "b2.jpg", "sub/a.webp", ".x/c.jpg")
    old = time.time() - MONTH
    for image in images:
        os.utime(image, (old, old))
    files = scan_files(tmp_path)
    assert [f.path for f in files] == scan(tmp_path)  # the same images, in order
    for file in files:
        if sys.platform == "win32":
            assert file.added > old + MONTH / 2
        else:
            assert file.added == pytest.approx(old)


def test_symlinks_files_are_kept_with_their_own_time_directories_not_followed(
    tmp_path: Path,
) -> None:
    target = make_images(tmp_path / "elsewhere", "t.jpg")[0]
    old = time.time() - MONTH
    os.utime(target, (old, old))
    library = tmp_path / "library"
    library.mkdir()
    try:
        (library / "link.jpg").symlink_to(target)
        (library / "linked-dir").symlink_to(target.parent, target_is_directory=True)
    except OSError as err:  # Windows without developer mode
        pytest.skip(f"cannot make symlinks here: {err}")
    files = scan_files(library)
    assert [f.path for f in files] == [library / "link.jpg"]
    assert files[0].added > old + MONTH / 2  # linked in now


def test_library_caches_until_max_age(tmp_path: Path) -> None:
    clock = FakeClock()
    library = Library(clock, max_age=60)
    make_images(tmp_path, "a.jpg")
    assert len(library.images(tmp_path)) == 1
    make_images(tmp_path, "b.jpg")
    assert len(library.images(tmp_path)) == 1
    assert len(library.images(tmp_path, refresh=True)) == 2
    make_images(tmp_path, "c.jpg")
    clock.advance(61)
    assert len(library.images(tmp_path)) == 3


def test_age_of_the_cached_scan(tmp_path: Path) -> None:
    clock = FakeClock()
    library = Library(clock, max_age=300)
    assert library.age(tmp_path) == 0  # never scanned
    library.images(tmp_path)
    clock.advance(120)
    library.images(tmp_path)  # cached
    assert library.age(tmp_path) == 120
    library.images(tmp_path, refresh=True)
    assert library.age(tmp_path) == 0
