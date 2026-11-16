"""
The window process: its window on the page asked for (also when it already
runs), and its life (started on demand, gone with its window or with the
daemon).
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
from wallpaper_py.app.runtime import DaemonOptions, Platform, run_daemon
from wallpaper_py.app.ui.launcher import PageName, ShowRequest, ui_server_name
from wallpaper_py.app.ui.process import UiOptions, run_ui
from wallpaper_py.app.ui.window import MainWindow
from wallpaper_py.app.worker import ServiceThread
from wallpaper_py.core import paths

from .clients import NO_WINDOW, run_python
from .hosts import make_service_thread
from .fakes import (
    FakeAutostart,
    FakeBackend,
    FakePresence,
    make_monitor,
    write_settings,
)
from .test_ui_launcher import SHOW

# argv: data dir, page. A window process as `wallpaper ui` runs it,
# logging to stderr; if no daemon runs it "starts" a harmless command (never
# the real daemon).
UI = """
import sys
from pathlib import Path
from wallpaper_py.app.processes import setup_logging
from wallpaper_py.app.ui.process import UiOptions, run_ui
from wallpaper_py.core import paths
data_dir, page = Path(sys.argv[1]), sys.argv[2]
paths.data_dir = lambda: data_dir
paths.log_file = lambda: data_dir / "wallpaper.log"
setup_logging(None)
def harmless():
    return [sys.executable, "-c", "pass"]
sys.exit(run_ui(UiOptions(page, daemon_command=harmless)))
"""


@pytest.fixture(name="service_thread")
def fixture_service_thread(tmp_path: Path) -> Iterator[ServiceThread]:
    """The daemon's service and socket, in this process."""
    backend = FakeBackend([make_monitor("Main")])
    name = ipc.server_name(paths.data_dir())
    write_settings(tmp_path)
    service_thread = make_service_thread(lambda: backend, name)
    yield service_thread
    assert service_thread.shutdown(5_000)


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
    qtbot: QtBot, service_thread: ServiceThread
) -> None:
    del qtbot
    with_service_thread_started(service_thread)
    seen: list[str] = []

    def close_when_live() -> bool:
        window = main_window()
        row = window.home.rows.get("walls-id") if window is not None else None
        # The overview, then the schedule from `status` (both over the
        # daemon's socket).
        if window is None or row is None or "sorted" not in row.details.text():
            return False
        seen.append(f"{row.file.text()} | {row.details.text()}")
        seen.append(window.sidebar.status.text())
        window.close()  # the window process ends with its window
        return True

    assert run_ui_while(UiOptions(), close_when_live) == 0
    assert len(seen) == 2
    assert seen[0].startswith("a.jpg | 8×6 · ")
    assert seen[0].endswith(" · sorted, every 10m · next in 10m")
    assert seen[1] == "Rotation active"
    assert InstanceLock(paths.ui_lock()).acquire()  # released


def with_service_thread_started(service_thread: ServiceThread) -> None:
    started: list[bool] = []
    service_thread.client.overview_changed.connect(lambda _: started.append(True))
    service_thread.start()
    deadline = time.monotonic() + 10
    while not started and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert started


@pytest.mark.parametrize("page", ["home", "library", "settings"])
def test_the_window_opens_on_the_page_asked_for(
    service_thread: ServiceThread, page: PageName
) -> None:
    """`wallpaper ui <page>` (the CLI and the tray start it so)."""
    with_service_thread_started(service_thread)
    shown: list[object] = []

    def close_when_shown() -> bool:
        window = main_window()
        if window is None:
            return False
        shown.append(window.current_page())
        shown.append(window.stack.currentWidget() is window.pages[page])
        window.close()
        return True

    options = UiOptions(page)
    assert run_ui_while(options, close_when_shown) == 0
    assert shown == [page, True]


def test_a_second_request_brings_the_window_to_its_page(
    service_thread: ServiceThread,
) -> None:
    """A second `wallpaper ui settings` (or a tray entry) while one runs."""
    with_service_thread_started(service_thread)
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
            request = ShowRequest(page="settings")
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

    assert run_ui_while(UiOptions(), ask_then_close) == 0
    assert pages == ["home", "settings"]
    assert answers == [b'{"error":null}']


def test_one_window_process_that_ends_with_the_daemon(
    qtbot: QtBot, tmp_path: Path
) -> None:
    write_settings(tmp_path)
    data = str(paths.data_dir())
    backend = FakeBackend([make_monitor("Main")])
    platform = Platform(
        lambda: backend, FakePresence, FakeAutostart, thread_scope=nullcontext
    )
    shows: dict[str, str] = {}
    processes: list[subprocess.Popen[str]] = []

    def scenario() -> None:
        """While the daemon's loop runs: start a UI, use it, quit the daemon."""
        try:
            processes.append(
                subprocess.Popen(  # pylint: disable=consider-using-with
                    [sys.executable, "-c", UI, data, "home"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=NO_WINDOW,
                )
            )
            name = ipc.server_name(Path(data), "ui")
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:  # until it listens
                result = run_python(qtbot, SHOW, name, "library")
                shows["library"] = result.stdout.strip()
                if shows["library"] != "NONE":
                    break
                qtbot.wait(200)
        finally:
            QCoreApplication.quit()  # the daemon quits: so must the UI

    QTimer.singleShot(0, scenario)
    assert run_daemon(DaemonOptions(tray=False), platform) == 0
    assert len(processes) == 1
    ui = processes[0]
    try:
        _, err = ui.communicate(timeout=20)
    finally:
        if ui.poll() is None:
            ui.kill()
    assert shows["library"] == "SHOWN"  # one window process; it took the request
    assert ui.returncode == 0, err
    assert "Connected to the daemon" in err  # it followed the daemon's events
    assert "The daemon closed the connection" in err
