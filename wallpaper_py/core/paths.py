"""Per-user locations for app data. State is per OS, never shared."""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_cache_path, user_data_path

APP_NAME = "wallpaper_py"


def data_dir() -> Path:
    return user_data_path(APP_NAME, appauthor=False)


def cache_dir() -> Path:
    return user_cache_path(APP_NAME, appauthor=False)


def render_dir() -> Path:
    """Images rendered to exact monitor size (see core.setter)."""
    return cache_dir() / "rendered"
