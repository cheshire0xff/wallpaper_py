"""
The app: the one process that rotates the wallpapers and serves commands.

`wallpaper tray` runs it with the tray icon (that is what starts at login);
`wallpaper run` runs it headless in a terminal. Either way it holds the
instance lock, listens on the command socket and runs the playlist service
on its own thread, so CLI commands and ticks never overlap.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from .. import backends
from ..commands import Request
from ..core import paths
from ..core.autostart import Autostart
from ..core.backend import Backend
from ..core.model import WallpaperError
from ..core.presence import Presence
from .instance import InstanceLock
from .ipc import CommandServer, server_name
from .screens import ScreenWatcher
from .tray import Tray
from .worker import ServiceHost, ServiceSettings

log = logging.getLogger(__name__)

SHUTDOWN_TIMEOUT_MS = 10_000


@dataclass(frozen=True)
class AppOptions:
    config_path: Path
    tray: bool
    tick_period: float = 5.0
    quit_after_ticks: int | None = None  # for tests


@dataclass(frozen=True)
class Platform:
    """The platform pieces the app uses; injectable for tests."""

    backend_factory: Callable[[], Backend]
    presence_factory: Callable[[], Presence]
    autostart_factory: Callable[[], Autostart] = backends.get_autostart
    thread_scope: Callable[[], AbstractContextManager[None]] = backends.thread_scope


def setup_logging(to_file: bool) -> None:
    """Terminal (if there is one) and, for the tray app, a rotating file."""
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:  # None under pythonw
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        handlers.append(stream)
    if to_file:
        log_file = paths.log_file()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file = RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        handlers.append(file)
    # A no-op if logging is already set up (e.g. by an embedding test run).
    logging.basicConfig(level=logging.INFO, handlers=handlers)


def autostart_command(config_path: Path) -> list[str]:
    """How login starts the tray app: this interpreter, windowless on Windows."""
    python = Path(sys.executable)
    if sys.platform == "win32" and python.name.lower() == "python.exe":
        windowless = python.with_name("pythonw.exe")
        if windowless.is_file():
            python = windowless
    command = [str(python), "-m", "wallpaper_py"]
    if config_path != paths.config_file():
        command += ["--config", str(config_path.resolve())]
    return [*command, "tray"]


def run_app(options: AppOptions, platform: Platform) -> int:
    """Run until quit (tray menu, Ctrl+C, or `quit_after_ticks`)."""
    app = _application(options.tray)
    lock = InstanceLock(paths.instance_lock())
    if not lock.acquire():
        raise WallpaperError(
            "wallpaper_py is already running; quit it first (tray menu: Quit)"
        )
    try:
        return _run_locked(app, options, platform)
    finally:
        lock.release()


def _run_locked(app: QCoreApplication, options: AppOptions, platform: Platform) -> int:
    host = ServiceHost(
        ServiceSettings(
            config_path=options.config_path,
            backend_factory=platform.backend_factory,
            presence_factory=platform.presence_factory,
            thread_scope=platform.thread_scope,
            tick_period=options.tick_period,
        )
    )
    server = CommandServer(server_name(paths.data_dir()), options.config_path)

    def received(connection_id: int, request: Request) -> None:
        host.submit(request, lambda reply: server.reply(connection_id, reply))

    server.received.connect(received)
    server.listen()

    tray: Tray | None = None
    if options.tray:
        tray = Tray(
            host,
            platform.autostart_factory(),
            autostart_command(options.config_path),
            options.config_path.parent,
            app.quit,
        )
    watcher = ScreenWatcher(app) if isinstance(app, QGuiApplication) else None
    if watcher is not None:
        watcher.settled.connect(host.request_tick)
    if options.quit_after_ticks is not None:
        host.ticked.connect(_countdown(options.quit_after_ticks, app.quit))

    log.info("Started (config: %s, socket: %s)", options.config_path, server.name)
    with _quit_on_interrupt(app):
        host.start()
        if tray is not None:
            tray.show()
        try:
            code = app.exec()
        finally:
            if tray is not None:
                tray.hide()
            server.close()
            stopped = host.shutdown(SHUTDOWN_TIMEOUT_MS)
    if not stopped:
        # Destroying a running QThread aborts the process anyway; say why.
        log.error("The playlist service did not stop in time; exiting")
        logging.shutdown()
        os._exit(1)
    log.info("Stopped")
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


@contextmanager
def _quit_on_interrupt(app: QCoreApplication) -> Iterator[None]:
    """
    Ctrl+C quits cleanly. Python only runs signal handlers between
    bytecodes, so a timer wakes the interpreter while Qt's loop waits.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.signal(signal.SIGINT, lambda *_: app.quit())
    wake = QTimer()
    wake.timeout.connect(lambda: None)
    wake.start(250)
    try:
        yield
    finally:
        wake.stop()
        signal.signal(signal.SIGINT, previous)
