import os
from pathlib import Path

import pytest

from wallpaper_py.core.config import ConfigError
from wallpaper_py.core.marks import MarkFile, MarkFileBusyError

from .fakes import BusyFile, settled


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
        encoding="utf-8",
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


def test_a_file_that_is_not_utf8_is_refused_and_left_alone(tmp_path: Path) -> None:
    file = tmp_path / "fav.txt"
    file.write_text(f"{tmp_path / 'a.jpg'}\n", encoding="utf-16")  # PowerShell's >
    settled(file)
    before = file.read_bytes()
    marks = MarkFile(file)
    with pytest.raises(ConfigError, match="not UTF-8"):
        marks.paths()
    with pytest.raises(ConfigError):
        marks.add(tmp_path / "b.jpg")
    with pytest.raises(ConfigError):
        marks.discard(tmp_path / "a.jpg")
    assert file.read_bytes() == before


def test_a_file_that_cannot_be_opened_keeps_the_last_list(tmp_path: Path) -> None:
    file = tmp_path / "to-remove.txt"
    marks = MarkFile(file)
    marks.add(tmp_path / "a.jpg")
    assert marks.paths() == [tmp_path / "a.jpg"]
    text = file.read_text(encoding="utf-8")

    file.unlink()
    file.mkdir()  # reading it fails with an OSError, like a locked file
    assert marks.paths() == [tmp_path / "a.jpg"]
    with pytest.raises(MarkFileBusyError) as error:
        marks.add(tmp_path / "b.jpg")
    # Not a lock that may pass (PermissionError on Windows): no guess.
    assert "open in another program" not in str(error.value) or isinstance(
        error.value.__cause__, PermissionError
    )

    file.rmdir()
    file.write_text(text + f"{tmp_path / 'c.jpg'}\n", encoding="utf-8")
    assert marks.paths() == [tmp_path / "a.jpg", tmp_path / "c.jpg"]


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


def test_a_byte_order_mark_is_refused(tmp_path: Path) -> None:
    file = tmp_path / "fav.txt"
    file.write_text(f"{tmp_path / 'a.jpg'}\n", encoding="utf-8-sig")  # Notepad's BOM
    settled(file)
    with pytest.raises(ConfigError, match="byte order mark"):
        MarkFile(file).paths()


def test_a_file_that_cannot_be_replaced_is_left_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = tmp_path / "fav.txt"
    marks = MarkFile(file)
    marks.add(tmp_path / "a.jpg")
    before = file.read_bytes()

    def locked(*_: object) -> None:
        raise PermissionError(32, "being used by another process")

    monkeypatch.setattr(os, "replace", locked)
    with pytest.raises(MarkFileBusyError, match="open in another program"):
        marks.add(tmp_path / "b.jpg")
    assert file.read_bytes() == before
    assert not file.with_name("fav.txt.tmp").exists()
    assert marks.paths() == [tmp_path / "a.jpg"]


def test_a_file_never_read_is_unknown_not_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    file = tmp_path / "to-remove.txt"
    file.write_text(f"{tmp_path / 'a.jpg'}\n", encoding="utf-8")
    busy = BusyFile(file, monkeypatch)
    marks = MarkFile(file)
    for _ in range(2):
        with pytest.raises(MarkFileBusyError, match="unknown until it can be read"):
            marks.paths()
        with pytest.raises(MarkFileBusyError):
            assert tmp_path / "a.jpg" not in marks
    assert len(caplog.records) == 1  # logged once while it stays busy

    busy.busy = False
    assert marks.paths() == [tmp_path / "a.jpg"]
    busy.busy = True
    os.utime(file, ns=(1, 1))  # changed: read again, which fails
    assert marks.paths() == [tmp_path / "a.jpg"]  # known now: the last list


def test_a_file_that_cannot_be_looked_at_is_busy_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E.g. a folder the user can't enter: stat() fails, not only the read."""
    file = tmp_path / "to-remove.txt"
    file.write_text(f"{tmp_path / 'a.jpg'}\n", encoding="utf-8")
    marks = MarkFile(file)
    assert marks.paths() == [tmp_path / "a.jpg"]
    stat = Path.stat

    def denied(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path == file:
            raise PermissionError(13, "Permission denied", str(path))
        return stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", denied)
    assert marks.paths() == [tmp_path / "a.jpg"]  # the last list
    with pytest.raises(MarkFileBusyError, match="unknown until it can be read"):
        MarkFile(file).paths()


def test_a_missing_file_is_known_to_be_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = tmp_path / "to-remove.txt"
    BusyFile(file, monkeypatch)
    assert not MarkFile(file).paths()


def test_a_file_that_just_changed_and_wont_decode_may_be_half_written(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A script appending in place, caught between the two bytes of an "é"."""
    file = tmp_path / "to-remove.txt"
    marks = MarkFile(file)
    marks.add(tmp_path / "a.jpg")
    assert marks.paths() == [tmp_path / "a.jpg"]
    whole = f"{tmp_path / 'a.jpg'}\n{tmp_path / 'é.jpg'}\n".encode()
    file.write_bytes(whole[: whole.index("é".encode()) + 1])  # "é" cut in two
    assert marks.paths() == [tmp_path / "a.jpg"]  # busy: the last list
    assert "maybe still being written" in caplog.text
    with pytest.raises(MarkFileBusyError):
        marks.add(tmp_path / "b.jpg")  # nothing written over it
    with pytest.raises(MarkFileBusyError, match="unknown until it can be read"):
        MarkFile(file).paths()

    settled(file)  # it stays like that: broken
    with pytest.raises(ConfigError, match="not UTF-8"):
        marks.paths()
    file.write_bytes(whole)  # the write finished
    assert marks.paths() == [tmp_path / "a.jpg", tmp_path / "é.jpg"]


def test_a_busy_file_after_a_write_is_the_list_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not the list before it: an image just listed for removal stays listed."""
    file = tmp_path / "to-remove.txt"
    marks = MarkFile(file)
    marks.add(tmp_path / "a.jpg")
    assert marks.paths() == [tmp_path / "a.jpg"]
    marks.add(tmp_path / "b.jpg")
    BusyFile(file, monkeypatch)  # a virus scan, before the next read
    assert marks.paths() == [tmp_path / "a.jpg", tmp_path / "b.jpg"]
