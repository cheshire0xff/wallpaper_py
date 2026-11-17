"""The daemon's service thread and socket in the test process, and clients for it."""

from collections.abc import Callable
from contextlib import nullcontext

from PySide6.QtNetwork import QLocalSocket
from pytestqt.qtbot import QtBot

from wallpaper_py.app.worker import ServiceSettings, ServiceThread
from wallpaper_py.core.backend import Backend

from .fakes import FakePresence, FakeSound


def make_service_thread(
    backend_factory: Callable[[], Backend],
    server_name: str,
    sound: FakeSound | None = None,
) -> ServiceThread:
    """Not started; ticks only at start and on request. Shut it down after."""
    return ServiceThread(
        ServiceSettings(
            backend_factory=backend_factory,
            presence_factory=FakePresence,
            thread_scope=nullcontext,
            play_sound=sound or FakeSound(),
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
