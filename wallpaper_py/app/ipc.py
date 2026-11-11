"""
Local sockets: the running app's command socket, and the line framing it
shares with the UI process's own small socket (app/ui/launcher.py).

`QLocalServer` is a named pipe on Windows and a Unix socket elsewhere,
readable only by the current user. One line of JSON each way per
connection (protocol.py, docs/protocol.md):

    -> {"v": 2, "request": {"command": "next", "target": "konachan"},
        "config": "/home/me/.config/wallpaper_py/config.toml"}
    <- {"command": "next", "playlist": "konachan", "change": {...}}

except `subscribe`, which keeps the connection open: the app then writes
one event per line until it quits.

Clients: `send()` blocks (the CLI; `None` means no app is running, and the
CLI then starts one); `LineRequest` is event driven (the tray and the UI
process, which must not block their event loop). The command server lives
on the app's service thread and runs each request there as it arrives.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, TypeAdapter, ValidationError
from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from ..core.model import WallpaperError
from ..protocol import (
    CommandRequest,
    Envelope,
    ErrorReply,
    Event,
    Reply,
    Subscribe,
)

log = logging.getLogger(__name__)

MAX_MESSAGE = 64 * 1024  # bytes, for a request; a request is ~200
MAX_REPLY = 16 * MAX_MESSAGE  # replies can be long (status)
MAX_BACKLOG = 4 * 1024 * 1024  # bytes unread by a subscriber before it is dropped
CONNECT_TIMEOUT_MS = 2_000
# A command waits for the tick in progress, then may render a 4K image or
# scan a 20k-image directory for the first time.
REPLY_TIMEOUT_S = 120.0
REQUEST_TIMEOUT_MS = 10_000  # a client that connects but never sends
_CONNECTION_ID = "connection_id"  # Qt property on a server's socket and timer

NOT_LISTENING = (
    QLocalSocket.LocalSocketError.ServerNotFoundError,
    QLocalSocket.LocalSocketError.ConnectionRefusedError,  # a stale socket
)


class ProtocolError(WallpaperError):
    """A malformed message, or one from a different version."""


def server_name(data_dir: Path, role: str = "") -> str:
    """
    Per user and per state directory, so two users (Windows pipe names are
    machine-wide) or two test runs never meet. `role` tells the app's
    socket ("") from the UI process's ("ui").
    """
    key = os.path.normcase(os.path.abspath(data_dir))
    name = f"wallpaper_py-{hashlib.sha1(key.encode()).hexdigest()[:16]}"
    return f"{name}-{role}" if role else name


# -- messages --------------------------------------------------------------------

_REPLY: TypeAdapter[Reply] = TypeAdapter(Reply)
# What a subscriber reads: events, or one error if the app refused.
_STREAMED: TypeAdapter[Event | ErrorReply] = TypeAdapter(Event | ErrorReply)


def encode(message: BaseModel) -> bytes:
    return message.model_dump_json().encode() + b"\n"


def decode_request(line: bytes) -> Envelope:
    try:
        return Envelope.model_validate_json(line)
    except ValidationError as err:
        raise ProtocolError(f"Malformed request: {brief(err)}") from err


def decode_reply(line: bytes) -> Reply:
    return _decode(_REPLY, line, "reply")


def decode_event(line: bytes) -> Event | ErrorReply:
    """A line of a subscription: an event, or the error that refused it."""
    return _decode(_STREAMED, line, "event")


def _decode[T](adapter: TypeAdapter[T], line: bytes, what: str) -> T:
    try:
        return adapter.validate_json(line)
    except ValidationError as err:
        raise ProtocolError(f"Malformed {what} from the app: {brief(err)}") from err


def brief(err: ValidationError) -> str:
    """The first problem, in one line (pydantic's full text is multi-line)."""
    first = err.errors()[0]
    where = ".".join(str(part) for part in first["loc"])
    return f"{where}: {first['msg']}" if where else str(first["msg"])


def same_file(a: Path, b: Path) -> bool:
    return os.path.normcase(a.resolve()) == os.path.normcase(b.resolve())


# -- blocking client -------------------------------------------------------------


def send(
    name: str, envelope: Envelope, timeout: float = REPLY_TIMEOUT_S
) -> Reply | None:
    """
    Run a command in the running app and return its reply; None if no app
    is running. Raises WallpaperError if an app is there but doesn't answer
    (the caller must then *not* act on its own: the app may be mid-change).

    Call it from a process that is not serving the socket itself: PySide
    keeps the GIL while it waits, which would starve the server's thread.
    """
    line = request_line(name, encode(envelope), timeout, MAX_REPLY)
    return None if line is None else decode_reply(line)


def request_line(
    name: str,
    line: bytes,
    timeout: float,
    limit: int = MAX_MESSAGE,
    peer: str = "the running app",
) -> bytes | None:
    """
    One line out, one line back, blocking; None if nothing listens on
    `name`. `peer` names the other side in errors.
    """
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
        if socket.error() in NOT_LISTENING:
            return None
        raise WallpaperError(f"Cannot reach {peer}: {socket.errorString()}")
    try:
        socket.write(line)
        # Without an event loop, only this pushes the bytes out. (It is
        # False, harmlessly, when the write already completed.)
        written = socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
        if not written and socket.bytesToWrite():
            raise WallpaperError(f"Cannot send to {peer}: {socket.errorString()}")
        return _read_line(socket, time.monotonic() + timeout, limit, peer)
    finally:
        socket.abort()


def _read_line(socket: QLocalSocket, deadline: float, limit: int, peer: str) -> bytes:
    peer = peer[0].upper() + peer[1:]  # starts the sentences below
    buffer = bytearray()
    while b"\n" not in buffer:
        left_ms = int((deadline - time.monotonic()) * 1000)
        if left_ms <= 0:
            raise WallpaperError(f"{peer} did not answer in time")
        ready = socket.waitForReadyRead(left_ms)
        buffer += socket.readAll().data()
        if not ready and b"\n" not in buffer:
            if socket.state() != QLocalSocket.LocalSocketState.ConnectedState:
                raise WallpaperError(f"{peer} closed the connection")
        if len(buffer) > limit:
            raise ProtocolError("Reply too long")
    return bytes(buffer.split(b"\n", 1)[0])


# -- event-driven client ---------------------------------------------------------


class LineRequest(QObject):
    """
    One line out, one line back, without blocking the event loop. Exactly
    one callback runs: `on_answer(line)`, or `on_failure(not_running,
    message)` where `not_running` means nothing listens on `name`.

    Owned by `parent` (Qt, not Python: nobody needs to keep a reference),
    and deletes itself when done.
    """

    # pylint: disable=too-many-arguments,too-many-instance-attributes
    def __init__(
        self,
        name: str,
        line: bytes,
        on_answer: Callable[[bytes], None],
        on_failure: Callable[[bool, str], None],
        *,
        parent: QObject,
        timeout_s: float = REPLY_TIMEOUT_S,
        limit: int = MAX_REPLY,
    ) -> None:
        super().__init__(parent)
        self._line = line
        self._on_answer = on_answer
        self._on_failure = on_failure
        self._limit = limit
        self._buffer = bytearray()
        self._done = False
        self._socket = QLocalSocket(self)
        self._socket.connected.connect(self._send)
        self._socket.readyRead.connect(self._ready_read)
        self._socket.errorOccurred.connect(self._error)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timed_out)
        self._timer.start(round(timeout_s * 1000))
        self._socket.connectToServer(name)

    @Slot()
    def _send(self) -> None:
        self._socket.write(self._line)

    @Slot()
    def _ready_read(self) -> None:
        self._read()

    def _read(self) -> bool:
        """True once it is done (answered, or failed on a too long reply)."""
        if self._done:
            return True
        self._buffer += self._socket.readAll().data()
        if b"\n" in self._buffer:
            self._finish()
            self._on_answer(bytes(self._buffer.split(b"\n", 1)[0]))
        elif len(self._buffer) > self._limit:
            self._fail(False, "Reply too long")
        return self._done

    @Slot(QLocalSocket.LocalSocketError)
    def _error(self, error: QLocalSocket.LocalSocketError) -> None:
        # The answer may have arrived just before the peer closed.
        if self._read():
            return
        self._fail(error in NOT_LISTENING, self._socket.errorString())

    @Slot()
    def _timed_out(self) -> None:
        self._fail(False, "The running app did not answer in time")

    def _fail(self, not_running: bool, message: str) -> None:
        if self._done:
            return
        self._finish()
        self._on_failure(not_running, message)

    def _finish(self) -> None:
        self._done = True
        self._timer.stop()
        self._socket.abort()
        self.deleteLater()


# -- servers ---------------------------------------------------------------------


class LineServer(QObject):
    """
    Accepts connections and reads one line from each, which `_received`
    handles; it answers with `finish()` (one line, then close) or keeps the
    connection and `send()`s more. Never blocks: it may share its thread
    with the playlist service.

    Its slots are bound methods that find the connection from `sender()`,
    never lambdas holding `self`: Qt would keep such a lambda (and so this
    object) alive until a socket is deleted, and freeing this object from
    inside that deletion deletes the socket twice.
    """

    def __init__(self, name: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.name = name
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._accept)
        self._clients: dict[int, tuple[QLocalSocket, bytearray]] = {}
        self._next_id = 0

    def listen(self) -> None:
        """Only while holding the instance lock: it removes a stale socket."""
        QLocalServer.removeServer(self.name)
        if not self._server.listen(self.name):
            raise WallpaperError(
                f"Cannot open the socket {self.name}: {self._server.errorString()}"
            )

    def close(self) -> None:
        self._server.close()
        for socket, _ in list(self._clients.values()):
            socket.abort()
        self._clients.clear()

    def send(self, connection_id: int, line: bytes) -> bool:
        """
        Write a line and keep the connection; False if it is gone. A client
        that stops reading is dropped rather than buffered without end.
        """
        client = self._clients.get(connection_id)
        if client is None:
            return False
        socket, _ = client
        if socket.bytesToWrite() > MAX_BACKLOG:
            log.warning("Dropping a client that stopped reading")
            self._forget(connection_id)
            socket.abort()
            return False
        socket.write(line)
        return True

    def finish(self, connection_id: int, line: bytes) -> None:
        """Write the last line, then close once it is out."""
        client = self._clients.pop(connection_id, None)
        if client is None:
            return  # the client gave up meanwhile
        socket, _ = client
        socket.write(line)
        socket.disconnectFromServer()  # after the line is written

    def _received(self, connection_id: int, line: bytes) -> None:
        raise NotImplementedError

    def _too_long(self) -> bytes:
        """The answer to a request over MAX_MESSAGE bytes."""
        raise NotImplementedError

    @Slot()
    def _accept(self) -> None:
        while (socket := self._server.nextPendingConnection()) is not None:
            connection_id = self._next_id
            self._next_id += 1
            self._clients[connection_id] = (socket, bytearray())
            socket.setProperty(_CONNECTION_ID, connection_id)
            socket.readyRead.connect(self._ready_read)
            socket.disconnected.connect(socket.deleteLater)
            socket.disconnected.connect(self._disconnected)
            expiry = QTimer(socket)  # deleted with its socket
            expiry.setProperty(_CONNECTION_ID, connection_id)
            expiry.setSingleShot(True)
            expiry.timeout.connect(self._expired)
            expiry.start(REQUEST_TIMEOUT_MS)
            if socket.bytesAvailable():  # arrived before readyRead was connected
                self._read(connection_id)

    def _sender_id(self) -> int:
        return int(self.sender().property(_CONNECTION_ID))

    @Slot()
    def _ready_read(self) -> None:
        self._read(self._sender_id())

    @Slot()
    def _disconnected(self) -> None:
        self._forget(self._sender_id())

    @Slot()
    def _expired(self) -> None:
        self._expire(self._sender_id())

    def _read(self, connection_id: int) -> None:
        client = self._clients.get(connection_id)
        if client is None:
            return
        socket, buffer = client
        buffer += socket.readAll().data()
        if b"\n" not in buffer:
            if len(buffer) > MAX_MESSAGE:
                self.finish(connection_id, self._too_long())
            return
        # One request per connection. (Only our own connection: Qt may
        # have its own on the socket.)
        socket.readyRead.disconnect(self._ready_read)
        self._received(connection_id, bytes(buffer.split(b"\n", 1)[0]))

    def _expire(self, connection_id: int) -> None:
        client = self._clients.get(connection_id)
        if client is not None and b"\n" not in client[1]:  # still no request
            self._forget(connection_id)
            client[0].abort()

    def _forget(self, connection_id: int) -> None:
        self._clients.pop(connection_id, None)


class CommandServer(LineServer):
    """
    The app's socket. Emits `received(connection_id, CommandRequest)`; the owner
    answers with `reply(connection_id, Reply)`. A `subscribe` request is
    announced with `subscribed(connection_id)` and the connection stays
    open for `send_event()` / `broadcast()`. Malformed requests, other
    versions and requests for a different config file are answered here.
    """

    received = Signal(int, object)  # connection id, protocol.CommandRequest
    subscribed = Signal(int)  # connection id

    def __init__(
        self, name: str, config_path: Path, parent: QObject | None = None
    ) -> None:
        super().__init__(name, parent)
        self._config_path = config_path
        self._subscribers: set[int] = set()

    def reply(self, connection_id: int, reply: Reply) -> None:
        self.finish(connection_id, encode(reply))

    def send_event(self, connection_id: int, event: Event) -> None:
        if not self.send(connection_id, encode(event)):
            self._subscribers.discard(connection_id)

    def broadcast(self, event: Event) -> None:
        """To every subscriber."""
        line = encode(event)
        for connection_id in list(self._subscribers):
            if not self.send(connection_id, line):
                self._subscribers.discard(connection_id)

    def close(self) -> None:
        super().close()
        self._subscribers.clear()

    def _received(self, connection_id: int, line: bytes) -> None:
        try:
            envelope = decode_request(line)
        except ProtocolError as err:
            log.warning("Command socket: %s", err)
            self.reply(connection_id, ErrorReply.make("protocol", err))
            return
        if not same_file(envelope.config, self._config_path):
            message = (
                f"The running app uses {self._config_path}, not "
                f"{envelope.config}; quit it to use another config"
            )
            self.reply(connection_id, ErrorReply.make("wrong_config", message))
            return
        request: CommandRequest | Subscribe = envelope.request
        if isinstance(request, Subscribe):
            self._subscribers.add(connection_id)
            self.subscribed.emit(connection_id)
        else:
            self.received.emit(connection_id, request)

    def _too_long(self) -> bytes:
        return encode(ErrorReply.make("protocol", "Request too long"))

    def _forget(self, connection_id: int) -> None:
        super()._forget(connection_id)
        self._subscribers.discard(connection_id)
