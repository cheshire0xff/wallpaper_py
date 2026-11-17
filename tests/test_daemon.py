"""
The whole app in-process (fake backend), with the CLI as a separate process
like in real use: single instance, commands through the socket, and the CLI
refusing when no daemon is running.
"""

import logging
import signal
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from wallpaper_py import cli as cli_module
from wallpaper_py.app import runtime as daemon_runtime
from wallpaper_py.app.instance import InstanceLock
from wallpaper_py.app.ipc import CommandServer
from wallpaper_py.app.processes import autostart_command
from wallpaper_py.app.runtime import DaemonOptions, Platform, run_daemon
from wallpaper_py.app.ui import process as ui_process
from wallpaper_py.core import paths
from wallpaper_py.core.model import WallpaperError

from .clients import CLI, run_python
from .fakes import (
    FakeAutostart,
    FakeBackend,
    FakePresence,
    FakeSound,
    make_monitor,
    write_settings,
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
        play_sound=FakeSound(),
    )


def test_tray_and_ui_set_the_application_icon(
    qt_application: QApplication,
) -> None:
    previous = qt_application.windowIcon()
    font = qt_application.font()
    name = qt_application.applicationName()
    quit_on_close = qt_application.quitOnLastWindowClosed()
    try:
        qt_application.setWindowIcon(QIcon())
        assert qt_application.windowIcon().isNull()
        daemon_runtime._application(tray=True)  # pylint: disable=protected-access
        assert not qt_application.windowIcon().isNull()

        qt_application.setWindowIcon(QIcon())
        ui_process._application()  # pylint: disable=protected-access
        assert not qt_application.windowIcon().isNull()
    finally:
        qt_application.setWindowIcon(previous)
        qt_application.setFont(font)
        qt_application.setApplicationName(name)
        qt_application.setQuitOnLastWindowClosed(quit_on_close)


@pytest.mark.parametrize("tray", [False, True])
def test_runs_and_quits_after_ticks(
    tmp_path: Path, platform: Platform, backend: FakeBackend, tray: bool
) -> None:
    write_settings(tmp_path)
    options = DaemonOptions(tray=tray, quit_after_ticks=1)
    assert run_daemon(options, platform) == 0
    assert [c.source and c.source.name for c in backend.set_calls] == ["a.jpg"]
    assert InstanceLock(paths.instance_lock()).acquire()  # released


def test_second_instance_is_refused(platform: Platform) -> None:
    lock = InstanceLock(paths.instance_lock())
    assert lock.acquire()
    try:
        options = DaemonOptions(tray=False)
        with pytest.raises(WallpaperError, match="already running"):
            run_daemon(options, platform)
    finally:
        lock.release()


def test_cli_commands_go_through_the_running_daemon(
    qtbot: QtBot, tmp_path: Path, platform: Platform, backend: FakeBackend
) -> None:
    write_settings(tmp_path)
    cli = [str(paths.data_dir())]
    results: dict[str, subprocess.CompletedProcess[str]] = {}

    def terminal() -> None:
        """Commands from another process, while the daemon's loop keeps serving."""
        for name in ("next", "prev"):
            results[name] = run_python(qtbot, CLI, *cli, name)
        QCoreApplication.quit()

    QTimer.singleShot(0, terminal)  # runs once exec() started: the socket is up
    assert run_daemon(DaemonOptions(tray=False), platform) == 0

    assert results["next"].returncode == 0, results["next"].stderr
    assert results["next"].stdout.strip() == "walls: b.jpg on Main"
    assert results["prev"].stdout.strip() == "walls: a.jpg on Main"
    # Every change was made by the daemon (the CLI's backend factory fails).
    assert [c.source and c.source.name for c in backend.set_calls] == [
        "a.jpg",
        "b.jpg",
        "a.jpg",
    ]


def daemon_running() -> bool:
    lock = InstanceLock(paths.instance_lock())
    if lock.acquire():
        lock.release()
        return False
    return True


def test_cli_refuses_when_no_daemon_runs(qtbot: QtBot, tmp_path: Path) -> None:
    write_settings(tmp_path)
    result = run_python(qtbot, CLI, str(paths.data_dir()), "next")
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.strip() == f"error: {cli_module.NOT_RUNNING}"
    assert not daemon_running()  # nothing started, no lock taken


def test_socket_failure_stops_the_daemon(
    tmp_path: Path, platform: Platform, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cannot_listen(self: CommandServer) -> None:
        raise WallpaperError(f"Cannot open the command socket {self.name}: busy")

    monkeypatch.setattr(CommandServer, "listen", cannot_listen)
    write_settings(tmp_path)
    with pytest.raises(WallpaperError, match="Cannot open the command socket"):
        run_daemon(DaemonOptions(tray=False), platform)
    assert not daemon_running()


def test_autostart_command_starts_the_tray_without_a_console() -> None:
    command = autostart_command()
    assert command[1:] == ["-m", "wallpaper_py", "tray"]
    python = Path(sys.executable)
    if sys.platform == "win32":
        python = python.with_name("pythonw.exe")  # no console window
    assert Path(command[0]) == python


def test_ctrl_c_stops_cleanly(
    tmp_path: Path, platform: Platform, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    write_settings(tmp_path)
    QTimer.singleShot(200, lambda: signal.raise_signal(signal.SIGINT))
    assert run_daemon(DaemonOptions(tray=False), platform) == 0
    messages = [r.getMessage() for r in caplog.records]
    assert "Ctrl+C: stopping (press it again to exit at once)" in messages
    assert messages[-1] == "Stopped"
    assert InstanceLock(paths.instance_lock()).acquire()  # released
