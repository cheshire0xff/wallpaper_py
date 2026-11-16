"""core.settings: the Settings model, its checks and warnings, durations."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from wallpaper_py.core.model import Background, ImageMode, Look, MonitorId
from wallpaper_py.core.settings import (
    NO_PLAYLISTS,
    MonitorSettings,
    PlaylistSettings,
    Settings,
    SettingsError,
    Problem,
    check_folders,
    check_settings,
    settings_problems,
    format_duration,
    parse_duration,
)


def playlist(tmp_path: Path, **overrides: Any) -> PlaylistSettings:
    values: dict[str, Any] = {
        "id": "1",
        "name": "walls",
        "directory": tmp_path,
        "interval": 60.0,
    }
    return PlaylistSettings(**{**values, **overrides})


def settings(*playlists: PlaylistSettings, **values: Any) -> Settings:
    return Settings(playlists=playlists, **values)


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("45s", 45), ("30m", 1800), ("1h30m", 5400), ("2d", 172800), ("1H 5M", 3900)],
)
def test_parse_duration(text: str, seconds: float) -> None:
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "30", "m", "5 minutes", "-5m", "1m1h"])
def test_parse_duration_rejects_garbage(text: str) -> None:
    with pytest.raises(ValueError, match="Invalid duration"):
        parse_duration(text)


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0, "0s"), (59.6, "1m"), (90, "1m30s"), (3600, "1h"), (5430, "1h30m30s")],
)
def test_format_duration(seconds: float, text: str) -> None:
    assert format_duration(seconds) == text
    assert parse_duration(text) == round(seconds)


def test_defaults() -> None:
    default = Settings()
    assert not default.playlists and not default.monitors
    assert default.pause_when_locked and default.pause_when_idle == 300
    data = Path("data")
    assert default.list_files(data) == (
        data / "favourites.txt",
        data / "to-remove.txt",
    )
    check_settings(default)


def test_members_and_looks_go_by_monitor_id(tmp_path: Path) -> None:
    a, b = MonitorId("Mon-A"), MonitorId("mon-b")
    fit = Look(ImageMode.FIT)
    value = settings(
        playlist(tmp_path),
        monitors=(
            MonitorSettings(id=b, playlist="1", look=fit),
            MonitorSettings(id=a, playlist="1"),
            MonitorSettings(id=MonitorId("c")),
        ),
    )
    assert value.members("1") == (b, a)
    assert value.look(MonitorId("MON-B")) == fit
    assert value.look(MonitorId("unknown")) == Look()
    assert value.playlist(" WALLS ") is value.playlists[0]


def test_read_strictly_from_json() -> None:
    """The window sends them; a typo or a wrong type is refused."""
    json = '{"playlists": [], "monitors": [{"id": "m", "look": {"mode": "fit"}}]}'
    assert Settings.model_validate_json(json).look(MonitorId("M")).mode is (
        ImageMode.FIT
    )
    for bad in (
        '{"pause_when_locked": 1}',
        '{"monitors": [{"id": "m", "look": {"mode": "fit", "x": 1}}]}',
        '{"playlist": []}',
        '{"pause_when_idle": "Infinity"}',
        # Not 1.0 and 300.0, as lax pydantic would read them.
        '{"pause_when_idle": true}',
        '{"pause_when_idle": "300"}',
        '{"playlists": [{"id": "1", "name": "a", "directory": "/a", '
        '"interval": true}]}',
        '{"playlists": [{"id": "1", "name": "a", "directory": "/a", '
        '"interval": "600"}]}',
    ):
        with pytest.raises(ValidationError):
            Settings.model_validate_json(bad)
    # A background is #rrggbb (lower case, as the window sends it) or a kind.
    for background in ('"#ABCDEF"', '"#abc"', '"black"', '"Edges"', "0", "true"):
        look = f'{{"background": {background}}}'
        with pytest.raises(ValidationError):
            Settings.model_validate_json(
                f'{{"monitors": [{{"id": "m", "look": {look}}}]}}'
            )
    read = Settings.model_validate_json(
        '{"monitors": [{"id": "a", "look": {"mode": "fit", "background": "edges"}},'
        ' {"id": "b", "look": {"background": "#0a1b2c"}}]}'
    )
    assert read.look(MonitorId("a")) == Look(ImageMode.FIT, Background.EDGES)
    assert read.look(MonitorId("b")).background == "#0a1b2c"
    # Blur and darkening: numbers, not `true` or text; whole ones too.
    for value in ("true", '"0.01"', '"NaN"'):
        for field in ("blur", "darken"):
            with pytest.raises(ValidationError):
                Settings.model_validate_json(
                    f'{{"monitors": [{{"id": "m", "look": {{"{field}": {value}}}}}]}}'
                )
    blurred = Settings.model_validate_json(
        '{"monitors": [{"id": "m", "look": {"blur": 0.01, "darken": 0}}]}'
    ).look(MonitorId("m"))
    assert (blurred.blur, blurred.darken) == (0.01, 0.0)
    # Whole numbers are seconds too.
    whole = Settings.model_validate_json('{"pause_when_idle": 300}')
    assert whole.pause_when_idle == 300.0


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"name": " "}, "a playlist, name: Give it a name"),
        ({"name": "walls "}, "walls, name: Can't start or end with spaces"),
        ({"id": ""}, "walls, id: Needs an id no other playlist has"),
        ({"directory": Path("relative")}, "walls, folder: Use the folder's full path"),
        ({"interval": 9.0}, "walls, interval: At least 10s"),
    ],
)
def test_a_playlist_value_that_cant_be_used(
    tmp_path: Path, change: dict[str, Any], message: str
) -> None:
    with pytest.raises(SettingsError) as raised:
        check_settings(settings(playlist(tmp_path, **change)))
    assert str(raised.value) == message


def test_names_and_ids_are_unique(tmp_path: Path) -> None:
    walls = playlist(tmp_path)
    with pytest.raises(
        SettingsError, match="WALLS, name: Another playlist is called walls"
    ):
        check_settings(settings(walls, playlist(tmp_path, id="2", name="WALLS")))
    with pytest.raises(
        SettingsError, match="other, id: Needs an id no other playlist has"
    ):
        check_settings(settings(walls, playlist(tmp_path, name="other")))
    check_settings(settings(walls, playlist(tmp_path, id="2", name="other")))


def test_every_problem_is_found_with_its_place(tmp_path: Path) -> None:
    """The Settings page shows each next to its field; the daemon raises the first."""
    walls = playlist(tmp_path, interval=5.0)
    twin = playlist(tmp_path, id="2", name="Walls", directory=Path("relative"))
    found = settings_problems(
        settings(
            walls,
            twin,
            monitors=(MonitorSettings(id=MonitorId("m"), playlist="9"),),
            pause_when_idle=0.0,
        )
    )
    assert found == [
        Problem("1", "interval", "At least 10s"),
        Problem("2", "name", "Another playlist is called walls"),
        Problem("2", "directory", "Use the folder's full path"),
        Problem(
            None, "monitors", "Monitor m is on playlist id '9', which doesn't exist"
        ),
        Problem(
            None, "pause_when_idle", "Must be more than 0; untick it to never pause"
        ),
    ]


def test_monitors_are_listed_once_on_a_playlist_that_exists(tmp_path: Path) -> None:
    def with_monitors(*monitors: MonitorSettings) -> Settings:
        return settings(playlist(tmp_path), monitors=monitors)

    with pytest.raises(SettingsError, match="listed twice"):
        check_settings(
            with_monitors(
                MonitorSettings(id=MonitorId("M")), MonitorSettings(id=MonitorId("m"))
            )
        )
    with pytest.raises(SettingsError, match="doesn't exist"):
        check_settings(with_monitors(MonitorSettings(id=MonitorId("m"), playlist="9")))
    with pytest.raises(SettingsError, match="id is empty"):
        check_settings(with_monitors(MonitorSettings(id=MonitorId(" "))))


@pytest.mark.parametrize(
    ("look", "message"),
    [
        (Look(blur=0.0009), "blur must be from 0.001 to 0.02"),
        (Look(blur=0.021), "blur must be from 0.001 to 0.02"),
        (Look(blur=-0.005), "blur must be from"),
        (Look(darken=-0.1), "darkening must be from 0 to 0.8"),
        (Look(darken=0.81), "darkening must be from 0 to 0.8"),
    ],
)
def test_a_look_value_that_cant_be_used(look: Look, message: str) -> None:
    """Blur and darkening, in any mode or background (they are kept)."""
    monitor = MonitorSettings(id=MonitorId("m"), look=look)
    with pytest.raises(SettingsError, match=f"Monitor m: the {message}"):
        check_settings(settings(monitors=(monitor,)))


def test_the_ends_of_the_looks_ranges_can_be_used() -> None:
    for look in (Look(blur=0.001, darken=0.0), Look(blur=0.02, darken=0.8)):
        check_settings(
            settings(monitors=(MonitorSettings(id=MonitorId("m"), look=look),))
        )


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"pause_when_idle": 0.0}, "Idle pause: Must be more than 0"),
        ({"favourites_file": Path("f.txt")}, "Favourites file: Use the file's full"),
        ({"to_remove_file": Path("r.txt")}, "To-remove file: Use the file's full"),
    ],
)
def test_a_general_value_that_cant_be_used(
    values: dict[str, Any], message: str
) -> None:
    with pytest.raises(SettingsError, match=message):
        check_settings(settings(**values))


def test_only_a_new_or_changed_folder_must_exist(tmp_path: Path) -> None:
    gone = tmp_path / "unmounted"
    old = settings(playlist(tmp_path, directory=gone))
    check_folders(old, old)  # it was there before: it may come back
    renamed = settings(playlist(tmp_path, directory=gone, name="other"))
    check_folders(renamed, old)
    with pytest.raises(SettingsError, match="walls, folder: No such folder"):
        check_folders(old, Settings())  # new
    with pytest.raises(SettingsError, match="No such folder"):
        check_folders(settings(playlist(tmp_path, directory=gone / "x")), old)
    check_folders(settings(playlist(tmp_path)), old)  # changed, and there


def test_no_playlists_says_so() -> None:
    assert Settings().warnings() == [NO_PLAYLISTS]
    # One warning among the others, not a return before them.
    assert Settings(pause_when_idle=None).warnings() == [NO_PLAYLISTS]


def test_warns_when_interval_is_shorter_than_idle_pause(tmp_path: Path) -> None:
    value = settings(playlist(tmp_path, interval=120.0), pause_when_idle=300.0)
    (warning,) = value.warnings()
    assert "walls" in warning and "2m" in warning and "5m" in warning


@pytest.mark.parametrize(
    ("interval", "idle"), [(300.0, 300.0), (1800.0, 300.0), (60.0, None), (None, 5.0)]
)
def test_no_warning_otherwise(
    tmp_path: Path, interval: float | None, idle: float | None
) -> None:
    value = settings(playlist(tmp_path, interval=interval), pause_when_idle=idle)
    assert not value.warnings()
