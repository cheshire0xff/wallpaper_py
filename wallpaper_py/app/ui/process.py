"""
The UI process: `wallpaper ui [layout|library]`.

Started on demand (by the tray or the CLI) and gone when its last window
closes, so the windows' memory (later: thumbnails) is given back. At most
one runs: a start first asks a running one to show the window and exits.
It is a client of the app's socket like the CLI, starting the app if none
runs, and it quits when the app does (its subscription ends). Without
--config it shows whatever config the app runs (the overview says which).
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication

from ...core import paths
from ...core.model import WallpaperError
from ..instance import InstanceLock
from ..ipc import server_name
from ..processes import autostart_command, quit_on_interrupt, start_detached
from .client import Client, SocketClient
from .launcher import (
    SHOW_TIMEOUT_S,
    ShowRequest,
    UiServer,
    WindowKind,
    show_in_running_ui,
    ui_server_name,
)
from .layout import LayoutWindow
from .settings import SettingsWindow
from .window import PlaceholderWindow, Window

log = logging.getLogger(__name__)

WindowFactory = Callable[[WindowKind, Client], Window]


def make_window(kind: WindowKind, client: Client) -> Window:
    """The window of a kind (the library window is still a placeholder)."""
    if kind == "layout":
        return LayoutWindow(client)
    if kind == "settings":
        return SettingsWindow(client)
    return PlaceholderWindow(kind, client)


@dataclass(frozen=True)
class UiOptions:
    config_path: Path | None  # --config, if given; None: the app's
    window: WindowKind = "layout"
    # How to start the app if it isn't running; injectable for tests.
    app_command: Callable[[Path], list[str]] = autostart_command


class Windows(QObject):
    """
    The open windows, at most one of each kind. `all_closed` fires when the
    last one closes.
    """

    all_closed = Signal()

    def __init__(self, client: Client, factory: WindowFactory = make_window) -> None:
        super().__init__()
        self._client = client
        self._factory = factory
        self._open: dict[WindowKind, Window] = {}

    @Slot(str)
    def show(self, kind: WindowKind) -> Window:
        window = self._open.get(kind)
        if window is None:
            window = self._factory(kind, self._client)
            window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            window.closed.connect(self._closed)  # no lambda: see ipc.LineServer
            self._open[kind] = window
        if window.isMinimized():
            window.showNormal()
        window.show()
        window.raise_()
        window.activateWindow()
        return window

    def kinds(self) -> list[WindowKind]:
        return list(self._open)

    @Slot()
    def _closed(self) -> None:
        window = self.sender()
        self._open = {k: w for k, w in self._open.items() if w is not window}
        if not self._open:
            self.all_closed.emit()


def run_ui(options: UiOptions) -> int:
    """Show the window here, or in the UI process that already runs."""
    name = ui_server_name(paths.data_dir())
    request = ShowRequest(window=options.window, config=options.config_path)
    if show_in_running_ui(name, request):
        return 0
    app = _application()
    lock = InstanceLock(paths.ui_lock())
    if not lock.acquire():
        # Another UI process is starting right now: let it show the window.
        if _wait_for_running_ui(name, request):
            return 0
        raise WallpaperError("Another UI process is starting but does not answer")
    try:
        return _run_locked(app, options, name)
    finally:
        lock.release()


def _wait_for_running_ui(name: str, request: ShowRequest) -> bool:
    deadline = time.monotonic() + SHOW_TIMEOUT_S
    while time.monotonic() < deadline:
        time.sleep(0.2)
        if show_in_running_ui(name, request):
            return True
    return False


def _run_locked(app: QApplication, options: UiOptions, name: str) -> int:
    config_path = options.config_path
    server = UiServer(name, config_path)
    server.listen()
    start_with = config_path or paths.config_file()  # if no app runs
    client = SocketClient(
        server_name(paths.data_dir()),
        config_path,
        start_app=lambda: start_detached(options.app_command(start_with)),
    )
    windows = Windows(client)
    server.show_requested.connect(windows.show)
    client.overview_changed.connect(server.follow)
    windows.all_closed.connect(app.quit)
    client.ended.connect(app.quit)  # the app quit: so do its windows

    log.info("UI started (config: %s)", config_path or "the app's")
    with quit_on_interrupt(app):
        windows.show(options.window)
        client.start()
        try:
            code = app.exec()
        finally:
            server.close()  # first: no new requests to a closing process
            client.close()
    log.info("UI stopped")
    return int(code)


def _application() -> QApplication:
    app = QCoreApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    if not isinstance(app, QApplication):
        raise WallpaperError("The windows need a QApplication")
    # `Windows.all_closed` decides; a closing dialog must not end the process.
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("wallpaper_py")
    return app
