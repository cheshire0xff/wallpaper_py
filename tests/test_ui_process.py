"""
The window process: its window on the page asked for (also when one is
open already), at most one of them, and its life (started on demand, gone
with its window or with the daemon).
"""

import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from wallpaper_py.app import ipc
from wallpaper_py.app.runtime import DaemonOptions, Platform, run_daemon
from wallpaper_py.app.ui.process import UiOptions, run_ui
from wallpaper_py.app.ui.window import MainWindow
from wallpaper_py.app.worker import ServiceThread
from wallpaper_py.core import paths
from wallpaper_py.protocol import (
    PageName,
    Reply,
    ShowWindowRequest,
    ShowWindowResult,
    StatusRequest,
    StatusResult,
)

from .clients import NO_WINDOW
from .hosts import make_service_thread
from .fakes import (
    FakeAutostart,
    FakeBackend,
    FakePresence,
    make_monitor,
    write_settings,
)

# argv: data dir, page. A window process as `wallpaper ui` runs it, logging
# to stderr. If no daemon runs, the daemon it "starts" only leaves a mark
# (started-<page> in the data dir): never the real daemon.
UI = """
import sys
from pathlib import Path
from wallpaper_py.app.processes import setup_logging
from wallpaper_py.app.ui.process import UiOptions, run_ui
from wallpaper_py.core import paths
data_dir, page = Path(sys.argv[1]), sys.argv[2]
paths.data_dir = lambda: data_dir
paths.cache_dir = lambda: data_dir / "cache"
paths.log_file = lambda: data_dir / "wallpaper.log"
setup_logging(None)
mark = str(data_dir / f"started-{page}")
def harmless():
    return [sys.executable, "-c", f"open({mark!r}, 'a').close()"]
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


def with_service_thread_started(service_thread: ServiceThread) -> None:
    started: list[bool] = []
    service_thread.client.overview_changed.connect(lambda _: started.append(True))
    service_thread.start()
    deadline = time.monotonic() + 10
    while not started and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert started


def show_window(qtbot: QtBot, service_thread: ServiceThread, page: PageName) -> Reply:
    """As the tray asks: through the daemon's own client."""
    replies: list[Reply] = []
    service_thread.client.run(ShowWindowRequest(page=page), replies.append)
    qtbot.waitUntil(lambda: bool(replies))
    return replies[0]


def test_ui_shows_the_live_overview_until_its_window_closes(
    qtbot: QtBot, service_thread: ServiceThread
) -> None:
    with_service_thread_started(service_thread)
    seen: list[str] = []
    replies: list[Reply] = []

    def close_when_live() -> bool:
        window = main_window()
        row = window.home.rows.get("walls-id") if window is not None else None
        # The overview, then the schedule from `status` (both over the
        # daemon's socket).
        if window is None or row is None or "sorted" not in row.details.text():
            return False
        seen.append(f"{row.file.text()} | {row.details.text()}")
        seen.append(window.sidebar.status.text())
        # A command still on its way when the window closes gets its reply.
        client = window.home._client  # pylint: disable=protected-access
        client.run(StatusRequest(), replies.append)
        window.close()  # the window process ends with its window
        return True

    assert run_ui_while(UiOptions(), close_when_live) == 0
    assert len(seen) == 2
    assert seen[0].startswith("a.jpg | 8×6 · ")
    assert seen[0].endswith(" · sorted, every 10m · next in 10m")
    assert seen[1] == "Rotation active"
    assert len(replies) == 1 and isinstance(replies[0], StatusResult)
    # The subscription ended with the window: the daemon knows of none.
    assert show_window(qtbot, service_thread, "home") == ShowWindowResult(shown=False)


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


def test_the_tray_brings_the_open_window_to_its_page(
    qtbot: QtBot, service_thread: ServiceThread
) -> None:
    """A tray entry (or a second `wallpaper ui settings`) while one is open."""
    with_service_thread_started(service_thread)
    replies: list[Reply] = []
    pages: list[str] = []

    def ask_then_close() -> bool:
        window = main_window()
        if window is None:
            return False
        if not pages:
            pages.append(window.current_page())
            window.showMinimized()
            request = ShowWindowRequest(page="settings")
            service_thread.client.run(request, replies.append)
            return False
        if window.current_page() != "settings" or window.isMinimized():
            return False
        pages.append(window.current_page())
        window.close()
        return True

    assert run_ui_while(UiOptions(), ask_then_close) == 0
    assert pages == ["home", "settings"]
    qtbot.waitUntil(lambda: bool(replies))
    assert replies == [ShowWindowResult(shown=True)]


def start_window(data: Path, page: PageName) -> subprocess.Popen[str]:
    return subprocess.Popen(  # pylint: disable=consider-using-with
        [sys.executable, "-c", UI, str(data), page],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=NO_WINDOW,
    )


@pytest.mark.parametrize("daemon_first", [True, False], ids=["running", "starting"])
def test_two_windows_at_once_leave_one(
    qtbot: QtBot, tmp_path: Path, daemon_first: bool
) -> None:
    """
    Two `wallpaper ui` at once: the daemon takes their subscriptions one at
    a time, and the first is the window. The other has its page shown there
    and exits; if both had to start the daemon, after showing "Connecting…".
    The window ends with the daemon.
    """
    write_settings(tmp_path)
    data = paths.data_dir()
    backend = FakeBackend([make_monitor("Main")])
    windows: dict[PageName, subprocess.Popen[str]] = {}

    def start_both() -> None:
        for page in ("home", "library"):
            windows[page] = start_window(data, page)

    def scenario() -> None:
        try:
            if daemon_first:
                start_both()
            qtbot.waitUntil(
                lambda: any(w.poll() is not None for w in windows.values()),
                timeout=30_000,
            )
            qtbot.wait(300)  # the other one stays: it is the window
        finally:
            QCoreApplication.quit()  # the daemon quits: so must the window

    try:
        if not daemon_first:
            start_both()
            marks = [data / f"started-{page}" for page in windows]
            deadline = time.monotonic() + 30
            while not all(m.exists() for m in marks):  # both found no daemon
                assert time.monotonic() < deadline
                time.sleep(0.05)
        QTimer.singleShot(0, scenario)
        platform = Platform(
            lambda: backend, FakePresence, FakeAutostart, thread_scope=nullcontext
        )
        assert run_daemon(DaemonOptions(tray=False), platform) == 0
        logs = {page: w.communicate(timeout=20)[1] for page, w in windows.items()}
    finally:
        for window in windows.values():
            if window.poll() is None:
                window.kill()

    assert all(w.returncode == 0 for w in windows.values()), logs
    one_window_was_kept(logs)


def one_window_was_kept(logs: dict[PageName, str]) -> None:
    """From the window processes' logs, by page."""
    turned_away = [p for p, log in logs.items() if "open in another process" in log]
    assert len(turned_away) == 1, logs
    [other] = turned_away
    [kept] = set(logs) - {other}
    assert f"Showing {other}" in logs[kept]  # its page, in the window
    assert "The daemon closed the connection" in logs[kept]
