"""Per-user locations for app data. State is per OS, never shared."""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_cache_path, user_data_path, user_log_path

APP_NAME = "wallpaper_py"


def data_dir() -> Path:
    return user_data_path(APP_NAME, appauthor=False)


def state_db() -> Path:
    """
    SQLite: the settings and the state (history, shuffle decks, holds, what
    each monitor shows). Written only by the app (core.store).
    """
    return data_dir() / "state.sqlite3"


def instance_lock() -> Path:
    """Held by the running app: one process rotates (and writes the state)."""
    return data_dir() / "app.lock"


def ui_lock() -> Path:
    """Held by the UI process (the windows): at most one runs."""
    return data_dir() / "ui.lock"


def ui_state() -> Path:
    """
    The windows' view state (the Library's playlist, tab, ...): written only
    by the UI process; not settings.
    """
    return data_dir() / "ui-state.json"


def log_file() -> Path:
    """The app's log (the tray app has no terminal)."""
    return user_log_path(APP_NAME, appauthor=False) / "wallpaper.log"


def ui_log_file() -> Path:
    """The UI process's log, next to the app's (each rotates its own file)."""
    return log_file().with_name("ui.log")


def cache_dir() -> Path:
    return user_cache_path(APP_NAME, appauthor=False)


def render_dir() -> Path:
    """Images rendered to exact monitor size (see core.setter)."""
    return cache_dir() / "rendered"


def thumbnail_dir() -> Path:
    """The library window's thumbnail disk cache (Phase 5; may not exist yet)."""
    return cache_dir() / "thumbnails"
