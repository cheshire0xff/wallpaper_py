"""
The playlist service on its own thread.

`ServiceWorker` lives on a worker `QThread` and creates everything it uses
there: the command socket, the backend (COM on Windows), the SQLite
connection, the tick timer. A change renders for ~200 ms, which must never
happen on the main thread. The core stays synchronous and lock-free: only
this thread touches it.

Everything reaches the service through `ServiceWorker.execute`, one at a
time, between ticks: socket requests directly (the socket lives on this
thread), the tray through its `LocalClient`. Not the window's requests:
the socket answers those itself (`CommandServer`), also while the service
is down; the tray's `show_window` is handed to it. After `save_settings` it ticks,
so that the monitors show the new settings before the reply. A command or
tick over `SLOW_S` is logged with its time.
Nothing on this thread may block waiting for a client or spin a nested
event loop. Each event (a change, a new overview, the list files' mtime or
size moved since the last tick or command) is sent once, as the protocol's
`Event` (`ServiceWorker._send`): to the main thread as the `sent` signal,
and to socket subscribers (the window process) as a line.

The service stops when it can't go on (a list file went bad, see
`_failed`); each tick then tries to open it again, so it is back once the
file is fixed. Meanwhile commands answer with the error, and the overview
shows it.

`ServiceThread` is the main thread's handle on the thread: start, extra
ticks, stop. Its `client`, a `LocalClient`, is the tray's `Client`: requests
go to the worker as queued signals, replies and events come back the same
way. The models cross the threads as they are (frozen, so safe to share):
no JSON. Neither waits on the worker, except once at shutdown.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass
from typing import override

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot

from ..commands import (
    error_reply,
    execute,
    open_service,
    overview,
)
from ..core.backend import Backend
from ..core.model import AppError, ConfigError, WallpaperError
from ..core.presence import Presence
from ..core.service import Change, PlaylistService
from ..protocol import (
    ChangedEvent,
    ClientRequest,
    CommandRequest,
    ErrorReply,
    Event,
    MarksChangedEvent,
    Overview,
    OverviewChangedEvent,
    Reply,
    ShowWindowRequest,
    ShowWindowResult,
)
from .client import Client, ReplyCallback
from .ipc import CommandServer

log = logging.getLogger(__name__)

SLOW_S = 0.1  # seconds: a command or tick that takes longer is logged


@dataclass(frozen=True)
class ServiceSettings:
    backend_factory: Callable[[], Backend]
    presence_factory: Callable[[], Presence]
    # Per-thread platform setup around the backend's life (backends.thread_scope).
    thread_scope: Callable[[], AbstractContextManager[None]]
    tick_period: float = 5.0  # seconds
    server_name: str | None = None  # the command socket (ipc.server_name); None: none


class ServiceWorker(QObject):
    """Every slot runs on the worker thread."""

    # pylint: disable=too-many-instance-attributes

    ticked = Signal()
    sent = Signal(object)  # protocol.Event (not `event`: QObject.event)
    replied = Signal(int, object)  # request id, protocol.Reply
    failed = Signal(str)  # the command socket could not open; nothing runs

    def __init__(self, settings: ServiceSettings) -> None:
        super().__init__()
        self._settings = settings
        self._thread_scope = ExitStack()
        self._session = ExitStack()  # backend, database and service
        self._service: PlaylistService | None = None
        self._error: ErrorReply | None = None  # why there is no service
        self._timer: QTimer | None = None
        self._server: CommandServer | None = None
        self._overview: Overview | None = None
        # The list files as last published (_publish_lists). Kept while the
        # service is down: a list fixed meanwhile is announced once it runs.
        self._lists_stamp: tuple[object, ...] | None = None

    @Slot()
    def start(self) -> None:
        self._thread_scope.enter_context(self._settings.thread_scope())
        if self._settings.server_name is not None:
            # Once for the daemon's life: it keeps answering while the service
            # is down (with the error).
            self._server = CommandServer(self._settings.server_name, self)
            self._server.received.connect(self._answer)
            self._server.subscribed.connect(self._welcome)
            try:
                self._server.listen()
            except WallpaperError as err:
                log.error("%s", err)
                self.failed.emit(str(err))
                return
        self._timer = QTimer(self)
        self._timer.setInterval(round(self._settings.tick_period * 1000))
        self._timer.timeout.connect(self.tick)
        self._timer.start()
        self.tick()  # opens the service

    @Slot()
    def stop(self) -> None:
        """
        At shutdown: the timer, then the socket (ending every subscription,
        so the window process quits too), the service, COM; then the thread.
        """
        if self._timer is not None:
            self._timer.stop()
        if self._server is not None:
            self._server.close()
        self._close()
        self._thread_scope.close()
        self.thread().quit()

    @Slot()
    def tick(self) -> None:
        started = time.monotonic()
        self._tick()
        _log_if_slow("Tick", started)

    def _tick(self) -> None:
        if self._service is None:
            self._open()  # down: try again (a list file may be fixed now)
        if self._service is not None:
            try:
                for change in self._service.tick():
                    log.info("%s", change.describe())
                    self._announce(change)
            except Exception as err:  # pylint: disable=broad-exception-caught
                self._failed("Tick", err)
        self._publish()
        self.ticked.emit()

    def execute(self, request: CommandRequest) -> Reply:
        """Run one command: the only way in for socket, tray and windows."""
        started = time.monotonic()
        reply = self._execute(request)
        _log_if_slow(f"Command {request.command}", started)
        return reply

    def _execute(self, request: CommandRequest) -> Reply:
        reply: Reply
        render = False  # monitors to render again: tick before the reply
        if self._service is None:
            reply = self._error or ErrorReply.make(
                "failed", "The service is not running"
            )
        else:
            try:
                reply, change, render = execute(self._service, request)
            except Exception as err:  # pylint: disable=broad-exception-caught
                reply, change = self._failed(f"Command {request.command}", err), None
            if change is not None:
                self._announce(change)
        if request.changes:
            log.info("%s: %r", request.command, reply)
        if render:
            # Renders the monitors whose playlist, look or folder changed,
            # then publishes the overview: both before the reply.
            self._tick()
        else:
            self._publish()
        return reply

    def _failed(self, what: str, error: Exception) -> ErrorReply:
        """
        The one handler for an error from the running service (a command, a
        tick, the overview); returns what a command answers.

        - ConfigError: a list file went bad while running (e.g. saved
          with a byte order mark). The service stops, as when it can't
          start; commands answer with the error and the overview shows it
          until a tick opens it again.
        - Any other WallpaperError: expected (a command already answered
          it); logged, the next tick retries.
        - Anything else: a bug, logged in full. The loop must outlive
          surprises (e.g. a COM error while a monitor is switching).
        """
        if isinstance(error, ConfigError):
            self._fail(error)
        elif isinstance(error, WallpaperError):
            log.warning("%s: %s", what, error)
        else:
            log.exception("%s failed", what)  # called from an except block
        return error_reply(error)

    @Slot(int, object)
    def submit(self, request_id: int, request: ClientRequest) -> None:
        """
        A request queued by `LocalClient.run`; answered by `replied`.
        `show_window` goes to the socket, which knows the window.
        """
        reply: Reply
        if isinstance(request, ShowWindowRequest):
            shown = self._server is not None and self._server.show_window(request.page)
            reply = ShowWindowResult(shown=shown)
        else:
            reply = self.execute(request)
        self.replied.emit(request_id, reply)

    @Slot(int, object)
    def _answer(self, connection_id: int, request: CommandRequest) -> None:
        """A request from the socket (on this thread, so no queueing)."""
        assert self._server is not None
        self._server.reply(connection_id, self.execute(request))

    @Slot(int)
    def _welcome(self, connection_id: int) -> None:
        """A new subscriber: its first event is the current overview."""
        assert self._server is not None
        if self._overview is not None:  # else the first publish brings it
            event = OverviewChangedEvent(overview=self._overview)
            self._server.send_event(connection_id, event)

    def _announce(self, change: Change) -> None:
        self._send(ChangedEvent(change=change))

    def _send(self, event: Event) -> None:
        """To both kinds of listener: the main thread, socket subscribers."""
        self.sent.emit(event)
        if self._server is not None:
            self._server.broadcast(event)

    def _open(self) -> None:
        settings = self._settings
        try:
            backend = settings.backend_factory()
            self._service = self._session.enter_context(
                open_service(
                    backend,
                    settings.presence_factory(),
                    settings.tick_period,
                )
            )
        except AppError as err:
            self._fail(err)
            return
        except Exception as err:  # pylint: disable=broad-exception-caught
            log.exception("Starting the service failed")
            self._fail(err)
            return
        self._error = None
        for warning in self._service.settings.warnings():
            log.warning("%s", warning)
        names = ", ".join(p.name for p in self._service.settings.playlists)
        log.info("Running playlists: %s", names)
        # Put every current image back, rendered again: after a reboot, or
        # after the service was down.
        self._service.restore()

    def _fail(self, error: Exception) -> None:
        reply = error_reply(error)
        if reply != self._error:  # each tick tries again: logged once
            log.error("%s", reply.error.message)
        self._error = reply
        self._close()

    def _close(self) -> None:
        self._service = None  # release the backend before the scope ends
        self._session.close()

    def _publish(self) -> None:
        """Send the overview if it changed, and `marks_changed` if the lists did."""
        self._publish_lists()
        current: Overview | None = None
        if self._service is not None:
            try:
                current = overview(self._service)
            except Exception as err:  # pylint: disable=broad-exception-caught
                self._failed("Reading the overview", err)
                if self._service is not None:  # still running: keep the last
                    return
        if current is None:  # no service, or overview() just stopped it
            current = Overview(error=self._error.error.message if self._error else None)
        if current != self._overview:
            self._overview = current
            self._send(OverviewChangedEvent(overview=current))

    def _publish_lists(self) -> None:
        """`marks_changed` if a list file changed: a command, an edit by hand."""
        if self._service is None:
            return
        stamp = self._service.lists_stamp()
        if stamp != self._lists_stamp:
            self._lists_stamp = stamp
            self._send(MarksChangedEvent())


def _log_if_slow(what: str, started: float) -> None:
    took = time.monotonic() - started
    if took > SLOW_S:
        log.info("%s took %d ms", what, round(took * 1000))


class LocalClient(Client):
    """
    The tray's `Client`, in the daemon: `run` queues the request to the
    worker and its reply comes back through `replied`; events come through
    the worker's `sent` signal. Made before the thread starts, so it sees
    every event (that is its subscription). Lives on the main thread.
    """

    _submit = Signal(int, object)  # request id, protocol.ClientRequest

    def __init__(self, worker: ServiceWorker, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._callbacks: dict[int, ReplyCallback] = {}
        self._next_id = 0
        queued = Qt.ConnectionType.QueuedConnection
        self._submit.connect(worker.submit, queued)
        worker.replied.connect(self._replied, queued)
        worker.sent.connect(self._dispatch, queued)

    @override
    def run(self, request: ClientRequest, on_reply: ReplyCallback) -> None:
        request_id = self._next_id
        self._next_id += 1
        self._callbacks[request_id] = on_reply
        self._submit.emit(request_id, request)

    @Slot(int, object)
    def _replied(self, request_id: int, reply: Reply) -> None:
        callback = self._callbacks.pop(request_id, None)
        if callback is not None:
            callback(reply)


class ServiceThread(QObject):
    """
    The main thread's handle on the worker thread; `client` is the daemon's
    own `Client` (the tray's).
    """

    ticked = Signal()
    failed = Signal(str)  # see ServiceWorker.failed

    # Queued into the worker thread.
    _tick = Signal()
    _stop = Signal()

    def __init__(self, settings: ServiceSettings) -> None:
        super().__init__()
        self._thread = QThread()
        self._thread.setObjectName("playlist-service")
        self._worker = ServiceWorker(settings)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.start)
        self._thread.finished.connect(self._worker.deleteLater)
        self.client = LocalClient(self._worker, self)

        queued = Qt.ConnectionType.QueuedConnection
        self._tick.connect(self._worker.tick, queued)
        self._stop.connect(self._worker.stop, queued)
        self._worker.ticked.connect(self.ticked, queued)
        self._worker.failed.connect(self.failed, queued)

    def start(self) -> None:
        self._thread.start()

    @Slot()
    def request_tick(self) -> None:
        """An extra tick soon (e.g. a monitor came back)."""
        self._tick.emit()

    def shutdown(self, timeout_ms: int = 10_000) -> bool:
        """
        Stop the worker (and its socket) after what it is doing now and wait
        for its thread; False if it did not finish in time.
        """
        if not self._thread.isRunning():
            return True
        self._stop.emit()
        # Short waits: between them Python can run a Ctrl+C handler, which
        # it can't while blocked in one long wait.
        deadline = time.monotonic() + timeout_ms / 1000
        while not self._thread.wait(100):
            if time.monotonic() >= deadline:
                return False
        return True
