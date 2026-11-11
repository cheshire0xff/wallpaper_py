import os
from pathlib import Path

from wallpaper_py.core.marks import MarkFile


def test_missing_file_is_an_empty_list(tmp_path: Path) -> None:
    marks = MarkFile(tmp_path / "fav.txt")
    assert not marks.paths()
    assert not (tmp_path / "fav.txt").exists()


def test_add_appends_and_creates_the_file(tmp_path: Path) -> None:
    marks = MarkFile(tmp_path / "lists" / "fav.txt")
    assert marks.add(tmp_path / "a.jpg")
    assert marks.add(tmp_path / "b.jpg")
    assert not marks.add(tmp_path / "a.jpg")
    assert (tmp_path / "lists" / "fav.txt").read_text(encoding="utf-8") == (
        f"{tmp_path / 'a.jpg'}\n{tmp_path / 'b.jpg'}\n"
    )
    assert tmp_path / "b.jpg" in marks
    assert not list(tmp_path.glob("lists/*.tmp"))


def test_hand_written_lines_survive_changes(tmp_path: Path) -> None:
    file = tmp_path / "fav.txt"
    file.write_text(
        f'# my favourites\n\n"{tmp_path / "a.jpg"}"\n{tmp_path / "b.jpg"}',
        encoding="utf-8-sig",  # Notepad's BOM
    )
    marks = MarkFile(file)
    assert marks.paths() == [tmp_path / "a.jpg", tmp_path / "b.jpg"]
    assert not marks.add(tmp_path / "a.jpg")  # quotes from "Copy as path"
    marks.add(tmp_path / "c.jpg")
    marks.discard(tmp_path / "b.jpg")
    lines = file.read_text(encoding="utf-8").splitlines()
    assert lines == [
        "# my favourites",
        "",
        f'"{tmp_path / "a.jpg"}"',
        str(tmp_path / "c.jpg"),
    ]


def test_external_changes_are_reloaded(tmp_path: Path) -> None:
    file = tmp_path / "to-remove.txt"
    marks = MarkFile(file)
    marks.add(tmp_path / "a.jpg")
    assert len(marks.paths()) == 1
    file.write_text("", encoding="utf-8")  # the library script cleared it
    os.utime(file, ns=(1, 1))  # a different mtime even on coarse clocks
    assert not marks.paths()


def test_change_rereads_the_file_first(tmp_path: Path) -> None:
    """An add never writes back a stale copy over someone else's edit."""
    file = tmp_path / "fav.txt"
    marks = MarkFile(file)
    marks.add(tmp_path / "a.jpg")
    marks.paths()  # cached
    file.write_text(f"{tmp_path / 'x.jpg'}\n", encoding="utf-8")
    marks.add(tmp_path / "b.jpg")
    assert marks.paths() == [tmp_path / "x.jpg", tmp_path / "b.jpg"]


def test_missing_images_are_never_dropped(tmp_path: Path) -> None:
    marks = MarkFile(tmp_path / "fav.txt")
    marks.add(tmp_path / "gone.jpg")  # never existed
    marks.add(tmp_path / "other.jpg")
    assert marks.paths() == [tmp_path / "gone.jpg", tmp_path / "other.jpg"]


def test_discard_of_unlisted_path_leaves_the_file_alone(tmp_path: Path) -> None:
    file = tmp_path / "fav.txt"
    marks = MarkFile(file)
    marks.add(tmp_path / "a.jpg")
    before = file.stat().st_mtime_ns
    assert not marks.discard(tmp_path / "b.jpg")
    assert file.stat().st_mtime_ns == before
