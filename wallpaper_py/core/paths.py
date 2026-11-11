"""Per-user locations for app data. State is per OS, never shared."""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_cache_path, user_config_path, user_data_path

APP_NAME = "wallpaper_py"


def data_dir() -> Path:
    return user_data_path(APP_NAME, appauthor=False)


def config_dir() -> Path:
    return user_config_path(APP_NAME, appauthor=False)


def config_file() -> Path:
    """The human-edited TOML file defining playlists (see core.config)."""
    return config_dir() / "config.toml"


def state_db() -> Path:
    """SQLite state: history, shuffle decks, favourites, to-remove."""
    return data_dir() / "state.sqlite3"


def cache_dir() -> Path:
    return user_cache_path(APP_NAME, appauthor=False)


def render_dir() -> Path:
    """Images rendered to exact monitor size (see core.setter)."""
    return cache_dir() / "rendered"
