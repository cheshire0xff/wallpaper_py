"""
The window process: `wallpaper ui [home|library|settings]`.

Started on demand (by the tray or the CLI) and gone when its window
closes, so the window's memory (the grids' thumbnails) is given back. It is
a client of the daemon's socket like the CLI, starting the daemon if none
runs, and it quits when the daemon does (its subscription ends).

The daemon decides which process has the window: each subscribes as the
window, on its page (`Subscribe.window`), and the first one wins; a later
one has its page shown in that window and exits. So the window waits for
that answer (milliseconds) before showing; it shows at once, with
"Connecting…", when it has to start the daemon, and after SHOW_ANYWAY_MS
if the daemon is slow to answer (busy, or stuck). Shown and then refused
(two started the daemon at once), it quits.

Closing the window ends the subscription at once, so from then on the
daemon knows of no window; the process quits once its commands in flight
have their replies.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QCoreApplication, QObject, Qt, QTimer, Slot
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from ...backends import allow_foreground
from ...core import paths
from ...core.model import WallpaperError
from ...protocol import PageName
from ..client import SocketClient
from ..icon import app_icon
from ..ipc import server_name
from ..processes import autostart_command, quit_on_interrupt, start_detached
from .window import MainWindow

log = logging.getLogger(__name__)

# Milliseconds: a daemon that hasn't answered the subscription by then gets
# the window shown anyway, "Connecting…".
SHOW_ANYWAY_MS = 2_000


@dataclass(frozen=True)
class UiOptions:
    page: PageName = "home"
    # How to start the daemon if it isn't running; injectable for tests.
    daemon_command: Callable[[], list[str]] = autostart_command


def run_ui(options: UiOptions) -> int:
    """Show the window on the page here, or in the window process that has it."""
    # Started by a click or from a terminal: let the process that has the
    # window come to the front, if it is another one.
    allow_foreground()
    app = _application()
    client = SocketClient(
        server_name(paths.data_dir()),
        start_daemon=lambda: start_detached(options.daemon_command()),
        window=options.page,
    )
    _WindowProcess(app, client, options.page)

    log.info("UI started")
    with quit_on_interrupt(app):
        client.start()
        try:
            code = app.exec()
        finally:
            client.close()
    log.info("UI stopped")
    return int(code)


class _WindowProcess(QObject):
    """The window's life here: shown when the daemon says so, then closing."""

    def __init__(self, app: QApplication, client: SocketClient, page: PageName):
        super().__init__(client)
        self._app = app
        self._client = client
        self._page = page
        self._window: MainWindow | None = None
        client.accepted.connect(self._show)
        client.daemon_started.connect(self._show)  # "Connecting…" meanwhile
        client.failed.connect(self._failed)
        client.shown_elsewhere.connect(self._shown_elsewhere)
        client.show_requested.connect(self._show_page)
        client.ended.connect(app.quit)  # the daemon quit: so does its window
        self._show_anyway = QTimer(self)
        self._show_anyway.setSingleShot(True)
        self._show_anyway.timeout.connect(self._show)
        self._show_anyway.start(SHOW_ANYWAY_MS)

    @Slot()
    def _show(self) -> None:
        self._show_anyway.stop()
        if self._window is not None:
            return
        window = MainWindow(self._client)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.closed.connect(self._closed)
        self._app.aboutToQuit.connect(window.shutdown)  # its threads, if still open
        self._window = window
        window.open_page(self._page)

    @Slot(str)
    def _failed(self, message: str) -> None:
        """Nothing more comes from the daemon: the window says why."""
        if self._window is None:
            self._show()
            assert self._window is not None
            self._window.show_failure(message)  # it missed the signal

    @Slot(str)
    def _show_page(self, page: str) -> None:
        if self._window is not None:
            log.info("Showing %s", page)
            self._window.open_page(page)

    @Slot()
    def _shown_elsewhere(self) -> None:
        self._show_anyway.stop()
        # Quit, not close: a window shown meanwhile ("Connecting…") must not
        # ask to save (closeEvent); aboutToQuit shuts it down.
        self._app.quit()

    @Slot()
    def _closed(self) -> None:
        self._client.close()  # the daemon knows of no window from now on
        if self._client.busy():
            log.info("Window closed; quitting once the daemon has answered")
            self._client.idle.connect(self._app.quit)
        else:
            self._app.quit()


BODY_POINTS = 10.5  # Windows 11's body text (14 px); a smaller system font grows
# Windows 11's own font, in place of the system's "Segoe UI": the same
# letters, but with every weight (Segoe UI has no Medium).
WINDOWS_FONT = ("Segoe UI", "Segoe UI Variable Text")


def body_font(system: QFont) -> QFont:
    """
    The window process's font: the system's, at least BODY_POINTS (Qt takes
    9 pt on Windows, smaller than Windows 11's own body text).
    """
    font = QFont(system)
    if 0 < font.pointSizeF() < BODY_POINTS:
        font.setPointSizeF(BODY_POINTS)
    system_family, windows_family = WINDOWS_FONT
    if font.family() == system_family and windows_family in QFontDatabase.families():
        font.setFamily(windows_family)
    return font


def _application() -> QApplication:
    app = QCoreApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    if not isinstance(app, QApplication):
        raise WallpaperError("The windows need a QApplication")
    # `MainWindow.closed` decides; a closing dialog must not end the process.
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("wallpaper_py")
    app.setWindowIcon(app_icon())
    app.setFont(body_font(app.font()))
    return app
