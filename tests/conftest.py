"""
Shared test setup: Qt runs offscreen (CI has no desktop, and tests never
show a tray icon), and every test gets its own data / config / cache / log
directories, so no test can reach a real running app or real state.
"""

import os
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from wallpaper_py.core import paths

os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture(autouse=True, scope="session")
def qt_application(qapp: QApplication) -> QApplication:
    """
    Create the QApplication before anything else can create a plain
    QCoreApplication (e.g. `wallpaper run` in a test).
    """
    return qapp


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "user"
    monkeypatch.setattr(paths, "data_dir", lambda: home / "data")
    monkeypatch.setattr(paths, "config_dir", lambda: home / "config")
    monkeypatch.setattr(paths, "cache_dir", lambda: home / "cache")
    monkeypatch.setattr(paths, "log_file", lambda: home / "logs" / "wallpaper.log")
