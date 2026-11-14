from wallpaper_py.core import paths


def test_state_database_is_in_the_data_folder() -> None:
    assert paths.state_db() == paths.data_dir() / "state.sqlite3"
