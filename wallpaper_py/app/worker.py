"""
The playlist service on its own thread.

`ServiceWorker` lives on a worker `QThread` and creates everything it uses
there: the backend (COM on Windows), the SQLite connection, the tick timer.
A change renders for ~200 ms, which must never happen on the UI thread.
The core stays synchronous and lock-free: only this thread touches it.

`ServiceHost` is the UI thread's handle. It reaches the worker only through
queued signals (requests, extra ticks, reload, stop) and gets results back
the same way (replies, the overview, changes); it never waits on the
worker, except once at shutdown. Requests run one at a time between ticks,
in the order sent.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot

from ..commands import Overview, Reply, Request, execute, open_service, overview
from ..core.backend import Backend
from ..core.model import WallpaperError
from ..core.presence import Presence
from ..core.service import PlaylistService

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


class ServiceWorker(QObject):
    """Every slot runs on the worker thread."""

    # pylint: disable=too-many-instance-attributes

    ticked = Signal()
    changed = Signal(object)  # core.service.Change
    overview_changed = Signal(object)  # commands.Overview
    replied = Signal(int, object)  # request id, commands.Reply

    def __init__(self, settings: ServiceSettings) -> None:
        super().__init__()
        self._settings = settings
        self._thread_scope = ExitStack()
        self._session = ExitStack()  # backend, database and service
        self._service: PlaylistService | None = None
        self._error: str | None = None
        self._restored = False
        self._timer: QTimer | None = None
        self._overview: Overview | None = None

    @Slot()
    def start(self) -> None:
        self._thread_scope.enter_context(self._settings.thread_scope())
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
                    self.changed.emit(change)
            except WallpaperError as err:
                log.warning("%s", err)
            # The loop must outlive surprises (e.g. a COM error while a
            # monitor is switching); logged in full, the next tick retries.
            except Exception:  # pylint: disable=broad-exception-caught
                log.exception("Tick failed")
        self._publish()
        self.ticked.emit()

    @Slot(int, object)
    def execute(self, request_id: int, request: Request) -> None:
        if self._service is None:
            reply = Reply.error(self._error or "The service is not running")
        else:
            try:
                reply = execute(self._service, request)
            except Exception as err:  # pylint: disable=broad-exception-caught
                log.exception("Command %s failed", request.command)
                reply = Reply.error(f"Unexpected error: {err}")
        if request.command != "status":
            log.info("%s %s: %s", request.command, request.target or "", reply)
        self.replied.emit(request_id, reply)
        self._publish()

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
            self._fail(str(err))
            return
        except Exception as err:  # pylint: disable=broad-exception-caught
            log.exception("Starting the service failed")
            self._fail(f"Unexpected error: {err}")
            return
        self._error = None
        for warning in self._service.config.warnings():
            log.warning("%s", warning)
        names = ", ".join(p.name for p in self._service.config.playlists)
        log.info("Running playlists: %s", names)
        if not self._restored:  # first start: put every current image back
            self._service.restore()
            self._restored = True

    def _fail(self, error: str) -> None:
        log.error("%s", error)
        self._error = error
        self._close()

    def _close(self) -> None:
        self._service = None  # release the backend before the scope ends
        self._session.close()

    def _publish(self) -> None:
        """Emit the overview if it changed (the tray redraws its menu)."""
        if self._service is None:
            current = Overview(error=self._error)
        else:
            try:
                current = overview(self._service)
            except Exception:  # pylint: disable=broad-exception-caught
                log.exception("Reading the overview failed")
                return
        if current != self._overview:
            self._overview = current
            self.overview_changed.emit(current)


class ServiceHost(QObject):
    """The UI thread's handle on the worker thread."""

    ticked = Signal()
    changed = Signal(object)  # core.service.Change
    overview_changed = Signal(object)  # commands.Overview

    # Queued into the worker thread.
    _execute = Signal(int, object)
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
        self._execute.connect(self._worker.execute, queued)
        self._tick.connect(self._worker.tick, queued)
        self._reload.connect(self._worker.reload, queued)
        self._stop.connect(self._worker.stop, queued)
        self._worker.replied.connect(self._on_replied, queued)
        self._worker.overview_changed.connect(self._on_overview, queued)
        self._worker.changed.connect(self.changed, queued)
        self._worker.ticked.connect(self.ticked, queued)

    def start(self) -> None:
        self._thread.start()

    def submit(self, request: Request, callback: ReplyCallback) -> None:
        """Run `request` on the service; `callback` gets the reply (UI thread)."""
        request_id = self._next_id
        self._next_id += 1
        self._callbacks[request_id] = callback
        self._execute.emit(request_id, request)

    def request_tick(self) -> None:
        """An extra tick soon (e.g. a monitor came back)."""
        self._tick.emit()

    def reload(self) -> None:
        self._reload.emit()

    def shutdown(self, timeout_ms: int = 10_000) -> bool:
        """
        Stop the worker after what it is doing now and wait for its thread;
        False if it did not finish in time.
        """
        if not self._thread.isRunning():
            return True
        self._stop.emit()
        return bool(self._thread.wait(timeout_ms))

    @Slot(int, object)
    def _on_replied(self, request_id: int, reply: Reply) -> None:
        callback = self._callbacks.pop(request_id, None)
        if callback is not None:
            callback(reply)

    @Slot(object)
    def _on_overview(self, current: Overview) -> None:
        self.overview = current
        self.overview_changed.emit(current)
