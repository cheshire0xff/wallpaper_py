from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from wallpaper_py.core.config import (
    NO_PLAYLISTS,
    Config,
    ConfigError,
    Order,
    PlaylistConfig,
    format_duration,
    load_config,
    parse_config,
    parse_duration,
)
from wallpaper_py.core.model import ImageMode, MonitorId

EXAMPLE = """
pause_when_idle = "10m"

[playlists.konachan]
id = "1"
directory = 'D:\\pictures\\KONACHAN'
monitors = ["AW3225QF", "PLX2783H"]
interval = "30m"
mode = "fill"

[playlists.fantasy]
id = "2"
directory = "fantasy"
monitors = ["ZeroMOD"]
order = "sorted"
interval = "1h30m"
"""


def parse(raw: dict[str, Any], tmp_path: Path) -> Any:
    return parse_config(raw, base_dir=tmp_path / "cfg", default_dir=tmp_path / "data")


def playlist(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "1",
        "directory": "d",
        "monitors": ["A"],
        "interval": "1m",
        **overrides,
    }


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("45s", 45), ("30m", 1800), ("1h30m", 5400), ("2d", 172800), ("1H 5M", 3900)],
)
def test_parse_duration(text: str, seconds: float) -> None:
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "30", "m", "5 minutes", "-5m", "1m1h"])
def test_parse_duration_rejects_garbage(text: str) -> None:
    with pytest.raises(ConfigError):
        parse_duration(text)


def test_load_example(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(EXAMPLE, encoding="utf-8")
    config = load_config(path, default_dir=tmp_path / "data")

    konachan, fantasy = config.playlists
    assert konachan.monitors == (MonitorId("AW3225QF"), MonitorId("PLX2783H"))
    assert konachan.interval == 1800
    assert konachan.order is Order.SHUFFLE
    assert konachan.mode is ImageMode.FILL
    assert fantasy.monitors == (MonitorId("ZeroMOD"),)
    assert fantasy.order is Order.SORTED and fantasy.mode is None
    assert fantasy.directory == tmp_path / "fantasy"  # relative to the file
    assert config.pause_when_idle == 600 and config.pause_when_locked
    assert config.favourites_file == tmp_path / "data" / "favourites.txt"
    assert config.to_remove_file == tmp_path / "data" / "to-remove.txt"
    assert config.playlist("KONACHAN") is konachan
    assert (konachan.id, fantasy.id) == ("1", "2")


def test_list_files_are_configurable(tmp_path: Path) -> None:
    config = parse(
        {
            "playlists": {"a": playlist()},
            "favourites_file": "lists/fav.txt",
            "to_remove_file": str(tmp_path / "rm.txt"),
            "pause_when_idle": False,
        },
        tmp_path,
    )
    assert config.favourites_file == tmp_path / "cfg" / "lists" / "fav.txt"
    assert config.to_remove_file == tmp_path / "rm.txt"
    assert config.pause_when_idle is None


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"playlists": {"a": playlist()}, "typo": 1}, "Unknown key"),
        ({"playlists": {"a": playlist(intervall="1m")}}, "Unknown key"),
        (
            {"playlists": {"a": {"id": "1", "directory": "d", "monitors": ["A"]}}},
            "interval",
        ),
        ({"playlists": {"a": playlist(monitors=[])}}, "monitors"),
        ({"playlists": {"a": playlist(interval=60)}}, "string"),
        ({"playlists": {"a": playlist(interval="5s")}}, "at least 10s"),
        ({"playlists": {"a": playlist(order="random")}}, "sorted, shuffle"),
        ({"playlists": {"a": playlist(mode="tile")}}, "fill, fit, stretch"),
        (
            {"playlists": {"a": playlist(), "b": playlist(id="2", monitors=["a"])}},
            "only one playlist",
        ),
        ({"playlists": {"a": playlist()}, "pause_when_idle": 5}, "duration"),
        ({"playlists": {"a": playlist()}, "pause_when_idle": "0s"}, "use false"),
        ({"playlists": {"a": playlist()}, "pause_when_idle": True}, "or false"),
        ({"playlists": {"a": playlist()}, "pause_when_idle": 0}, "or false"),
        ({"playlists": 5}, r"must be \[playlists.NAME\] tables"),
        ({"playlists": {"a": 5}}, r"\[playlists.a\] must be a table"),
        ({"playlists": {"a": playlist(monitors=["A", 3])}}, "monitors must be a list"),
        ({"playlists": {"a": playlist(directory=" ")}}, "directory must be a path"),
        ({"playlists": {"a": playlist()}, "favourites_file": 3}, "must be a path"),
        # A misspelt key is named, not the key it leaves missing.
        (
            {
                "playlists": {
                    "a": {"id": "1", "directory": "d", "monitors": "A", "intrval": "1m"}
                }
            },
            "Unknown key.*intrval",
        ),
    ],
)
def test_invalid_configs_are_explained(
    raw: dict[str, Any], message: str, tmp_path: Path
) -> None:
    with pytest.raises(ConfigError, match=message):
        parse(raw, tmp_path)


@pytest.mark.parametrize("value", [None, "", "  "])
def test_a_playlist_needs_an_id(value: Any, tmp_path: Path) -> None:
    a = playlist(id=value)
    if value is None:
        del a["id"]
    raw = {"playlists": {"a": a, "b": playlist(id="4", monitors=["B"])}}
    with pytest.raises(ConfigError) as error:
        parse(raw, tmp_path)
    assert str(error.value) == "[playlists.a] needs an id, text no other playlist has"


@pytest.mark.parametrize("value", [1234, True, ["x"]])
def test_an_id_that_is_not_text_is_not_called_missing(
    value: Any, tmp_path: Path
) -> None:
    raw = {"playlists": {"a": playlist(id=value)}}
    with pytest.raises(ConfigError) as error:
        parse(raw, tmp_path)
    assert str(error.value) == "[playlists.a] id must be text"


def test_two_playlists_with_one_id_are_refused(tmp_path: Path) -> None:
    raw = {"playlists": {"a": playlist(), "b": playlist(monitors=["B"])}}
    with pytest.raises(ConfigError) as error:
        parse(raw, tmp_path)
    assert str(error.value) == "'a' and 'b' both have id '1'; ids must be unique"


@pytest.mark.parametrize("monitors", ["A", [], [" "], ["A", ""]])
def test_monitors_is_a_list_of_one_or_more_ids(
    tmp_path: Path, monitors: object
) -> None:
    raw = {"playlists": {"a": playlist(monitors=monitors)}}
    with pytest.raises(ConfigError) as error:
        parse(raw, tmp_path)
    assert str(error.value) == (
        "[playlists.a] monitors must be a list of one or more monitor ids"
    )


def test_enum_values_as_the_settings_window_writes_them(tmp_path: Path) -> None:
    raw = {"playlists": {"a": playlist(order="Sorted")}}
    with pytest.raises(ConfigError, match="order must be one of: sorted, shuffle"):
        parse(raw, tmp_path)


def test_no_playlists_is_valid_and_says_so(tmp_path: Path) -> None:
    for config in (
        parse({}, tmp_path),
        load_config(tmp_path / "none yet.toml", default_dir=tmp_path),
    ):
        assert not config.playlists
        assert config.warnings() == [NO_PLAYLISTS]


def test_only_a_missing_file_is_not_from_a_file(tmp_path: Path) -> None:
    """`prune` goes only by a config read from a file."""
    path = tmp_path / "config.toml"
    assert not load_config(path, default_dir=tmp_path).from_file
    path.write_text("", encoding="utf-8")
    assert load_config(path, default_dir=tmp_path).from_file


def test_manual_interval(tmp_path: Path) -> None:
    raw = {
        "playlists": {"strip": playlist(interval=" Manual ")},
        "pause_when_idle": "5m",
    }
    config = parse(raw, tmp_path)
    strip = config.playlist("strip")
    assert strip is not None and strip.interval is None and strip.manual
    assert not config.warnings()  # never changes, so never changes unseen
    with pytest.raises(ConfigError, match="'30m' or 'manual'"):
        parse({"playlists": {"a": playlist(interval=False)}}, tmp_path)


def test_example_config_is_valid(tmp_path: Path) -> None:
    example = Path(__file__).parents[1] / "docs" / "config.example.toml"
    config = load_config(example, default_dir=tmp_path)
    assert config.playlists


def test_bad_toml_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[playlists.a\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="config.toml"):
        load_config(path, default_dir=tmp_path)


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0, "0s"), (59.6, "1m"), (90, "1m30s"), (3600, "1h"), (5430, "1h30m30s")],
)
def test_format_duration(seconds: float, text: str) -> None:
    assert format_duration(seconds) == text
    assert parse_duration(text) == round(seconds)


def config_with(interval: float, idle: float | None) -> Config:
    return Config(
        playlists=(
            PlaylistConfig("walls", "1", Path("d"), (MonitorId("A"),), interval),
        ),
        favourites_file=Path("f.txt"),
        to_remove_file=Path("r.txt"),
        pause_when_idle=idle,
    )


def test_warns_when_interval_is_shorter_than_idle_pause() -> None:
    (warning,) = config_with(interval=120, idle=300).warnings()
    assert "walls" in warning and "2m" in warning and "5m" in warning


def test_no_playlists_does_not_stop_the_other_checks() -> None:
    """
    NO_PLAYLISTS is one warning among the others, not a return before them.
    (Today's pause_when_idle check is per playlist, so it adds none here.)
    """
    config = replace(config_with(interval=120, idle=300), playlists=())
    assert config.warnings() == [NO_PLAYLISTS]


@pytest.mark.parametrize(("interval", "idle"), [(300, 300), (1800, 300), (60, None)])
def test_no_warning_otherwise(interval: float, idle: float | None) -> None:
    assert not config_with(interval, idle).warnings()


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "cp1250"])
def test_only_utf8_without_a_byte_order_mark(tmp_path: Path, encoding: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(EXAMPLE + "# zażółć", encoding=encoding)
    with pytest.raises(ConfigError, match="not UTF-8"):
        load_config(path, default_dir=tmp_path)
