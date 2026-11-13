"""
The whole app in-process (fake backend), with the CLI as a separate process
like in real use: single instance, commands through the socket, or run by
the CLI itself (under the app's lock) when no app is running.
"""

import logging
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, nullcontext
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from wallpaper_py import cli as cli_module
from wallpaper_py.app import runtime as app_runtime
from wallpaper_py.app.instance import InstanceLock
from wallpaper_py.app.ipc import CommandServer
from wallpaper_py.app.processes import autostart_command
from wallpaper_py.app.runtime import AppOptions, Platform, run_app
from wallpaper_py.app.ui import process as ui_process
from wallpaper_py.core import paths
from wallpaper_py.core.model import WallpaperError

from .clients import CLI, CLI_HERE, HOLD_LOCK, NO_WINDOW, run_python
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
        app_runtime._application(tray=True)  # pylint: disable=protected-access
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
    options = AppOptions(write_config(tmp_path), tray=tray, quit_after_ticks=1)
    assert run_app(options, platform) == 0
    assert [c.image.name for c in backend.set_calls] == ["a.jpg"]
    assert InstanceLock(paths.instance_lock()).acquire()  # released


def test_second_instance_is_refused(tmp_path: Path, platform: Platform) -> None:
    lock = InstanceLock(paths.instance_lock())
    assert lock.acquire()
    try:
        options = AppOptions(write_config(tmp_path), tray=False, lock_wait_s=0.2)
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
        for name in ("next", "prev"):
            results[name] = run_python(qtbot, CLI, *cli, name)
        QCoreApplication.quit()

    QTimer.singleShot(0, terminal)  # runs once exec() started: the socket is up
    assert run_app(AppOptions(config, tray=False), platform) == 0

    assert results["next"].returncode == 0, results["next"].stderr
    assert results["next"].stdout.strip() == "walls: b.jpg on Main"
    assert results["prev"].stdout.strip() == "walls: a.jpg on Main"
    # Every change was made by the app (the CLI's backend factory fails).
    assert [c.image.name for c in backend.set_calls] == ["a.jpg", "b.jpg", "a.jpg"]


def test_cli_without_config_reaches_an_app_on_another_config(
    qtbot: QtBot, tmp_path: Path, platform: Platform
) -> None:
    config = write_config(tmp_path)  # not the default config file
    assert config != paths.config_file()
    data = str(paths.data_dir())
    results: dict[str, subprocess.CompletedProcess[str]] = {}

    def terminal() -> None:
        results["plain"] = run_python(qtbot, CLI, data, "next")
        other = str(tmp_path / "other.toml")
        Path(other).write_text("", encoding="utf-8")
        results["other"] = run_python(qtbot, CLI, data, "--config", other, "next")
        results["reload"] = run_python(qtbot, CLI, data, "reload")
        QCoreApplication.quit()

    QTimer.singleShot(0, terminal)
    assert run_app(AppOptions(config, tray=False), platform) == 0
    # No --config: whatever the app runs.
    assert results["plain"].returncode == 0, results["plain"].stderr
    assert results["plain"].stdout.strip() == "walls: b.jpg on Main"
    # A config named explicitly must be the app's.
    assert results["other"].returncode == 1
    assert "The running app uses" in results["other"].stderr
    assert results["reload"].stdout.strip() == "Config reloaded"


def app_running() -> bool:
    lock = InstanceLock(paths.instance_lock())
    if lock.acquire():
        lock.release()
        return False
    return True


@contextmanager
def lock_held(seconds: float) -> Iterator[subprocess.Popen[str]]:
    """The app's lock, held by another process for `seconds` from now."""
    argv = [sys.executable, "-c", HOLD_LOCK, str(paths.data_dir()), str(seconds)]
    with subprocess.Popen(
        argv, stdout=subprocess.PIPE, text=True, creationflags=NO_WINDOW
    ) as holder:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"
        yield holder
        holder.wait(timeout=30)


def test_cli_runs_here_when_no_app_runs(qtbot: QtBot, tmp_path: Path) -> None:
    config = write_config(tmp_path)
    cli = [str(paths.data_dir()), "--config", str(config)]
    first = run_python(qtbot, CLI_HERE, *cli, "next")
    assert first.returncode == 0, first.stderr
    assert first.stdout.strip() == "walls: a.jpg on Main"  # no tick first
    assert not app_running()  # nothing left behind
    assert run_python(qtbot, CLI_HERE, *cli, "next").stdout.strip() == (
        "walls: b.jpg on Main"  # the state was kept
    )
    status = run_python(qtbot, CLI_HERE, *cli, "status").stdout
    assert "upcoming:  c.jpg" in status
    assert status.rstrip().endswith(cli_module.NOT_RUNNING)


def test_two_clis_without_an_app_take_turns(tmp_path: Path) -> None:
    config = write_config(tmp_path)
    argv = [sys.executable, "-c", CLI_HERE, str(paths.data_dir())]
    argv += ["--config", str(config), "next"]
    with ExitStack() as stack:
        clis = [
            stack.enter_context(
                subprocess.Popen(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=NO_WINDOW,
                )
            )
            for _ in range(2)
        ]
        outputs = [cli.communicate(timeout=60) for cli in clis]
    assert [cli.returncode for cli in clis] == [0, 0], outputs
    # One after the other, under the lock: never the same image twice.
    assert sorted(out.strip() for out, _ in outputs) == [
        "walls: a.jpg on Main",
        "walls: b.jpg on Main",
    ]


def test_cli_waits_while_the_lock_is_held(qtbot: QtBot, tmp_path: Path) -> None:
    """An app starting (lock taken, socket not up yet), or a command here."""
    config = write_config(tmp_path)
    with lock_held(1.0):
        started = time.monotonic()
        result = run_python(
            qtbot, CLI_HERE, str(paths.data_dir()), "--config", str(config), "next"
        )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "walls: a.jpg on Main"
    assert time.monotonic() - started > 0.5


def test_app_waits_for_a_command_run_here(
    tmp_path: Path, platform: Platform, backend: FakeBackend
) -> None:
    """Login starting the app while a CLI command holds the lock."""
    options = AppOptions(write_config(tmp_path), tray=False, quit_after_ticks=1)
    with lock_held(1.0):
        assert run_app(options, platform) == 0
    assert [c.image.name for c in backend.set_calls] == ["a.jpg"]


def test_socket_failure_stops_the_app(
    tmp_path: Path, platform: Platform, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cannot_listen(self: CommandServer) -> None:
        raise WallpaperError(f"Cannot open the command socket {self.name}: busy")

    monkeypatch.setattr(CommandServer, "listen", cannot_listen)
    with pytest.raises(WallpaperError, match="Cannot open the command socket"):
        run_app(AppOptions(write_config(tmp_path), tray=False), platform)
    assert not app_running()


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
