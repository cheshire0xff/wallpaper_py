"""
How windows talk to the app: `Client`, the only thing they may use.

    client.run(request, on_reply)   a command; its Reply comes back later
    client.overview_changed         the latest Overview (also client.overview)
    client.changed                  a wallpaper changed (a Change)
    client.failed                   the app can't be reached; nothing more comes

Windows never see the service, `ServiceWorker` or a socket. `SocketClient`
implements it over the app's socket for the UI process: it subscribes (and
starts the app first if none runs, like the CLI), and runs each command on
a connection of its own. Everything is event driven: the UI thread never
waits.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtNetwork import QLocalSocket

from ...core import paths
from ...protocol import (
    ChangedEvent,
    CommandRequest,
    Envelope,
    ErrorReply,
    Overview,
    OverviewChangedEvent,
    Reply,
    Subscribe,
)
from .. import ipc

log = logging.getLogger(__name__)

ReplyCallback = Callable[[Reply], None]

START_TIMEOUT_S = 10.0  # for a started app to answer
RETRY_INTERVAL_MS = 200


class Client(QObject):
    """The narrow interface windows depend on (see the module docstring)."""

    overview_changed = Signal(object)  # protocol.Overview
    changed = Signal(object)  # core.service.Change
    failed = Signal(str)  # message

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.overview: Overview | None = None  # None until the first arrives

    def run(self, request: CommandRequest, on_reply: ReplyCallback) -> None:
        """Run `request` in the app; `on_reply` gets the reply (this thread)."""
        raise NotImplementedError

    def _set_overview(self, overview: Overview) -> None:
        self.overview = overview
        self.overview_changed.emit(overview)


class SocketClient(Client):
    """
    `Client` over the app's socket. `start()` subscribes; if no app runs,
    it calls `start_app` once and retries for START_TIMEOUT_S. `ended`
    fires when an established subscription closes (the app quit).
    """

    # pylint: disable=too-many-instance-attributes

    ended = Signal()

    def __init__(
        self,
        name: str,
        config_path: Path,
        start_app: Callable[[], None] | None,
        *,
        start_timeout_s: float = START_TIMEOUT_S,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._name = name
        self._config_path = config_path
        self._start_app = start_app
        self._start_timeout_s = start_timeout_s
        self._deadline: float | None = None  # set once the app was started
        self._socket: QLocalSocket | None = None
        self._buffer = bytearray()
        self._subscribed = False
        self._over = False  # failed, ended or closed: nothing more happens

    def start(self) -> None:
        self._connect()

    def close(self) -> None:
        self._over = True
        self._drop_socket()

    def run(self, request: CommandRequest, on_reply: ReplyCallback) -> None:
        def answered(line: bytes) -> None:
            try:
                reply = ipc.decode_reply(line)
            except ipc.ProtocolError as err:
                reply = ErrorReply.make("protocol", err)
            on_reply(reply)

        def failed(not_running: bool, message: str) -> None:
            if not_running:
                message = "wallpaper_py is not running"
            on_reply(ErrorReply.make("unreachable", message))

        envelope = Envelope(request=request, config=self._config_path)
        ipc.LineRequest(self._name, ipc.encode(envelope), answered, failed, parent=self)

    # -- the subscription ------------------------------------------------------

    # Slots are bound methods (no lambdas holding `self`, see ipc.LineServer).
    # A socket replaced by a retry may still signal, so each slot first
    # checks that the sender is the current socket.

    @Slot()
    def _connect(self) -> None:
        if self._over:
            return
        socket = QLocalSocket(self)
        self._socket = socket
        self._buffer.clear()
        socket.connected.connect(self._subscribe)
        socket.readyRead.connect(self._ready_read)
        socket.errorOccurred.connect(self._error)
        socket.disconnected.connect(self._disconnected)
        socket.connectToServer(self._name)

    def _current(self) -> QLocalSocket | None:
        """The socket that sent the signal, if it is still the current one."""
        socket = self._socket
        return socket if socket is not None and self.sender() is socket else None

    @Slot()
    def _subscribe(self) -> None:
        if (socket := self._current()) is not None:
            envelope = Envelope(request=Subscribe(), config=self._config_path)
            socket.write(ipc.encode(envelope))

    @Slot()
    def _ready_read(self) -> None:
        if (socket := self._current()) is not None:
            self._read(socket)

    @Slot()
    def _disconnected(self) -> None:
        if (socket := self._current()) is not None:
            self._end(socket)

    def _read(self, socket: QLocalSocket) -> None:
        self._buffer += socket.readAll().data()
        while not self._over and b"\n" in self._buffer:
            line, _, rest = self._buffer.partition(b"\n")
            self._buffer = bytearray(rest)
            try:
                message = ipc.decode_event(bytes(line))
            except ipc.ProtocolError as err:  # e.g. an event added later
                log.info("Skipping a line from the app: %s", err)
                continue
            if isinstance(message, ErrorReply):  # the app refused
                self._fail(message.error.message)
                return
            if not self._subscribed:
                self._subscribed = True
                log.info("Connected to the app")
            if isinstance(message, OverviewChangedEvent):
                self._set_overview(message.overview)
            elif isinstance(message, ChangedEvent):
                self.changed.emit(message.change)

    @Slot(QLocalSocket.LocalSocketError)
    def _error(self, error: QLocalSocket.LocalSocketError) -> None:
        socket = self._current()
        if socket is None or self._over:
            return
        self._read(socket)  # a refusal may have arrived just before
        if self._over:
            return
        if self._subscribed:
            self._end(socket)
        elif error in ipc.NOT_LISTENING:
            self._drop_socket()
            self._retry()
        else:
            self._fail(f"Cannot reach wallpaper_py: {socket.errorString()}")

    def _end(self, socket: QLocalSocket) -> None:
        if self._over or not self._subscribed:
            return
        self._read(socket)
        log.info("The app closed the connection")
        self.close()
        self.ended.emit()

    def _retry(self) -> None:
        if self._deadline is None:
            if self._start_app is None:
                self._fail("wallpaper_py is not running")
                return
            try:
                self._start_app()
            except OSError as err:
                self._fail(f"Cannot start wallpaper_py: {err}")
                return
            self._deadline = time.monotonic() + self._start_timeout_s
        elif time.monotonic() >= self._deadline:
            self._fail(f"could not start wallpaper_py; see {paths.log_file()}")
            return
        QTimer.singleShot(RETRY_INTERVAL_MS, self, self._connect)

    def _fail(self, message: str) -> None:
        log.error("%s", message)
        self.close()
        self.failed.emit(message)

    def _drop_socket(self) -> None:
        socket, self._socket = self._socket, None
        if socket is not None:
            socket.abort()
            socket.deleteLater()
