"""
The playlist service on its own thread.

`ServiceWorker` lives on a worker `QThread` and creates everything it uses
there: the command socket, the backend (COM on Windows), the SQLite
connection, the tick timer. A change renders for ~200 ms, which must never
happen on the UI thread. The core stays synchronous and lock-free: only
this thread touches it.

Everything reaches the service through `ServiceWorker.execute`, one at a
time, between ticks: socket requests directly (the socket lives on this
thread), the tray through `ServiceHost`. After `save_settings` it ticks,
so that the monitors show the new settings before the reply.
Nothing on this thread may block waiting for a client or spin a nested
event loop. Events (a change, a new overview) go to the UI thread as
signals and to socket subscribers (the UI process) as lines, both from
here; so does `marks_changed` (only to subscribers), when the list files'
mtime or size moved since the last tick or command.

The service stops when it can't go on (a list file went bad, see
`_failed`); each tick then tries to open it again, so it is back once the
file is fixed. Meanwhile commands answer with the error, and the overview
shows it.

`ServiceHost` is the UI thread's handle. It reaches the worker only through
queued signals (requests, extra ticks, stop) and gets results back the
same way (replies, the overview, changes); it never waits on the worker,
except once at shutdown.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass

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
    CommandRequest,
    ErrorReply,
    Event,
    MarksChangedEvent,
    Overview,
    OverviewChangedEvent,
    Reply,
    TargetRequest,
)
from .ipc import CommandServer

log = logging.getLogger(__name__)

ReplyCallback = Callable[[Reply], None]


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
    changed = Signal(object)  # core.service.Change
    overview_changed = Signal(object)  # protocol.Overview
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
            # Once for the app's life: it keeps answering while the service
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
        if self._timer is not None:
            self._timer.stop()
        if self._server is not None:
            self._server.close()
        self._close()
        self._thread_scope.close()
        self.thread().quit()

    @Slot()
    def tick(self) -> None:
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
        if request.command not in ("status", "upcoming", "stats", "settings"):
            target = request.target if isinstance(request, TargetRequest) else None
            log.info("%s %s: %r", request.command, target or "", reply)
        if render:
            # Renders the monitors whose playlist, look or folder changed,
            # then publishes the overview: both before the reply.
            self.tick()
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
    def submit(self, request_id: int, request: CommandRequest) -> None:
        """A request queued by `ServiceHost.submit`; answered by `replied`."""
        self.replied.emit(request_id, self.execute(request))

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
        self.changed.emit(change)
        self._broadcast(ChangedEvent(change=change))

    def _broadcast(self, event: Event) -> None:
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
        """
        Emit the overview if it changed (the tray redraws its menu), and
        tell subscribers if the lists changed.
        """
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
            self.overview_changed.emit(current)
            self._broadcast(OverviewChangedEvent(overview=current))

    def _publish_lists(self) -> None:
        """`marks_changed` if a list file changed: a command, an edit by hand."""
        if self._service is None:
            return
        stamp = self._service.lists_stamp()
        if stamp != self._lists_stamp:
            self._lists_stamp = stamp
            self._broadcast(MarksChangedEvent())


class ServiceHost(QObject):
    """The UI thread's handle on the worker thread."""

    ticked = Signal()
    changed = Signal(object)  # core.service.Change
    overview_changed = Signal(object)  # protocol.Overview
    failed = Signal(str)  # see ServiceWorker.failed

    # Queued into the worker thread.
    _submit = Signal(int, object)
    _tick = Signal()
    _stop = Signal()

    def __init__(self, settings: ServiceSettings) -> None:
        super().__init__()
        self.overview: Overview | None = None
        self._callbacks: dict[int, ReplyCallback] = {}
        self._next_id = 0
        self._thread = QThread()
        self._thread.setObjectName("playlist-service")
        self._worker = ServiceWorker(settings)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.start)
        self._thread.finished.connect(self._worker.deleteLater)

        queued = Qt.ConnectionType.QueuedConnection
        self._submit.connect(self._worker.submit, queued)
        self._tick.connect(self._worker.tick, queued)
        self._stop.connect(self._worker.stop, queued)
        self._worker.replied.connect(self._on_replied, queued)
        self._worker.overview_changed.connect(self._on_overview, queued)
        self._worker.changed.connect(self.changed, queued)
        self._worker.ticked.connect(self.ticked, queued)
        self._worker.failed.connect(self.failed, queued)

    def start(self) -> None:
        self._thread.start()

    def submit(self, request: CommandRequest, callback: ReplyCallback) -> None:
        """Run `request` on the service; `callback` gets the reply (UI thread)."""
        request_id = self._next_id
        self._next_id += 1
        self._callbacks[request_id] = callback
        self._submit.emit(request_id, request)

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

    @Slot(int, object)
    def _on_replied(self, request_id: int, reply: Reply) -> None:
        callback = self._callbacks.pop(request_id, None)
        if callback is not None:
            callback(reply)

    @Slot(object)
    def _on_overview(self, current: Overview) -> None:
        self.overview = current
        self.overview_changed.emit(current)
