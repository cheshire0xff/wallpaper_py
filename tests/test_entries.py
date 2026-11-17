"""
What the Library's grid tabs show (entries.py, no Qt): narrowing by name,
the sorts, entries inside a playlist's folder, and the readers' results and
errors.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from wallpaper_py.app.ui.entries import (
    Entry,
    Sort,
    arrange,
    in_folder,
    read_folder,
    read_history,
    read_list,
    upcoming_entries,
)
from wallpaper_py.core.store import Store

from .fakes import add_playlists
from .generate_test_images import generate_library

ROOT = Path("/pictures").absolute()


def names(entries: list[Entry]) -> list[str]:
    return [e.path.name for e in entries]


ENTRIES = [
    Entry(ROOT / "b" / "img10.jpg", 1.0),
    Entry(ROOT / "a" / "IMG2.jpg", 3.0),
    Entry(ROOT / "c" / "img1.png", 2.0),
]


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("name_az", ["img1.png", "IMG2.jpg", "img10.jpg"]),  # natural, any case
        ("name_za", ["img10.jpg", "IMG2.jpg", "img1.png"]),
        ("newest", ["IMG2.jpg", "img1.png", "img10.jpg"]),
        ("oldest", ["img10.jpg", "img1.png", "IMG2.jpg"]),
        (None, ["img10.jpg", "IMG2.jpg", "img1.png"]),  # Upcoming: as given
    ],
)
def test_sorts(sort: Sort | None, expected: list[str]) -> None:
    assert names(arrange(ENTRIES, sort)) == expected


def test_narrowed_by_a_part_of_the_name_in_any_case() -> None:
    assert names(arrange(ENTRIES, "name_az", " img1")) == ["img1.png", "img10.jpg"]
    assert names(arrange(ENTRIES, "name_az", "Img2")) == ["IMG2.jpg"]
    assert not arrange(ENTRIES, "name_az", "pictures")  # the name, not the folder


def test_in_folder_means_inside_it_or_a_folder_in_it() -> None:
    inside = Entry(ROOT / "a" / "deep" / "x.jpg", 0)
    beside = Entry(ROOT / "ab" / "y.jpg", 0)  # a name that only starts the same
    assert in_folder([*ENTRIES, inside, beside], ROOT / "a") == [ENTRIES[1], inside]
    if os.name == "nt":  # spelled as typed in a list: the case doesn't matter
        assert in_folder([Entry(ROOT / "A" / "x.jpg", 0)], ROOT / "a")


def test_a_folder_is_read_with_when_each_image_came(tmp_path: Path) -> None:
    images = generate_library(tmp_path / "walls", 3)
    read = read_folder(tmp_path / "walls")
    assert [e.path for e in read.entries] == images
    assert all(e.order > 0 for e in read.entries)
    assert read_folder(tmp_path / "not there") == read_folder(tmp_path / "walls" / "x")


def test_a_list_is_read_in_line_order(tmp_path: Path) -> None:
    file = tmp_path / "favourites.txt"
    file.write_text(f"{ROOT / 'z.jpg'}\n# a note\n{ROOT / 'a.jpg'}\n", encoding="utf-8")
    read = read_list(file)
    assert names(list(read.entries)) == ["z.jpg", "a.jpg"]
    assert names(arrange(read.entries, "newest")) == ["a.jpg", "z.jpg"]  # added last
    assert not read_list(tmp_path / "none.txt").entries


def test_a_list_with_a_bom_says_so(tmp_path: Path) -> None:
    file = tmp_path / "favourites.txt"
    file.write_bytes(b"\xef\xbb\xbf" + str(ROOT / "a.jpg").encode())
    read = read_list(file)
    assert not read.entries and read.error is not None and "BOM" in read.error


def test_history_is_read_newest_first_with_when(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    with add_playlists(Store(database), "1") as store:
        store.append_history("1", ROOT / "a.jpg", 100.0)
        store.append_history("1", ROOT / "b.jpg", 200.0)
        store.append_history("1", ROOT / "a.jpg", 300.0)  # repeats kept
    read = read_history(database, "1")
    assert [(e.path.name, e.shown_at) for e in read.entries] == [
        ("a.jpg", 300.0),
        ("b.jpg", 200.0),
        ("a.jpg", 100.0),
    ]
    assert names(arrange(read.entries, "oldest")) == ["a.jpg", "b.jpg", "a.jpg"]


def test_history_of_another_schema_is_an_error(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    with add_playlists(Store(database), "1"):
        pass
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 1")
    read = read_history(database, "1")
    assert read.error is not None and "schema" in read.error


def test_upcoming_keeps_its_order() -> None:
    paths = [ROOT / "c.jpg", ROOT / "a.jpg"]
    read = upcoming_entries(paths, 2)
    assert [e.path for e in arrange(read.entries, None)] == paths
    assert not read.more
    read = upcoming_entries(paths, 1)  # asked one more than shown
    assert [e.path for e in read.entries] == paths[:1] and read.more
