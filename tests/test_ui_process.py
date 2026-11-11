"""
The UI process: its windows, and its life (started on demand, gone with its
last window or with the app).
"""

import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from wallpaper_py.app import ipc
from wallpaper_py.app.instance import InstanceLock
from wallpaper_py.app.runtime import AppOptions, Platform, run_app
from wallpaper_py.app.ui.client import Client, ReplyCallback
from wallpaper_py.app.ui.process import UiOptions, Windows, run_ui
from wallpaper_py.app.ui.window import PlaceholderWindow, describe_overview
from wallpaper_py.app.worker import ServiceHost
from wallpaper_py.core import paths
from wallpaper_py.core.service import Change
from wallpaper_py.protocol import (
    CommandRequest,
    MonitorView,
    Overview,
    PlaylistView,
)

from .clients import NO_WINDOW, run_python
from .hosts import make_host
from .fakes import FakeAutostart, FakeBackend, FakePresence, make_monitor, write_config
from .test_ui_launcher import SHOW

WALLS = PlaylistView(
    name="walls",
    monitors=(
        MonitorView(selector="Main", name="Main", attached=True),
        MonitorView(selector="Side", name="Side", attached=False),
    ),
    current=Path("/p/a.jpg"),
    favourite=True,
)

# argv: data dir, config, window. A UI process as `wallpaper ui` runs it,
# logging to stderr; if no app runs it "starts" a harmless command (never
# the real app).
UI = """
import sys
from pathlib import Path
from wallpaper_py.app.processes import setup_logging
from wallpaper_py.app.ui.process import UiOptions, run_ui
from wallpaper_py.core import paths
data_dir, config, window = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
paths.data_dir = lambda: data_dir
paths.log_file = lambda: data_dir / "wallpaper.log"
setup_logging(None)
def harmless(config):
    return [sys.executable, "-c", "pass"]
sys.exit(run_ui(UiOptions(config, window, app_command=harmless)))
"""


class FakeClient(Client):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[CommandRequest] = []

    def run(self, request: CommandRequest, on_reply: ReplyCallback) -> None:
        self.requests.append(request)


def test_describe_overview() -> None:
    assert describe_overview(None) == "Connecting to wallpaper_py..."
    assert describe_overview(Overview(error="No config")) == "Error: No config"
    assert describe_overview(Overview(playlists=(WALLS,), paused="idle")) == (
        "walls · Main + Side (off)\n    a.jpg ★\nPaused: idle"
    )


def test_placeholder_follows_the_client(qtbot: QtBot) -> None:
    client = FakeClient()
    window = PlaceholderWindow("layout", client)
    qtbot.addWidget(window)
    assert window.windowTitle() == "wallpaper_py · Layout"
    assert window.overview_label.text() == "Connecting to wallpaper_py..."
    client.overview_changed.emit(Overview(playlists=(WALLS,)))
    assert window.overview_label.text().startswith("walls · Main")
    client.changed.emit(Change("walls", Path("/p/b.jpg"), "next", ("Main",)))
    assert window.change_label.text() == "Last change: walls: b.jpg on Main"
    client.failed.emit("could not start wallpaper_py")
    assert window.overview_label.text() == "Error: could not start wallpaper_py"


def test_one_window_per_kind_until_the_last_closes(qtbot: QtBot) -> None:
    windows = Windows(FakeClient())
    layout = windows.show("layout")
    assert windows.show("layout") is layout  # shown again, not a second one
    library = windows.show("library")
    assert windows.kinds() == ["layout", "library"]
    with qtbot.assertNotEmitted(windows.all_closed):
        layout.close()
    assert windows.kinds() == ["library"]
    with qtbot.waitSignal(windows.all_closed):
        library.close()
    assert not windows.kinds()


@pytest.fixture(name="host")
def fixture_host(tmp_path: Path) -> Iterator[ServiceHost]:
    """The app's service and socket, in this process."""
    backend = FakeBackend([make_monitor("Main")])
    name = ipc.server_name(paths.data_dir())
    host = make_host(write_config(tmp_path), lambda: backend, name)
    yield host
    assert host.shutdown(5_000)


def test_ui_shows_the_live_overview_until_its_window_closes(
    qtbot: QtBot, host: ServiceHost, tmp_path: Path
) -> None:
    with qtbot.waitSignal(host.overview_changed):
        host.start()
    seen: list[str] = []

    def close_when_live() -> None:
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, PlaceholderWindow) and widget.isVisible():
                text = widget.overview_label.text()
                if text.startswith("walls"):
                    seen.append(text)
                    widget.close()  # the last window: the UI process ends
                    return
        poll.start()

    poll = QTimer()
    poll.setSingleShot(True)
    poll.setInterval(50)
    poll.timeout.connect(close_when_live)
    poll.start()
    safety = QTimer()
    safety.setSingleShot(True)
    safety.timeout.connect(QCoreApplication.quit)
    safety.start(15_000)
    try:
        assert run_ui(UiOptions(tmp_path / "config.toml", "layout")) == 0
    finally:
        poll.stop()
        safety.stop()
    assert seen == ["walls · Main\n    a.jpg\nRotating"]
    assert InstanceLock(paths.ui_lock()).acquire()  # released


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
                    [sys.executable, "-c", UI, data, str(config), "layout"],
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
            result = run_python(qtbot, SHOW, name, "layout", str(tmp_path / "x.toml"))
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
