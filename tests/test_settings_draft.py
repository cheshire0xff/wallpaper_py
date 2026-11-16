"""The Settings page's edit without widgets: app/ui/settings_draft.py."""

import os
from dataclasses import replace
from pathlib import Path

import pytest

from wallpaper_py.app.ui.settings_draft import (
    BLUR_STEPS,
    DARKEN_STEPS,
    Draft,
    PlaylistDraft,
    blur_look,
    blur_step,
    change_summary,
    current_images,
    darken_look,
    darken_step,
    entry_names,
    interval_problem,
    monitor_entries,
    playlist_help,
    problems,
    short_id,
)
from wallpaper_py.core.model import (
    DEFAULT_BLUR,
    DEFAULT_DARKEN,
    MAX_BLUR,
    MAX_DARKEN,
    MIN_BLUR,
    Background,
    ImageMode,
    Look,
    MonitorId,
)
from wallpaper_py.core.settings import (
    MANUAL,
    MonitorSettings,
    Order,
    SAME_LISTS,
    PlaylistSettings,
    Settings,
)
from wallpaper_py.protocol import DesktopMonitor, Geometry, PlaylistView


def monitor(name: str, key: str, x: int | None) -> DesktopMonitor:
    geometry = None if x is None else Geometry(x=x, y=0, width=1920, height=1080)
    return DesktopMonitor(id=MonitorId(key), name=name, geometry=geometry)


A = MonitorId("a")
B = MonitorId("b")


@pytest.fixture(name="saved")
def fixture_saved(tmp_path: Path) -> Settings:
    (tmp_path / "walls").mkdir()
    walls = PlaylistSettings(
        id="w", name="walls", directory=tmp_path / "walls", interval=90.0
    )
    return Settings(
        playlists=(walls,),
        monitors=(MonitorSettings(id=A, playlist="w"),),
        pause_when_idle=None,
        favourites_file=tmp_path / "favs.txt",
    )


def test_a_draft_reads_and_gives_back_the_settings(saved: Settings) -> None:
    draft = Draft.of(saved)
    assert draft.playlists[0].interval == "1m30s"
    assert draft.pause_when_idle is None
    assert draft.settings() == saved
    manual = Draft.of(
        saved.model_copy(
            update={
                "playlists": (saved.playlists[0].model_copy(update={"interval": None}),)
            }
        )
    )
    assert (
        manual.playlists[0].manual and manual.settings().playlists[0].interval is None
    )


def test_rows_with_only_the_defaults_are_left_out(saved: Settings) -> None:
    draft = Draft.of(saved).with_monitor(MonitorSettings(id=B, playlist="w"))
    assert draft.settings().monitors[-1] == MonitorSettings(id=B, playlist="w")
    back = draft.with_monitor(MonitorSettings(id=B))
    assert back.has_row(B) and back.settings() == saved


def test_deleting_a_playlist_frees_its_monitors(saved: Settings) -> None:
    draft = Draft.of(saved).without_playlist("w")
    assert not draft.playlists and draft.monitor(A).playlist is None
    assert not draft.settings().monitors


def test_new_playlists_get_free_names(saved: Settings) -> None:
    draft = Draft.of(saved)
    first = draft.new_playlist()
    assert (first.name, first.directory, first.interval) == ("New playlist", "", "30m")
    second = draft.with_playlist(first).new_playlist()
    assert second.name == "New playlist 2" and second.id != first.id


def test_problems(saved: Settings, tmp_path: Path) -> None:
    draft = Draft.of(saved)
    assert not problems(draft, saved, tmp_path)
    other = PlaylistDraft(id="x", name=" WALLS ", directory="relative", interval="5s")
    gone = PlaylistDraft(id="y", name="", directory=str(tmp_path / "gone"))
    edited = draft.with_playlist(other).with_playlist(gone)
    edited = Draft(
        playlists=edited.playlists,
        monitors=edited.monitors,
        pause_when_idle="0s",
        favourites_file=str(tmp_path),
        to_remove_file="list.txt",
    )
    assert problems(edited, saved, tmp_path) == {
        ("x", "name"): "Another playlist is called walls",
        ("x", "directory"): "Use the folder's full path",
        ("x", "interval"): "At least 10s",
        ("y", "name"): "Give it a name",
        ("y", "directory"): f"No such folder: {tmp_path / 'gone'}",
        (None, "pause_when_idle"): "Must be more than 0; untick it to never pause",
        (None, "favourites_file"): "This is a folder; choose a file",
        (None, "to_remove_file"): "Use the file's full path",
    }
    # A saved folder that is gone (an unplugged drive) is no problem.
    (tmp_path / "walls").rmdir()
    assert not problems(draft, saved, tmp_path)


def test_the_two_lists_must_be_two_files(saved: Settings, tmp_path: Path) -> None:
    draft = Draft.of(saved)  # favourites: favs.txt; to-remove: the default
    same = replace(draft, to_remove_file=str(tmp_path / "FAVS.txt"))
    clash = {(None, "to_remove_file"): SAME_LISTS}
    if os.path.normcase("A") == "a":  # Windows: case doesn't matter
        assert problems(same, saved, tmp_path) == clash
    same = replace(draft, to_remove_file=str(tmp_path / "x" / ".." / "favs.txt"))
    assert problems(same, saved, tmp_path) == clash
    # The default to-remove file, typed as the favourites.
    default = replace(draft, favourites_file=str(tmp_path / "to-remove.txt"))
    assert problems(default, saved, tmp_path) == clash


def test_intervals() -> None:
    assert interval_problem(MANUAL) is None
    assert interval_problem("1h30m") is None
    assert interval_problem("soon") == "Use a duration like 45s, 30m, 1h30m or 1d"


def test_monitor_entries_and_their_names() -> None:
    left = monitor("PLX2783H", "p1", -1920)
    right = monitor("PLX2783H", "p2", 1920)
    middle = monitor("AW3225QF", "aw", 0)
    off = monitor("ZeroMOD", "z", None)
    away = MonitorId(r"\\?\DISPLAY#DELA1B2#5&1&0&UID9#{guid}")
    draft = Draft(monitors=(MonitorSettings(id=away, look=Look(ImageMode.FIT)),))
    entries = monitor_entries([right, off, middle, left], draft)
    assert [(e.label, e.state) for e in entries] == [
        ("PLX2783H · 1920×1080 · 1 of 2", "drawn"),
        ("AW3225QF", "drawn"),
        ("PLX2783H · 1920×1080 · 2 of 2", "drawn"),
        ("ZeroMOD · switched off", "off"),
        ("DELA1B2 · not connected", "away"),
    ]
    assert entry_names(entries)[off.id] == "ZeroMOD"
    assert short_id(MonitorId("HDMI-A-1")) == "HDMI-A-1"  # not a Windows id


def test_the_playlist_rows_description() -> None:
    a, b = monitor("A", "a", 0), monitor("B", "b", 1920)
    draft = Draft(
        playlists=(PlaylistDraft(id="w", name="walls", directory="/w"),),
        monitors=(MonitorSettings(id=A, playlist="w"),),
    )
    entries = monitor_entries([a, b], draft)
    assert playlist_help(draft, B, entries) == "Nothing rotates on this monitor"
    assert playlist_help(draft, A, entries) == (
        "Pick the playlist of another monitor to mirror it"
    )
    both = draft.with_monitor(MonitorSettings(id=B, playlist="w"))
    assert playlist_help(both, A, entries) == (
        "Mirrors B: the same image, changed together"
    )


def test_change_summary(saved: Settings, tmp_path: Path) -> None:
    names = {A: "A", B: "B"}
    walls = saved.playlists[0]
    art = PlaylistSettings(id="n", name="art", directory=tmp_path, order=Order.SORTED)
    edited = saved.model_copy(
        update={
            "playlists": (walls.model_copy(update={"name": "wallz"}), art),
            "monitors": (
                MonitorSettings(id=A, playlist="w", look=Look(ImageMode.FIT)),
                MonitorSettings(id=B, playlist="n"),
            ),
        }
    )
    assert change_summary(saved, edited, names, {A, B}) == (
        "mode of A, B shows art, name of walls, 1 more"
    )
    edges = Look(ImageMode.FIT, Background.EDGES)
    looks = saved.model_copy(
        update={"monitors": (MonitorSettings(id=A, playlist="w", look=edges),)}
    )
    assert change_summary(saved, looks, names, {A, B}) == ("mode of A, background of A")
    blurred = Look(blur=0.01, darken=0.5)
    looks = saved.model_copy(
        update={"monitors": (MonitorSettings(id=A, playlist="w", look=blurred),)}
    )
    assert change_summary(saved, looks, names, {A, B}) == ("blur of A, darkening of A")
    gone = saved.model_copy(update={"playlists": (), "monitors": ()})
    # A not listed now, its row gone: forgotten; listed: it shows nothing.
    assert change_summary(saved, gone, names, set()) == (
        "forget A, delete walls and its history"
    )
    assert change_summary(saved, gone, names, {A}) == (
        "A shows no playlist, delete walls and its history"
    )


def test_current_images_keep_a_folder_written_another_way(
    saved: Settings, tmp_path: Path
) -> None:
    image = tmp_path / "walls" / "one.jpg"
    shown = [
        PlaylistView(
            id="w",
            name="walls",
            directory=image.parent,
            monitors=(),
            current=image,
            favourite=False,
        )
    ]
    draft = Draft.of(saved)
    assert current_images(saved, draft, shown) == {"w": image}
    same = replace(draft.playlists[0], directory=f" {tmp_path / 'walls' / '.'} ")
    retyped = draft.with_playlist(same)
    assert current_images(saved, retyped, shown) == {"w": image}
    moved = draft.with_playlist(replace(same, directory=str(tmp_path / "other")))
    assert not current_images(saved, moved, shown)  # unknown until saved
    # Found by id: a name edited in the draft (or renamed meanwhile) changes
    # nothing.
    renamed = [shown[0].model_copy(update={"name": "other name"})]
    assert current_images(saved, draft, renamed) == {"w": image}


def test_the_sliders_steps() -> None:
    """
    The defaults on a step; every step a value the settings take; a value
    between steps or outside them shows at the nearest one.
    """
    look = Look()
    assert (blur_step(look), darken_step(look)) == (0, 5)
    assert blur_look(look, 0) == look and darken_look(look, 5) == look
    blurs = [
        blur_look(look, step).blur for step in range(BLUR_STEPS[0], BLUR_STEPS[1] + 1)
    ]
    assert MIN_BLUR <= blurs[0] < 1.1 * MIN_BLUR and blurs[-1] == MAX_BLUR
    assert all(b / a == pytest.approx(2**0.25) for a, b in zip(blurs, blurs[1:]))
    darkens = [
        darken_look(look, step).darken
        for step in range(DARKEN_STEPS[0], DARKEN_STEPS[1] + 1)
    ]
    assert darkens[0] == 0 and darkens[-1] == MAX_DARKEN
    for step in range(BLUR_STEPS[0], BLUR_STEPS[1] + 1):
        assert blur_step(blur_look(look, step)) == step
    for step in range(DARKEN_STEPS[0], DARKEN_STEPS[1] + 1):
        assert darken_step(darken_look(look, step)) == step
    assert blur_step(replace(look, blur=DEFAULT_BLUR * 1.05)) == 0
    assert blur_step(replace(look, blur=1.0)) == BLUR_STEPS[1]
    assert darken_step(replace(look, darken=DEFAULT_DARKEN + 0.01)) == 5
    assert darken_step(replace(look, darken=-1.0)) == 0
