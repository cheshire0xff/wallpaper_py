"""
The UI process: `wallpaper ui [home|library|settings]`.

Started on demand (by the tray or the CLI) and gone when its window
closes, so the window's memory (later: thumbnails) is given back. At most
one runs: a start first asks a running one to show its window on the page
and exits.
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

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication

from ...core import paths
from ...core.model import WallpaperError
from ..instance import InstanceLock
from ..ipc import server_name
from ..processes import autostart_command, quit_on_interrupt, start_detached
from . import theme
from .client import SocketClient
from .launcher import (
    SHOW_TIMEOUT_S,
    PageName,
    ShowRequest,
    UiServer,
    show_in_running_ui,
    ui_server_name,
)
from .window import MainWindow

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UiOptions:
    config_path: Path | None  # --config, if given; None: the app's
    page: PageName = "home"
    # How to start the app if it isn't running; injectable for tests.
    app_command: Callable[[Path], list[str]] = autostart_command


def run_ui(options: UiOptions) -> int:
    """Show the window on the page here, or in the UI process that runs."""
    name = ui_server_name(paths.data_dir())
    request = ShowRequest(page=options.page, config=options.config_path)
    if show_in_running_ui(name, request):
        return 0
    app = _application()
    lock = InstanceLock(paths.ui_lock())
    if not lock.acquire():
        # Another UI process is starting right now: let it show the page.
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
    window = MainWindow(client)
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    server.show_requested.connect(window.open_page)
    client.overview_changed.connect(server.follow)
    window.closed.connect(app.quit)
    client.ended.connect(app.quit)  # the app quit: so does its window

    log.info("UI started (config: %s)", config_path or "the app's")
    with quit_on_interrupt(app):
        window.open_page(options.page)
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
    # `MainWindow.closed` decides; a closing dialog must not end the process.
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("wallpaper_py")
    app.setFont(theme.body_font(app.font()))
    return app
