from pathlib import Path

from wallpaper_py.core import paths


def test_each_config_has_its_own_state_database(tmp_path: Path) -> None:
    """Playlist ids are only unique within a config: no sharing state."""
    default = paths.state_db(paths.config_file())
    assert default == paths.data_dir() / "state.sqlite3"
    other = paths.state_db(tmp_path / "other.toml")
    assert other.parent == paths.data_dir() and other != default
    assert paths.state_db(tmp_path / "x" / ".." / "other.toml") == other
    assert paths.state_db(tmp_path / "third.toml") not in (default, other)
