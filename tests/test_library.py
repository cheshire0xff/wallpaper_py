from pathlib import Path

from wallpaper_py.core.library import Library, natural_key, path_key, scan

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
