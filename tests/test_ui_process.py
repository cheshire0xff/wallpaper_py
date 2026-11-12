"""
The UI process: its window on the page asked for (also when it already
runs), and its life (started on demand, gone with its window or with the
app).
"""

import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QObject, QTimer
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from wallpaper_py.app import ipc
from wallpaper_py.app.instance import InstanceLock
from wallpaper_py.app.runtime import AppOptions, Platform, run_app
from wallpaper_py.app.ui.launcher import PageName, ShowRequest, ui_server_name
from wallpaper_py.app.ui.process import UiOptions, run_ui
from wallpaper_py.app.ui.window import MainWindow
from wallpaper_py.app.worker import ServiceHost
from wallpaper_py.core import paths

from .clients import NO_WINDOW, run_python
from .hosts import make_host
from .fakes import FakeAutostart, FakeBackend, FakePresence, make_monitor, write_config
from .test_ui_launcher import SHOW

# argv: data dir, config, page. A UI process as `wallpaper ui` runs it,
# logging to stderr; if no app runs it "starts" a harmless command (never
# the real app).
UI = """
import sys
from pathlib import Path
from wallpaper_py.app.processes import setup_logging
from wallpaper_py.app.ui.process import UiOptions, run_ui
from wallpaper_py.core import paths
data_dir, config, page = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
paths.data_dir = lambda: data_dir
paths.log_file = lambda: data_dir / "wallpaper.log"
setup_logging(None)
def harmless(config):
    return [sys.executable, "-c", "pass"]
sys.exit(run_ui(UiOptions(config, page, app_command=harmless)))
"""


@pytest.fixture(name="host")
def fixture_host(tmp_path: Path) -> Iterator[ServiceHost]:
    """The app's service and socket, in this process."""
    backend = FakeBackend([make_monitor("Main")])
    name = ipc.server_name(paths.data_dir())
    host = make_host(write_config(tmp_path), lambda: backend, name)
    yield host
    assert host.shutdown(5_000)


def main_window() -> MainWindow | None:
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, MainWindow) and widget.isVisible():
            return widget
    return None


def run_ui_while(options: UiOptions, step: Callable[[], bool]) -> int:
    """`run_ui` here, calling `step` every 50 ms until it says it is done."""

    def poll() -> None:
        if not step():
            timer.start()

    timer = QTimer()
    timer.setSingleShot(True)
    timer.setInterval(50)
    timer.timeout.connect(poll)
    timer.start()
    safety = QTimer()
    safety.setSingleShot(True)
    safety.timeout.connect(QCoreApplication.quit)
    safety.start(15_000)
    try:
        return run_ui(options)
    finally:
        timer.stop()
        safety.stop()


def test_ui_shows_the_live_overview_until_its_window_closes(
    qtbot: QtBot, host: ServiceHost, tmp_path: Path
) -> None:
    del qtbot
    with_host_started(host)
    seen: list[str] = []

    def close_when_live() -> bool:
        window = main_window()
        row = window.home.rows.get("walls") if window is not None else None
        # The overview, then the schedule from `status` (both over the
        # app's socket); the images are empty files.
        if window is None or row is None or "sorted" not in row.details.text():
            return False
        seen.append(f"{row.file.text()} | {row.details.text()}")
        seen.append(window.sidebar.status.text())
        window.close()  # the UI process ends with its window
        return True

    assert run_ui_while(UiOptions(tmp_path / "config.toml"), close_when_live) == 0
    assert len(seen) == 2
    assert seen[0].startswith("a.jpg | can't read: ")
    assert seen[0].endswith(" · sorted, every 10m · next in 10m")
    assert seen[1] == "Rotation active"
    assert InstanceLock(paths.ui_lock()).acquire()  # released


def with_host_started(host: ServiceHost) -> None:
    started: list[bool] = []
    host.overview_changed.connect(lambda _: started.append(True))
    host.start()
    deadline = time.monotonic() + 10
    while not started and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert started


@pytest.mark.parametrize("page", ["home", "library", "settings"])
def test_the_window_opens_on_the_page_asked_for(
    host: ServiceHost, tmp_path: Path, page: PageName
) -> None:
    """`wallpaper ui <page>` (the CLI and the tray start it so)."""
    with_host_started(host)
    shown: list[object] = []

    def close_when_shown() -> bool:
        window = main_window()
        if window is None:
            return False
        shown.append(window.current_page())
        shown.append(window.stack.currentWidget() is window.pages[page])
        window.close()
        return True

    options = UiOptions(tmp_path / "config.toml", page)
    assert run_ui_while(options, close_when_shown) == 0
    assert shown == [page, True]


def test_a_second_request_brings_the_window_to_its_page(
    host: ServiceHost, tmp_path: Path
) -> None:
    """A second `wallpaper ui settings` (or a tray entry) while one runs."""
    with_host_started(host)
    owner = QObject()
    answers: list[bytes] = []
    pages: list[str] = []

    def ask_then_close() -> bool:
        window = main_window()
        if window is None:
            return False
        if not answers and not pages:
            pages.append(window.current_page())
            window.showMinimized()
            request = ShowRequest(page="settings", config=tmp_path / "config.toml")
            ipc.LineRequest(  # as the tray asks: never blocking
                ui_server_name(paths.data_dir()),
                ipc.encode(request),
                answers.append,
                lambda *_: answers.append(b"failed"),
                parent=owner,
            )
            return False
        if window.current_page() != "settings" or window.isMinimized():
            return False
        pages.append(window.current_page())
        window.close()
        return True

    assert run_ui_while(UiOptions(tmp_path / "config.toml"), ask_then_close) == 0
    assert pages == ["home", "settings"]
    assert answers == [b'{"error":null}']


def test_one_ui_process_that_ends_with_the_app(qtbot: QtBot, tmp_path: Path) -> None:
    config = write_config(tmp_path)
    data = str(paths.data_dir())
    backend = FakeBackend([make_monitor("Main")])
    platform = Platform(
        lambda: backend, FakePresence, FakeAutostart, thread_scope=nullcontext
    )
    shows: dict[str, str] = {}
    processes: list[subprocess.Popen[str]] = []

    def scenario() -> None:
        """While the app's loop runs: start a UI, use it, quit the app."""
        try:
            processes.append(
                subprocess.Popen(  # pylint: disable=consider-using-with
                    [sys.executable, "-c", UI, data, str(config), "home"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=NO_WINDOW,
                )
            )
            name = ipc.server_name(Path(data), "ui")
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:  # until it listens
                result = run_python(qtbot, SHOW, name, "library", str(config))
                shows["library"] = result.stdout.strip()
                if shows["library"] != "NONE":
                    break
                qtbot.wait(200)
            result = run_python(qtbot, SHOW, name, "home", str(tmp_path / "x.toml"))
            shows["other config"] = result.stdout.strip()
        finally:
            QCoreApplication.quit()  # the app quits: so must the UI

    QTimer.singleShot(0, scenario)
    assert run_app(AppOptions(config, tray=False), platform) == 0
    assert len(processes) == 1
    ui = processes[0]
    try:
        _, err = ui.communicate(timeout=20)
    finally:
        if ui.poll() is None:
            ui.kill()
    assert shows["library"] == "SHOWN"  # one UI process; it took the request
    assert shows["other config"].startswith("ERROR The open windows use")
    assert ui.returncode == 0, err
    assert "Connected to the app" in err  # it followed the app's events
    assert "The app closed the connection" in err
