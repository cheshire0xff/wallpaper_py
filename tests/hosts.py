"""The app's service thread and socket in the test process, and clients for it."""

from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path

from PySide6.QtNetwork import QLocalSocket
from pytestqt.qtbot import QtBot

from wallpaper_py.app.worker import ServiceHost, ServiceSettings
from wallpaper_py.core.backend import Backend

from .fakes import FakePresence


def make_host(
    config: Path, backend_factory: Callable[[], Backend], server_name: str
) -> ServiceHost:
    """Not started; ticks only at start and on request. Shut it down after."""
    return ServiceHost(
        ServiceSettings(
            config_path=config,
            backend_factory=backend_factory,
            presence_factory=FakePresence,
            thread_scope=nullcontext,
            tick_period=60.0,  # only the first tick and explicit ones
            server_name=server_name,
        )
    )


def connect(qtbot: QtBot, name: str, line: bytes) -> QLocalSocket:
    """
    Connect and send a line without waiting for the reply. Event driven (no
    waitFor*), so it can run on this thread, next to the server.
    """
    socket = QLocalSocket()
    with qtbot.waitSignal(socket.connected):
        socket.connectToServer(name)
    socket.write(line)
    return socket


def read_lines(qtbot: QtBot, socket: QLocalSocket, count: int) -> list[bytes]:
    """The next `count` lines (waits for them)."""
    data = bytearray()

    def complete() -> bool:
        data.extend(socket.readAll().data())
        return data.count(b"\n") >= count

    qtbot.waitUntil(complete)
    return bytes(data).splitlines()[:count]
