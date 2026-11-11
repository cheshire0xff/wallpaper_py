from pathlib import Path
from typing import Any

import pytest

from wallpaper_py.core.config import (
    Config,
    ConfigError,
    Order,
    PlaylistConfig,
    format_duration,
    load_config,
    parse_config,
    parse_duration,
)
from wallpaper_py.core.model import ImageMode

EXAMPLE = """
pause_when_idle = "10m"

[playlists.konachan]
directory = 'D:\\pictures\\KONACHAN'
monitors = ["AW3225QF", "PLX2783H"]
interval = "30m"
mode = "fill"

[playlists.fantasy]
directory = "fantasy"
monitors = "ZeroMOD"
order = "Sorted"
interval = "1h30m"
"""


def parse(raw: dict[str, Any], tmp_path: Path) -> Any:
    return parse_config(raw, base_dir=tmp_path / "cfg", default_dir=tmp_path / "data")


def playlist(**overrides: Any) -> dict[str, Any]:
    return {"directory": "d", "monitors": ["A"], "interval": "1m", **overrides}


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
    assert konachan.monitors == ("AW3225QF", "PLX2783H")
    assert konachan.interval == 1800
    assert konachan.order is Order.SHUFFLE
    assert konachan.mode is ImageMode.FILL
    assert fantasy.monitors == ("ZeroMOD",)
    assert fantasy.order is Order.SORTED and fantasy.mode is None
    assert fantasy.directory == tmp_path / "fantasy"  # relative to the file
    assert config.pause_when_idle == 600 and config.pause_when_locked
    assert config.favourites_file == tmp_path / "data" / "favourites.txt"
    assert config.to_remove_file == tmp_path / "data" / "to-remove.txt"
    assert config.playlist("KONACHAN") is konachan


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
        ({}, "at least one"),
        ({"playlists": {"a": playlist()}, "typo": 1}, "Unknown key"),
        ({"playlists": {"a": playlist(intervall="1m")}}, "Unknown key"),
        ({"playlists": {"a": {"directory": "d", "monitors": ["A"]}}}, "interval"),
        ({"playlists": {"a": playlist(monitors=[])}}, "monitors"),
        ({"playlists": {"a": playlist(interval=60)}}, "string"),
        ({"playlists": {"a": playlist(interval="5s")}}, "at least 10s"),
        ({"playlists": {"a": playlist(order="random")}}, "sorted, shuffle"),
        ({"playlists": {"a": playlist(mode="tile")}}, "fill, fit, stretch"),
        (
            {"playlists": {"a": playlist(), "b": playlist(monitors=["a"])}},
            "only one playlist",
        ),
        ({"playlists": {"a": playlist()}, "pause_when_idle": 5}, "duration"),
    ],
)
def test_invalid_configs_are_explained(
    raw: dict[str, Any], message: str, tmp_path: Path
) -> None:
    with pytest.raises(ConfigError, match=message):
        parse(raw, tmp_path)


def test_missing_file_says_where(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="No config file"):
        load_config(tmp_path / "nope.toml", default_dir=tmp_path)


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
        playlists=(PlaylistConfig("walls", Path("d"), ("A",), interval),),
        favourites_file=Path("f.txt"),
        to_remove_file=Path("r.txt"),
        pause_when_idle=idle,
    )


def test_warns_when_interval_is_shorter_than_idle_pause() -> None:
    (warning,) = config_with(interval=120, idle=300).warnings()
    assert "walls" in warning and "2m" in warning and "5m" in warning


@pytest.mark.parametrize(("interval", "idle"), [(300, 300), (1800, 300), (60, None)])
def test_no_warning_otherwise(interval: float, idle: float | None) -> None:
    assert not config_with(interval, idle).warnings()
