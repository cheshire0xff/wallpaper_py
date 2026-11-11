"""
Editing config.toml in place (core.config_edit): a round trip keeps the file
byte for byte, each edit touches only its own lines, comments survive, and
what is written is what the core reads back.
"""

from __future__ import annotations

import difflib
from dataclasses import replace
from pathlib import Path

import pytest

from wallpaper_py.core.config import Order, loads_config
from wallpaper_py.core.config_edit import (
    DIRECTORY,
    FAVOURITES,
    IDLE,
    INTERVAL,
    MONITORS,
    NAME,
    ConfigConflictError,
    ConfigEditError,
    PlaylistSettings,
    Settings,
    assign_monitor,
    choose_selector,
    new_playlist,
    owner,
    read_file,
    read_settings,
    remove_selector,
    render,
    resolve,
    validate,
    write_file,
)
from wallpaper_py.core.model import ImageMode
from wallpaper_py.protocol import DesktopMonitor

EXAMPLE = (Path(__file__).parents[1] / "docs" / "config.example.toml").read_text(
    encoding="utf-8"
)
# The example, commented the way people do: on almost every line, and a
# comment block right above a table's header.
COMMENTED = EXAMPLE.replace(
    "[playlists.fantasy]",
    "# The strip under the main monitor.\n"
    "# Changed by hand only.\n"
    "[playlists.fantasy]   # ZeroMOD",
).replace('monitors = ["ZeroMOD"]', 'monitors = ["ZeroMOD"]              # the strip')

AW = DesktopMonitor(id=r"\\?\DISPLAY#DELA246#1", name="AW3225QF", geometry=None)
PLX = DesktopMonitor(id=r"\\?\DISPLAY#PLX#1", name="PLX2783H", geometry=None)
ZERO = DesktopMonitor(id=r"\\?\DISPLAY#AUS27B1#1", name="ZeroMOD", geometry=None)
# Two of a model: the same name, told apart by id.
TWIN_A = DesktopMonitor(id=r"\\?\DISPLAY#DEL#A", name="U2720Q", geometry=None)
TWIN_B = DesktopMonitor(id=r"\\?\DISPLAY#DEL#B", name="U2720Q", geometry=None)
LISTED = (AW, PLX, ZERO)


def diff(old: str, new: str) -> tuple[list[str], list[str]]:
    """(removed lines, added lines)."""
    removed, added = [], []
    for line in difflib.ndiff(old.splitlines(), new.splitlines()):
        if line.startswith("- "):
            removed.append(line[2:])
        elif line.startswith("+ "):
            added.append(line[2:])
    return removed, added


def edit_playlist(text: str, playlist_name: str, **changes: object) -> str:
    base = read_settings(text)
    playlist = next(p for p in base.playlists if p.name == playlist_name)
    edited = base.replace_playlist(replace(playlist, **changes))  # type: ignore[arg-type]
    return render(text, base, edited)


def edit_general(text: str, **changes: object) -> str:
    base = read_settings(text)
    return render(text, base, replace(base, **changes))  # type: ignore[arg-type]


def load(text: str, tmp_path: Path) -> object:
    return loads_config(text, tmp_path / "config.toml", tmp_path / "data")


# -- reading and the round trip ------------------------------------------------------


def test_reads_the_values_as_written() -> None:
    settings = read_settings(COMMENTED)
    konachan, fantasy = settings.playlists
    assert konachan == PlaylistSettings(
        uid=0,
        origin="konachan",
        name="konachan",
        directory="D:\\pictures\\KONACHAN",
        monitors=("AW3225QF", "PLX2783H"),
        interval="30m",
        order=Order.SHUFFLE,
        mode=ImageMode.FILL,
    )
    assert (fantasy.order, fantasy.interval, fantasy.manual) == (
        Order.SORTED,
        "manual",
        True,
    )
    assert settings.pause_when_locked and settings.pause_when_idle == "5m"
    assert settings.favourites_file == settings.to_remove_file == ""


def test_defaults_when_the_file_leaves_keys_out() -> None:
    settings = read_settings(
        "[playlists.a]\ndirectory = 'x'\nmonitors = 'M'\ninterval = 'Manual'\n"
    )
    assert settings.pause_when_locked and settings.pause_when_idle == "5m"
    [playlist] = settings.playlists
    assert (playlist.monitors, playlist.interval) == (("M",), "manual")
    assert (playlist.order, playlist.mode) == (Order.SHUFFLE, None)
    assert read_settings("pause_when_idle = false\n").pause_when_idle is None


@pytest.mark.parametrize("text", [EXAMPLE, COMMENTED, COMMENTED.replace("\n", "\r\n")])
def test_no_change_gives_the_identical_file(text: str) -> None:
    base = read_settings(text)
    assert render(text, base, base) == text
    assert render(text, base, replace(base)) == text  # equal, not the same object


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("[playlists.a\n", "Not valid TOML"),
        ("[playlists]\na = {directory = 'x'}\n", "only those can be edited"),
        ("[playlists.a]\norder = 'random'\n", "order must be one of"),
        ("[playlists.a]\nmonitors = [1]\n", "monitors must be a list"),
        ("pause_when_idle = 5\n", "pause_when_idle must be"),
    ],
)
def test_what_cannot_be_edited_is_explained(text: str, problem: str) -> None:
    with pytest.raises(ConfigEditError, match=problem):
        read_settings(text)


# -- each edit touches only its own lines --------------------------------------------


def test_interval_keeps_its_comment_in_its_column(tmp_path: Path) -> None:
    new = edit_playlist(COMMENTED, "konachan", interval="1h30m")
    assert diff(COMMENTED, new) == (
        ['interval = "30m"                     # active time since the last change'],
        ['interval = "1h30m"                   # active time since the last change'],
    )
    load(new, tmp_path)


def test_directory_stays_a_literal_string(tmp_path: Path) -> None:
    new = edit_playlist(COMMENTED, "konachan", directory="E:\\it's\\here")
    # A quote can't go into a literal string: then a basic one, escaped.
    [old], [line] = diff(COMMENTED, new)
    assert line.startswith('directory = "E:\\\\it\'s\\\\here" ')
    assert line.index("#") == old.index("#")  # the comment keeps its column
    new = edit_playlist(COMMENTED, "konachan", directory="E:\\walls")
    [old], [line] = diff(COMMENTED, new)
    assert old == "directory = 'D:\\pictures\\KONACHAN'   # scanned recursively"
    assert line == "directory = 'E:\\walls'" + " " * 15 + "# scanned recursively"
    assert line.index("#") == old.index("#")
    config = load(new, tmp_path)
    assert config.playlists[0].directory == Path("E:\\walls")  # type: ignore[attr-defined]


def test_order_and_mode() -> None:
    new = edit_playlist(COMMENTED, "fantasy", order=Order.SHUFFLE, mode=ImageMode.FIT)
    assert diff(COMMENTED, new) == (
        [
            'order = "sorted"                     # natural order: 2 before 10',
            'mode = "fill"',
        ],
        [
            'order = "shuffle"                    # natural order: 2 before 10',
            'mode = "fit"',
        ],
    )


def test_as_is_removes_mode_and_back_adds_it_after_the_last_key() -> None:
    as_is = edit_playlist(COMMENTED, "konachan", mode=None)
    assert diff(COMMENTED, as_is) == (
        ['mode = "fill"                        # fill / fit / stretch; omit: as is'],
        [],
    )
    again = edit_playlist(as_is, "konachan", mode=ImageMode.STRETCH)
    # Under interval, not under the comment that belongs to the next table.
    lines = again.splitlines()
    index = lines.index('mode = "stretch"')
    assert lines[index - 1].startswith("interval = ")
    assert diff(as_is, again) == ([], ['mode = "stretch"'])


def test_monitors(tmp_path: Path) -> None:
    new = edit_playlist(COMMENTED, "fantasy", monitors=("ZeroMOD", AW.id))
    assert diff(COMMENTED, new) == (
        ['monitors = ["ZeroMOD"]              # the strip'],
        ["monitors = [\"ZeroMOD\", '\\\\?\\DISPLAY#DELA246#1'] # the strip"],
    )
    config = load(new.replace('"AW3225QF", ', ""), tmp_path)
    assert config.playlists[1].monitors == ("ZeroMOD", AW.id)  # type: ignore[attr-defined]


def test_rename_changes_only_the_header(tmp_path: Path) -> None:
    new = edit_playlist(COMMENTED, "fantasy", name="strip")
    assert diff(COMMENTED, new) == (
        ["[playlists.fantasy]   # ZeroMOD"],
        ["[playlists.strip]   # ZeroMOD"],
    )
    config = load(new, tmp_path)
    assert [p.name for p in config.playlists] == ["konachan", "strip"]  # type: ignore[attr-defined]


def test_a_quoted_name_in_the_file_is_kept_and_editable() -> None:
    text = COMMENTED.replace("[playlists.fantasy]", '[playlists."my strip"]')
    settings = read_settings(text)
    assert settings.playlists[1].name == "my strip"
    assert not validate(settings, Path("."), LISTED).get((1, NAME))  # as written
    new = edit_playlist(text, "my strip", interval="1h")
    assert diff(text, new)[1] == ['interval = "1h"']
    renamed = edit_playlist(text, "my strip", name="strip")
    assert diff(text, renamed)[1] == ["[playlists.strip]   # ZeroMOD"]


def test_delete_keeps_the_comments_above_the_next_table(tmp_path: Path) -> None:
    base = read_settings(COMMENTED)
    edited = replace(base, playlists=base.playlists[1:])
    new = render(COMMENTED, base, edited)
    removed, added = diff(COMMENTED, new)
    assert removed == [
        "[playlists.konachan]",
        "directory = 'D:\\pictures\\KONACHAN'   # scanned recursively",
        'monitors = ["AW3225QF", "PLX2783H"]',
        'order = "shuffle"                    # every image once before any repeats',
        'interval = "30m"                     # active time since the last change',
        'mode = "fill"                        # fill / fit / stretch; omit: as is',
    ]
    assert not added  # the blank line stays, between two comment blocks
    assert "starts it again.\n\n# The strip under" in new
    assert "# Changed by hand only.\n[playlists.fantasy]   # ZeroMOD" in new
    assert [p.name for p in load(new, tmp_path).playlists] == ["fantasy"]  # type: ignore[attr-defined]

    # The last table: its own trailing lines go with it.
    edited = replace(base, playlists=base.playlists[:1])
    new = render(COMMENTED, base, edited)
    assert "fantasy" not in new and "The strip under" not in new
    assert new.endswith("omit: as is\n\n")


def test_add_appends_a_table(tmp_path: Path) -> None:
    (tmp_path / "new walls").mkdir()
    base = read_settings(COMMENTED)
    added = replace(
        new_playlist(base, 7, monitors=(TWIN_A.id,)),
        directory=str(tmp_path / "new walls"),
    )
    edited = replace(base, playlists=(*base.playlists, added))
    new = render(COMMENTED, base, edited)
    assert new.startswith(COMMENTED)  # nothing above changed
    assert new[len(COMMENTED) :] == (
        "\n[playlists.playlist]\n"
        f"directory = '{tmp_path / 'new walls'}'\n"
        "monitors = ['\\\\?\\DISPLAY#DEL#A']\n"
        'order = "shuffle"\n'
        'interval = "30m"\n'
        'mode = "fill"\n'
    )
    config = load(new, tmp_path)
    assert config.playlists[2].monitors == (TWIN_A.id,)  # type: ignore[attr-defined]


def test_add_to_a_file_without_a_final_newline_or_playlists() -> None:
    base = read_settings("pause_when_locked = true")
    edited = replace(
        base, playlists=(replace(new_playlist(base, 0, ("M",)), directory="d"),)
    )
    new = render("pause_when_locked = true", base, edited)
    assert new.startswith("pause_when_locked = true\n\n[playlists.playlist]\n")


def test_rename_and_add_with_the_old_name(tmp_path: Path) -> None:
    base = read_settings(COMMENTED)
    konachan, fantasy = base.playlists
    edited = replace(
        base,
        playlists=(
            replace(konachan, name="anime"),
            fantasy,
            replace(
                new_playlist(base, 9, ("PLX2783H",)), name="konachan", directory="x"
            ),
        ),
    )
    edited = edited.replace_playlist(
        replace(konachan, name="anime", monitors=("AW3225QF",))
    )
    config = load(render(COMMENTED, base, edited), tmp_path)
    names = [p.name for p in config.playlists]  # type: ignore[attr-defined]
    assert names == ["anime", "fantasy", "konachan"]


def test_general_settings() -> None:
    new = edit_general(COMMENTED, pause_when_locked=False, pause_when_idle=None)
    assert diff(COMMENTED, new) == (
        ["pause_when_locked = true", 'pause_when_idle = "5m"'],
        ["pause_when_locked = false", "pause_when_idle = false"],
    )
    # A list file: added after the last top-level key, not after the comments.
    new = edit_general(COMMENTED, favourites_file="D:\\pictures\\favs.txt")
    assert diff(COMMENTED, new) == ([], ["favourites_file = 'D:\\pictures\\favs.txt'"])
    lines = new.splitlines()
    assert lines[lines.index("favourites_file = 'D:\\pictures\\favs.txt'") - 1] == (
        'pause_when_idle = "5m"'
    )
    # Cleared: the default again, so the key goes.
    assert edit_general(new, favourites_file="") == COMMENTED


def test_crlf_files_stay_crlf() -> None:
    text = COMMENTED.replace("\n", "\r\n")
    new = edit_playlist(text, "konachan", mode=None)
    new = edit_playlist(new, "konachan", mode=ImageMode.FIT)
    assert "\n" not in new.replace("\r\n", "")
    base = read_settings(text)
    added = replace(new_playlist(base, 5, ("M",)), directory="d")
    new = render(text, base, replace(base, playlists=(*base.playlists, added)))
    assert "\n" not in new.replace("\r\n", "")


# -- validation ----------------------------------------------------------------------


@pytest.fixture(name="walls")
def fixture_walls(tmp_path: Path) -> Settings:
    """Two valid playlists with existing directories (one relative)."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    return Settings(
        playlists=(
            PlaylistSettings(0, "a", "a", "a", ("AW3225QF",), "30m"),
            PlaylistSettings(1, "b", "b", str(tmp_path / "b"), ("ZeroMOD",), "manual"),
        )
    )


def check(settings: Settings, tmp_path: Path) -> dict[tuple[int | None, str], str]:
    return validate(settings, tmp_path, LISTED)


def test_valid_settings_have_no_problems(walls: Settings, tmp_path: Path) -> None:
    assert not check(walls, tmp_path)


@pytest.mark.parametrize(
    ("interval", "problem"),
    [("30", "Use a duration"), ("5s", "At least 10s"), ("", "Use a duration")],
)
def test_bad_interval(
    walls: Settings, tmp_path: Path, interval: str, problem: str
) -> None:
    edited = walls.replace_playlist(replace(walls.playlists[0], interval=interval))
    problems = check(edited, tmp_path)
    assert list(problems) == [(0, INTERVAL)]
    assert problems[(0, INTERVAL)].startswith(problem)


def test_missing_directory(walls: Settings, tmp_path: Path) -> None:
    edited = walls.replace_playlist(replace(walls.playlists[0], directory="gone"))
    assert check(edited, tmp_path) == {
        (0, DIRECTORY): f"No such folder: {tmp_path / 'gone'}"
    }
    # Already so in the file (e.g. a drive unplugged): not held against an
    # edit elsewhere; changed to another missing one, it is.
    unplugged = replace(edited, pause_when_locked=False)
    assert not validate(unplugged, tmp_path, LISTED, base=edited)
    other = edited.replace_playlist(replace(edited.playlists[0], directory="gone2"))
    assert (0, DIRECTORY) in validate(other, tmp_path, LISTED, base=edited)
    edited = walls.replace_playlist(replace(walls.playlists[0], directory=" "))
    assert check(edited, tmp_path) == {(0, DIRECTORY): "Choose a folder"}


def test_duplicate_monitor(walls: Settings, tmp_path: Path) -> None:
    # By name in one, by id in the other: still the same monitor.
    edited = walls.replace_playlist(replace(walls.playlists[1], monitors=(AW.id,)))
    assert check(edited, tmp_path) == {
        (1, MONITORS): "AW3225QF is also in a; a monitor follows one playlist"
    }
    # Not listed now: compared as written.
    both = Settings(
        playlists=(
            replace(walls.playlists[0], monitors=("Gone",)),
            replace(walls.playlists[1], monitors=("gone",)),
        )
    )
    assert (1, MONITORS) in check(both, tmp_path)


def test_no_monitor(walls: Settings, tmp_path: Path) -> None:
    edited = walls.replace_playlist(replace(walls.playlists[0], monitors=()))
    assert check(edited, tmp_path) == {
        (0, MONITORS): "No monitor: choose one, or delete the playlist"
    }


@pytest.mark.parametrize(
    ("name", "problem"),
    [
        ("my walls", "Use only letters, digits, - and _ (no spaces)"),
        ("a.b", "Use only letters, digits, - and _ (no spaces)"),
        ("", "Give it a name"),
        ("A", "Another playlist is called a"),
    ],
)
def test_bad_name(walls: Settings, tmp_path: Path, name: str, problem: str) -> None:
    edited = walls.replace_playlist(replace(walls.playlists[1], name=name))
    assert check(edited, tmp_path) == {(1, NAME): problem}


def test_general_problems(walls: Settings, tmp_path: Path) -> None:
    edited = replace(walls, pause_when_idle="soon", favourites_file=str(tmp_path))
    problems = check(edited, tmp_path)
    assert set(problems) == {(None, IDLE), (None, FAVOURITES)}
    assert check(replace(walls, playlists=()), tmp_path) == {
        (None, "playlists"): "Add at least one playlist"
    }


# -- monitors and selectors ----------------------------------------------------------


def test_selector_is_the_name_when_unique_else_the_id() -> None:
    listed = (AW, TWIN_A, TWIN_B)
    assert choose_selector(AW, listed) == "AW3225QF"
    assert choose_selector(TWIN_A, listed) == TWIN_A.id
    assert choose_selector(TWIN_A, (AW, TWIN_A)) == "U2720Q"


def test_resolve_by_id_or_unique_name() -> None:
    listed = (AW, TWIN_A, TWIN_B)
    assert resolve("aw3225qf", listed) is AW
    assert resolve(TWIN_B.id.lower(), listed) is TWIN_B
    assert resolve("U2720Q", listed) is None  # ambiguous
    assert resolve("AW", listed) is None  # no partial matches


def test_assign_moves_a_monitor_and_keeps_selectors_as_written() -> None:
    base = read_settings(COMMENTED)
    konachan, fantasy = base.playlists
    # Written by name in konachan; moving it to fantasy writes its name.
    moved = assign_monitor(base, PLX, LISTED, fantasy.uid)
    assert moved.playlists[0].monitors == ("AW3225QF",)
    assert moved.playlists[1].monitors == ("ZeroMOD", "PLX2783H")
    assert owner(moved, PLX, LISTED) == moved.playlists[1]
    # Already there: nothing changes, also not how it is written.
    by_id = base.replace_playlist(replace(konachan, monitors=(AW.id, "PLX2783H")))
    assert assign_monitor(by_id, AW, LISTED, konachan.uid) == by_id
    # No playlist.
    none = assign_monitor(base, ZERO, LISTED, None)
    assert none.playlists[1].monitors == ()
    assert owner(none, ZERO, LISTED) is None


def test_a_monitor_not_listed_now_stays_until_removed() -> None:
    base = read_settings(COMMENTED)
    listed = (AW, PLX)  # ZeroMOD switched off and not listed
    moved = assign_monitor(base, PLX, listed, base.playlists[1].uid)
    assert moved.playlists[1].monitors == ("ZeroMOD", "PLX2783H")
    removed = remove_selector(moved, base.playlists[1].uid, "ZeroMOD")
    assert removed.playlists[1].monitors == ("PLX2783H",)


def test_new_playlists_get_free_names() -> None:
    base = read_settings(COMMENTED)
    first = new_playlist(base, 10)
    assert (first.name, first.origin, first.interval) == ("playlist", None, "30m")
    taken = replace(base, playlists=(*base.playlists, first))
    assert new_playlist(taken, 11).name == "playlist-2"


# -- the file ------------------------------------------------------------------------


def test_write_refuses_a_file_changed_on_disk(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(COMMENTED.encode())
    read = read_file(path)
    path.write_bytes((COMMENTED + "# a hand edit\n").encode())
    with pytest.raises(ConfigConflictError, match="changed on disk"):
        write_file(path, "new", expected=read)
    assert read_file(path).endswith("# a hand edit\n")  # untouched


def test_write_replaces_atomically(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(COMMENTED.replace("\n", "\r\n").encode())
    read = read_file(path)
    assert "\r\n" in read  # line endings kept as they are
    write_file(path, read.replace("30m", "1h"), expected=read)
    assert (
        path.read_bytes()
        == COMMENTED.replace("\n", "\r\n").replace("30m", "1h").encode()
    )
    assert [p.name for p in tmp_path.iterdir()] == ["config.toml"]  # no temp file
