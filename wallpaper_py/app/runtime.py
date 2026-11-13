"""
The app: the one process that rotates the wallpapers and serves commands.

`wallpaper tray` runs it with the tray icon (that is what starts at login);
`wallpaper run` runs it headless in a terminal; the UI process starts it
(as `tray`) when it finds none running. Either way it holds the instance
lock and runs the playlist service on its own thread, together
with the command socket, so commands and ticks never overlap. The UI thread
holds only the tray; the windows run in a process of their own
(app/ui/process.py), which the tray starts on demand.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from .. import backends
from ..core import paths
from ..core.autostart import Autostart
from ..core.backend import Backend
from ..core.model import WallpaperError
from ..core.presence import Presence
from .icon import app_icon
from .instance import InstanceLock
from .ipc import server_name
from .processes import autostart_command, quit_on_interrupt, ui_command
from .screens import ScreenWatcher
from .tray import Tray
from .ui.launcher import WindowOpener, ui_server_name
from .worker import ServiceHost, ServiceSettings

log = logging.getLogger(__name__)

SHUTDOWN_TIMEOUT_MS = 10_000
# A CLI command run while no app was running holds the lock for a moment
# (cli.py): wait it out, or login's start could find it held and give up.
LOCK_WAIT_S = 5.0


@dataclass(frozen=True)
class AppOptions:
    config_path: Path
    tray: bool
    tick_period: float = 5.0
    quit_after_ticks: int | None = None  # for tests
    lock_wait_s: float = LOCK_WAIT_S


@dataclass(frozen=True)
class Platform:
    """The platform pieces the app uses; injectable for tests."""

    backend_factory: Callable[[], Backend]
    presence_factory: Callable[[], Presence]
    autostart_factory: Callable[[], Autostart] = backends.get_autostart
    thread_scope: Callable[[], AbstractContextManager[None]] = backends.thread_scope


def run_app(options: AppOptions, platform: Platform) -> int:
    """Run until quit (tray menu, Ctrl+C, or `quit_after_ticks`)."""
    app = _application(options.tray)
    lock = InstanceLock(paths.instance_lock())
    if not lock.acquire(options.lock_wait_s):
        raise WallpaperError(
            "wallpaper_py is already running; quit it first (tray menu: Quit)"
        )
    try:
        return _run_locked(app, options, platform)
    finally:
        lock.release()


def _run_locked(app: QCoreApplication, options: AppOptions, platform: Platform) -> int:
    socket_name = server_name(paths.data_dir())
    host = ServiceHost(
        ServiceSettings(
            config_path=options.config_path,
            backend_factory=platform.backend_factory,
            presence_factory=platform.presence_factory,
            thread_scope=platform.thread_scope,
            tick_period=options.tick_period,
            server_name=socket_name,
        )
    )
    failures: list[str] = []

    def failed(error: str) -> None:
        failures.append(error)
        app.exit(1)

    host.failed.connect(failed)

    tray: Tray | None = None
    if options.tray:
        opener = WindowOpener(
            ui_server_name(paths.data_dir()),
            options.config_path,
            partial(ui_command, options.config_path),
        )
        tray = Tray(
            host,
            platform.autostart_factory(),
            autostart_command(options.config_path),
            options.config_path.parent,
            app.quit,
            open_window=opener.open,
        )
        opener.failed.connect(tray.notify)
    watcher = ScreenWatcher(app) if isinstance(app, QGuiApplication) else None
    if watcher is not None:
        watcher.settled.connect(host.request_tick)
    if options.quit_after_ticks is not None:
        host.ticked.connect(_countdown(options.quit_after_ticks, app.quit))

    log.info("Started (config: %s, socket: %s)", options.config_path, socket_name)
    with quit_on_interrupt(app):
        host.start()
        if tray is not None:
            tray.show()
        try:
            code = app.exec()
        finally:
            if tray is not None:
                tray.hide()
            log.info("Stopping the playlist service")
            stopped = host.shutdown(SHUTDOWN_TIMEOUT_MS)
    if not stopped:
        # Destroying a running QThread aborts the process anyway; say why.
        log.error("The playlist service did not stop in time; exiting")
        logging.shutdown()
        os._exit(1)
    log.info("Stopped")
    if failures:
        raise WallpaperError(failures[0])
    return int(code)


def _application(tray: bool) -> QCoreApplication:
    app = QCoreApplication.instance()
    if app is None:
        argv = sys.argv[:1]
        app = QApplication(argv) if tray else QCoreApplication(argv)
    if tray:
        if not isinstance(app, QApplication):
            raise WallpaperError("The tray needs a QApplication")
        app.setQuitOnLastWindowClosed(False)  # later windows only hide
        app.setWindowIcon(app_icon())
    app.setApplicationName("wallpaper_py")
    return app


def _countdown(ticks: int, done: Callable[[], None]) -> Callable[[], None]:
    left = ticks

    def count() -> None:
        nonlocal left
        left -= 1
        if left == 0:
            done()

    return count
