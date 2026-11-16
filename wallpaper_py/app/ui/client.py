"""
How windows talk to the daemon: `Client`, the only thing they may use.

    client.run(request, on_reply)   a command; its Reply comes back later
    client.overview_changed         the latest Overview (also client.overview)
    client.changed                  a wallpaper changed (a Change)
    client.marks_changed            favourites or to-remove changed
    client.failed                   the daemon can't be reached; nothing more comes

Windows never see `PlaylistService`, `ServiceWorker` or a socket. `SocketClient`
implements it over the daemon's socket for the window process: it subscribes (and
starts the daemon first if none runs, which the CLI never does), and runs each
command on a connection of its own. Everything is event driven: the main thread never
waits.
"""

from __future__ import annotations

import logging
import time
import weakref
from collections.abc import Callable
from typing import override

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtNetwork import QLocalSocket
from shiboken6 import Shiboken

from ...core import paths
from ...protocol import (
    ChangedEvent,
    CommandRequest,
    Envelope,
    ErrorReply,
    MarksChangedEvent,
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


class ReplyTo[P: QObject, *Ts]:
    """
    A `Client.run` callback that calls `method(page, *args, reply)`, holding
    the page weakly: the window may close (and be deleted) before the reply.
    `args`: what the page needs to know about the request (plain data).
    """

    def __init__(
        self, page: P, method: Callable[[P, *Ts, Reply], None], *args: *Ts
    ) -> None:
        self._page = weakref.ref(page)
        self._method = method
        self._args = args

    def __call__(self, reply: Reply) -> None:
        page = self._page()
        if page is not None and Shiboken.isValid(page):
            self._method(page, *self._args, reply)


class OneAtATime:
    """
    For a request whose latest answer is all that counts (Home's `status`,
    the Library's `stats`): one runs at a time, and asking while it runs
    asks once more after its reply, so the last answer is never stale.

        if asks.ask():          send it
        ...reply: if asks.done(): ask again (more came meanwhile)
    """

    def __init__(self) -> None:
        self._running = False
        self._again = False

    def ask(self) -> bool:
        """True: send it now. False: one runs; it is asked again after it."""
        if self._running:
            self._again = True
            return False
        self._running = True
        return True

    def done(self) -> bool:
        """Its reply came; True if it should be asked again now."""
        self._running = False
        again, self._again = self._again, False
        return again


class Client(QObject):
    """The narrow interface windows depend on (see the module docstring)."""

    overview_changed = Signal(object)  # protocol.Overview
    changed = Signal(object)  # core.service.Change
    marks_changed = Signal()  # read the list files again
    failed = Signal(str)  # message

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.overview: Overview | None = None  # None until the first arrives

    def run(self, request: CommandRequest, on_reply: ReplyCallback) -> None:
        """Run `request` in the daemon; `on_reply` gets the reply (this thread)."""
        raise NotImplementedError

    def _set_overview(self, overview: Overview) -> None:
        self.overview = overview
        self.overview_changed.emit(overview)


class SocketClient(Client):
    """
    `Client` over the daemon's socket. `start()` subscribes; if no daemon runs,
    it calls `start_daemon` once and retries for START_TIMEOUT_S. `ended`
    fires when an established subscription closes (the daemon quit). `idle`
    fires when the last command in flight has its reply (see `busy`).
    """

    # pylint: disable=too-many-instance-attributes

    ended = Signal()
    idle = Signal()

    def __init__(
        self,
        name: str,
        start_daemon: Callable[[], None] | None,
        *,
        start_timeout_s: float = START_TIMEOUT_S,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._name = name
        self._start_daemon = start_daemon
        self._start_timeout_s = start_timeout_s
        self._deadline: float | None = None  # set once the daemon was started
        self._socket: QLocalSocket | None = None
        self._lines = ipc.LineBuffer(ipc.MAX_REPLY, "Event")
        self._subscribed = False
        self._over = False  # failed, ended or closed: nothing more happens
        self._in_flight = 0  # commands sent by `run` and not answered yet

    def start(self) -> None:
        self._connect()

    def close(self) -> None:
        self._over = True
        self._drop_socket()

    def busy(self) -> bool:
        """
        A command still waits for its reply. Its request is only written on
        later turns of the event loop, so quitting now would drop it.
        """
        return self._in_flight > 0

    @override
    def run(self, request: CommandRequest, on_reply: ReplyCallback) -> None:
        # The callbacks live in the LineRequest, which Qt owns: they hold
        # this client weakly, or it would never be freed (see AGENTS.md).
        client = weakref.ref(self)

        def replied(reply: Reply) -> None:
            this = client()
            if this is not None and Shiboken.isValid(this):
                this.reply_done(reply, on_reply)
            else:
                on_reply(reply)

        def answered(line: bytes) -> None:
            try:
                reply = ipc.decode_reply(line)
            except ipc.ProtocolError as err:
                reply = ErrorReply.make("protocol", err)
            replied(reply)

        def failed(not_running: bool, message: str) -> None:
            if not_running:
                message = "wallpaper_py is not running"
            replied(ErrorReply.make("unreachable", message))

        self._in_flight += 1

        envelope = Envelope(request=request)
        ipc.LineRequest(self._name, ipc.encode(envelope), answered, failed, parent=self)

    def reply_done(self, reply: Reply, on_reply: ReplyCallback) -> None:
        """A command's reply came: pass it on, then `idle` if it was the last."""
        try:
            on_reply(reply)
        finally:
            self._in_flight -= 1
            if self._in_flight == 0:
                self.idle.emit()

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
        self._lines = ipc.LineBuffer(ipc.MAX_REPLY, "Event")
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
            envelope = Envelope(request=Subscribe())
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
        try:
            lines = self._lines.feed(socket.readAll().data())
        except ipc.ProtocolError as err:
            self._fail(f"Cannot read wallpaper_py's events: {err}")
            return
        for line in lines:
            if self._over:
                return
            try:
                message = ipc.decode_event(line)
            except ipc.ProtocolError as err:  # e.g. an event added later
                log.info("Skipping a line from the daemon: %s", err)
                continue
            if isinstance(message, ErrorReply):  # the daemon refused
                self._fail(message.error.message)
                return
            if not self._subscribed:
                self._subscribed = True
                log.info("Connected to the daemon")
            if isinstance(message, OverviewChangedEvent):
                self._set_overview(message.overview)
            elif isinstance(message, ChangedEvent):
                self.changed.emit(message.change)
            elif isinstance(message, MarksChangedEvent):
                self.marks_changed.emit()

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
        if self._over:  # what was left failed it (a line too long)
            return
        log.info("The daemon closed the connection")
        self.close()
        self.ended.emit()

    def _retry(self) -> None:
        if self._deadline is None:
            if self._start_daemon is None:
                self._fail("wallpaper_py is not running")
                return
            try:
                self._start_daemon()
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
