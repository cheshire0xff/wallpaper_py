"""
The playlist service on its own thread.

`ServiceWorker` lives on a worker `QThread` and creates everything it uses
there: the command socket, the backend (COM on Windows), the SQLite
connection, the tick timer. A change renders for ~200 ms, which must never
happen on the UI thread. The core stays synchronous and lock-free: only
this thread touches it.

Everything reaches the service through `ServiceWorker.execute`, one at a
time, between ticks: socket requests directly (the socket lives on this
thread), the tray through `ServiceHost`. Nothing on this thread may block
waiting for a client or spin a nested event loop. Events (a change, a new
overview) go to the UI thread as signals and to socket subscribers (the UI
process) as lines, both from here.

`ServiceHost` is the UI thread's handle. It reaches the worker only through
queued signals (requests, extra ticks, reload, stop) and gets results back
the same way (replies, the overview, changes); it never waits on the
worker, except once at shutdown.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot

from ..commands import error_reply, execute, open_service, overview
from ..core.backend import Backend
from ..core.model import WallpaperError
from ..core.presence import Presence
from ..core.service import Change, PlaylistService
from ..protocol import (
    ChangedEvent,
    ErrorReply,
    Event,
    Overview,
    OverviewChangedEvent,
    Reply,
    Request,
)
from .ipc import CommandServer

log = logging.getLogger(__name__)

ReplyCallback = Callable[[Reply], None]


@dataclass(frozen=True)
class ServiceSettings:
    config_path: Path
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
        self._restored = False
        self._timer: QTimer | None = None
        self._server: CommandServer | None = None
        self._overview: Overview | None = None

    @Slot()
    def start(self) -> None:
        self._thread_scope.enter_context(self._settings.thread_scope())
        if self._settings.server_name is not None:
            # Once for the app's life: it keeps answering across reloads
            # (with the error while the config is broken).
            self._server = CommandServer(
                self._settings.server_name, self._settings.config_path, self
            )
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
        self._open()
        self._timer.start()
        self.tick()

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
    def reload(self) -> None:
        log.info("Reloading the config")
        self._close()
        self._open()
        self.tick()

    @Slot()
    def tick(self) -> None:
        if self._service is not None:
            try:
                for change in self._service.tick():
                    log.info("%s", change.describe())
                    self._announce(change)
            except WallpaperError as err:
                log.warning("%s", err)
            # The loop must outlive surprises (e.g. a COM error while a
            # monitor is switching); logged in full, the next tick retries.
            except Exception:  # pylint: disable=broad-exception-caught
                log.exception("Tick failed")
        self._publish()
        self.ticked.emit()

    def execute(self, request: Request) -> Reply:
        """Run one command: the only way in for socket, tray and windows."""
        reply: Reply
        if self._service is None:
            reply = self._error or ErrorReply.make(
                "failed", "The service is not running"
            )
        else:
            try:
                reply, change = execute(self._service, request)
            except Exception as err:  # pylint: disable=broad-exception-caught
                log.exception("Command %s failed", request.command)
                reply, change = error_reply(err), None
            if change is not None:
                self._announce(change)
        if request.command != "status":
            log.info("%s %s: %r", request.command, request.target or "", reply)
        self._publish()
        return reply

    @Slot(int, object)
    def submit(self, request_id: int, request: Request) -> None:
        """A request queued by `ServiceHost.submit`; answered by `replied`."""
        self.replied.emit(request_id, self.execute(request))

    @Slot(int, object)
    def _answer(self, connection_id: int, request: Request) -> None:
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
                    settings.config_path,
                    backend,
                    settings.presence_factory(),
                    settings.tick_period,
                )
            )
        except WallpaperError as err:
            self._fail(err)
            return
        except Exception as err:  # pylint: disable=broad-exception-caught
            log.exception("Starting the service failed")
            self._fail(err)
            return
        self._error = None
        for warning in self._service.config.warnings():
            log.warning("%s", warning)
        names = ", ".join(p.name for p in self._service.config.playlists)
        log.info("Running playlists: %s", names)
        if not self._restored:  # first start: put every current image back
            self._service.restore()
            self._restored = True

    def _fail(self, error: Exception) -> None:
        self._error = error_reply(error)
        log.error("%s", self._error.error.message)
        self._close()

    def _close(self) -> None:
        self._service = None  # release the backend before the scope ends
        self._session.close()

    def _publish(self) -> None:
        """Emit the overview if it changed (the tray redraws its menu)."""
        if self._service is None:
            current = Overview(error=self._error.error.message if self._error else None)
        else:
            try:
                current = overview(self._service)
            except Exception:  # pylint: disable=broad-exception-caught
                log.exception("Reading the overview failed")
                return
        if current != self._overview:
            self._overview = current
            self.overview_changed.emit(current)
            self._broadcast(OverviewChangedEvent(overview=current))


class ServiceHost(QObject):
    """The UI thread's handle on the worker thread."""

    ticked = Signal()
    changed = Signal(object)  # core.service.Change
    overview_changed = Signal(object)  # protocol.Overview
    failed = Signal(str)  # see ServiceWorker.failed

    # Queued into the worker thread.
    _submit = Signal(int, object)
    _tick = Signal()
    _reload = Signal()
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
        self._reload.connect(self._worker.reload, queued)
        self._stop.connect(self._worker.stop, queued)
        self._worker.replied.connect(self._on_replied, queued)
        self._worker.overview_changed.connect(self._on_overview, queued)
        self._worker.changed.connect(self.changed, queued)
        self._worker.ticked.connect(self.ticked, queued)
        self._worker.failed.connect(self.failed, queued)

    def start(self) -> None:
        self._thread.start()

    def submit(self, request: Request, callback: ReplyCallback) -> None:
        """Run `request` on the service; `callback` gets the reply (UI thread)."""
        request_id = self._next_id
        self._next_id += 1
        self._callbacks[request_id] = callback
        self._submit.emit(request_id, request)

    @Slot()
    def request_tick(self) -> None:
        """An extra tick soon (e.g. a monitor came back)."""
        self._tick.emit()

    def reload(self) -> None:
        self._reload.emit()

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
