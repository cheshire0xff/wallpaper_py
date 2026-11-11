"""
The whole app in-process (fake backend), with the CLI as a separate process
like in real use: single instance, commands through the socket.
"""

import logging
import signal
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QTimer
from pytestqt.qtbot import QtBot

from wallpaper_py.app.instance import InstanceLock
from wallpaper_py.app.runtime import AppOptions, Platform, autostart_command, run_app
from wallpaper_py.core import paths
from wallpaper_py.core.model import WallpaperError

from .clients import CLI, run_python
from .fakes import (
    FakeAutostart,
    FakeBackend,
    FakePresence,
    make_monitor,
    write_config,
)


@pytest.fixture(name="backend")
def fixture_backend() -> FakeBackend:
    return FakeBackend([make_monitor("Main")])


@pytest.fixture(name="platform")
def fixture_platform(backend: FakeBackend) -> Platform:
    return Platform(
        backend_factory=lambda: backend,
        presence_factory=FakePresence,
        autostart_factory=FakeAutostart,
        thread_scope=nullcontext,
    )


@pytest.mark.parametrize("tray", [False, True])
def test_runs_and_quits_after_ticks(
    tmp_path: Path, platform: Platform, backend: FakeBackend, tray: bool
) -> None:
    options = AppOptions(write_config(tmp_path), tray=tray, quit_after_ticks=1)
    assert run_app(options, platform) == 0
    assert [c.image.name for c in backend.set_calls] == ["a.jpg"]
    assert InstanceLock(paths.instance_lock()).acquire()  # released


def test_second_instance_is_refused(tmp_path: Path, platform: Platform) -> None:
    lock = InstanceLock(paths.instance_lock())
    assert lock.acquire()
    try:
        options = AppOptions(write_config(tmp_path), tray=False)
        with pytest.raises(WallpaperError, match="already running"):
            run_app(options, platform)
    finally:
        lock.release()


def test_cli_commands_go_through_the_running_app(
    qtbot: QtBot, tmp_path: Path, platform: Platform, backend: FakeBackend
) -> None:
    config = write_config(tmp_path)
    cli = [str(paths.data_dir()), "--config", str(config)]
    results: dict[str, subprocess.CompletedProcess[str]] = {}

    def terminal() -> None:
        """Commands from another process, while the app's loop keeps serving."""
        for name in ("next", "prev", "run"):
            results[name] = run_python(qtbot, CLI, *cli, name)
        QCoreApplication.quit()

    QTimer.singleShot(0, terminal)  # runs once exec() started: the socket is up
    assert run_app(AppOptions(config, tray=False), platform) == 0

    assert results["next"].returncode == 0, results["next"].stderr
    assert results["next"].stdout.strip() == "walls: b.jpg on Main"
    assert results["prev"].stdout.strip() == "walls: a.jpg on Main"
    assert results["run"].returncode == 1
    assert "already running" in results["run"].stderr
    # Every change was made by the app (the CLI's backend factory fails).
    assert [c.image.name for c in backend.set_calls] == ["a.jpg", "b.jpg", "a.jpg"]


def test_autostart_command_starts_the_tray_without_a_console(tmp_path: Path) -> None:
    command = autostart_command(paths.config_file())
    assert command[1:] == ["-m", "wallpaper_py", "tray"]
    python = Path(sys.executable)
    if sys.platform == "win32":
        python = python.with_name("pythonw.exe")  # no console window
    assert Path(command[0]) == python
    other = autostart_command(tmp_path / "other.toml")
    assert other[-3:] == ["--config", str((tmp_path / "other.toml").resolve()), "tray"]


def test_ctrl_c_stops_cleanly(
    tmp_path: Path, platform: Platform, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    QTimer.singleShot(200, lambda: signal.raise_signal(signal.SIGINT))
    assert run_app(AppOptions(write_config(tmp_path), tray=False), platform) == 0
    messages = [r.getMessage() for r in caplog.records]
    assert "Ctrl+C: stopping (press it again to exit at once)" in messages
    assert messages[-1] == "Stopped"
    assert InstanceLock(paths.instance_lock()).acquire()  # released
