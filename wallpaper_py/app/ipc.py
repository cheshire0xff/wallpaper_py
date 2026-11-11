"""
The running app's command socket: `QLocalServer`, a named pipe on Windows
and a Unix socket elsewhere, readable only by the current user.

One request per connection, one line of JSON each way (pydantic models):

    -> {"v": 1, "request": {"command": "next", "target": "konachan"},
        "config": "/home/me/.config/wallpaper_py/config.toml"}
    <- {"code": 0, "out": "konachan: 123.jpg on AW3225QF", "err": ""}

The CLI uses `send()` (blocking); `None` means no app is running, and the
CLI then starts one. The server lives on the app's service thread and runs
each request there as it arrives.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    TypeAdapter,
    ValidationError,
    field_validator,
)
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from ..commands import Reply, Request
from ..core.model import WallpaperError

log = logging.getLogger(__name__)

PROTOCOL = 1
MAX_MESSAGE = 64 * 1024  # bytes; a request is ~200
CONNECT_TIMEOUT_MS = 2_000
# A command waits for the tick in progress, then may render a 4K image or
# scan a 20k-image directory for the first time.
REPLY_TIMEOUT_S = 120.0
REQUEST_TIMEOUT_MS = 10_000  # a client that connects but never sends


class ProtocolError(WallpaperError):
    """A malformed message, or one from a different version."""


def server_name(data_dir: Path) -> str:
    """
    Per user and per state directory, so two users (Windows pipe names are
    machine-wide) or two test runs never meet.
    """
    key = os.path.normcase(os.path.abspath(data_dir))
    return f"wallpaper_py-{hashlib.sha1(key.encode()).hexdigest()[:16]}"


class Envelope(BaseModel):
    """A request on the wire."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    v: int = PROTOCOL
    request: Request
    config: Path  # the config file the sender means

    @field_validator("v")
    @classmethod
    def _same_protocol(cls, version: int) -> int:
        if version != PROTOCOL:
            raise ValueError(
                f"the running app speaks protocol {PROTOCOL}, not {version}; "
                f"restart it after updating"
            )
        return version


_REPLY = TypeAdapter(Reply)


def encode_request(envelope: Envelope) -> bytes:
    return envelope.model_dump_json().encode() + b"\n"


def decode_request(line: bytes) -> Envelope:
    try:
        return Envelope.model_validate_json(line)
    except ValidationError as err:
        raise ProtocolError(f"Malformed request: {_brief(err)}") from err


def encode_reply(reply: Reply) -> bytes:
    return _REPLY.dump_json(reply) + b"\n"


def decode_reply(line: bytes) -> Reply:
    try:
        return _REPLY.validate_json(line)
    except ValidationError as err:
        raise ProtocolError(f"Malformed reply from the app: {_brief(err)}") from err


def _brief(err: ValidationError) -> str:
    """The first problem, in one line (pydantic's full text is multi-line)."""
    first = err.errors()[0]
    where = ".".join(str(part) for part in first["loc"])
    return f"{where}: {first['msg']}" if where else str(first["msg"])


def same_file(a: Path, b: Path) -> bool:
    return os.path.normcase(a.resolve()) == os.path.normcase(b.resolve())


# -- client --------------------------------------------------------------------


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
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
        if socket.error() in (
            QLocalSocket.LocalSocketError.ServerNotFoundError,
            QLocalSocket.LocalSocketError.ConnectionRefusedError,  # stale socket
        ):
            return None
        raise WallpaperError(f"Cannot reach the running app: {socket.errorString()}")
    try:
        socket.write(encode_request(envelope))
        # Without an event loop, only this pushes the bytes out. (It is
        # False, harmlessly, when the write already completed.)
        written = socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
        if not written and socket.bytesToWrite():
            raise WallpaperError(
                f"Cannot send to the running app: {socket.errorString()}"
            )
        line = _read_line(socket, time.monotonic() + timeout)
    finally:
        socket.abort()
    return decode_reply(line)


def _read_line(socket: QLocalSocket, deadline: float) -> bytes:
    buffer = bytearray()
    while b"\n" not in buffer:
        left_ms = int((deadline - time.monotonic()) * 1000)
        if left_ms <= 0:
            raise WallpaperError("The running app did not answer in time")
        ready = socket.waitForReadyRead(left_ms)
        buffer += socket.readAll().data()
        if not ready and b"\n" not in buffer:
            if socket.state() != QLocalSocket.LocalSocketState.ConnectedState:
                raise WallpaperError("The running app closed the connection")
        if len(buffer) > MAX_MESSAGE * 16:  # replies can be long (status)
            raise ProtocolError("Reply too long")
    return bytes(buffer.split(b"\n", 1)[0])


# -- server --------------------------------------------------------------------


class CommandServer(QObject):
    """
    Accepts requests and emits `received(connection_id, Request)`; the owner
    answers with `reply(connection_id, Reply)`. Malformed requests, other
    versions and requests for a different config file are answered here.
    Never blocks: it may share its thread with the playlist service.
    """

    received = Signal(int, object)  # connection id, commands.Request

    def __init__(
        self, name: str, config_path: Path, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self.name = name
        self._config_path = config_path
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
                f"Cannot open the command socket {self.name}: "
                f"{self._server.errorString()}"
            )

    def close(self) -> None:
        self._server.close()
        for socket, _ in list(self._clients.values()):
            socket.abort()
        self._clients.clear()

    def reply(self, connection_id: int, reply: Reply) -> None:
        client = self._clients.pop(connection_id, None)
        if client is None:
            return  # the client gave up meanwhile
        socket, _ = client
        socket.write(encode_reply(reply))
        socket.disconnectFromServer()  # after the reply is written

    def _accept(self) -> None:
        while (socket := self._server.nextPendingConnection()) is not None:
            connection_id = self._next_id
            self._next_id += 1
            self._clients[connection_id] = (socket, bytearray())
            socket.readyRead.connect(lambda c=connection_id: self._read(c))
            socket.disconnected.connect(socket.deleteLater)
            socket.disconnected.connect(lambda c=connection_id: self._forget(c))
            QTimer.singleShot(
                REQUEST_TIMEOUT_MS, socket, lambda c=connection_id: self._expire(c)
            )
            if socket.bytesAvailable():  # arrived before readyRead was connected
                self._read(connection_id)

    def _read(self, connection_id: int) -> None:
        client = self._clients.get(connection_id)
        if client is None:
            return
        socket, buffer = client
        buffer += socket.readAll().data()
        if b"\n" not in buffer:
            if len(buffer) > MAX_MESSAGE:
                self.reply(connection_id, Reply.error("Request too long"))
            return
        socket.readyRead.disconnect()
        line = bytes(buffer.split(b"\n", 1)[0])
        try:
            envelope = decode_request(line)
        except ProtocolError as err:
            log.warning("Command socket: %s", err)
            self.reply(connection_id, Reply.error(err))
            return
        if not same_file(envelope.config, self._config_path):
            self.reply(
                connection_id,
                Reply.error(
                    f"The running app uses {self._config_path}, not "
                    f"{envelope.config}; quit it to use another config"
                ),
            )
            return
        self.received.emit(connection_id, envelope.request)

    def _expire(self, connection_id: int) -> None:
        client = self._clients.get(connection_id)
        if client is not None and b"\n" not in client[1]:  # still no request
            self._forget(connection_id)
            client[0].abort()

    def _forget(self, connection_id: int) -> None:
        self._clients.pop(connection_id, None)
