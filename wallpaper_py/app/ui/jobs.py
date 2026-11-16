"""
Background work for the windows: a `QThreadPool` of its own, and the
results delivered on the main thread.

`Jobs.run(key, work)` runs `work()` on a pool thread; `done(key, result)`
fires on the main thread with what it returned. `run_loop(work)` is for a job
that delivers any number of results (`work(deliver)`, `deliver(key,
result)` per result) until it returns: the grid's tile workers. `close()`
drops the jobs not started yet, waits for the running ones and delivers
nothing after; the owner calls it before it goes away.

Workers emit on a relay that lives on the main thread, so delivery is queued
(AGENTS.md, Qt rules). The pool is made before the relay: children go in
creation order, so a pool deleted without `close()` still waits for its
jobs before the relay they emit on is gone. A job is never taken back out
of the pool (it deletes a job once it has run, so a Python handle to one
may be dead by then): work that may go stale checks for itself whether it
is still wanted, and state shared with it lives outside the job.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import override

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal, Slot

log = logging.getLogger(__name__)

# Hands one result to the main thread: (key, result).
Deliver = Callable[[object, object], None]


class _Relay(QObject):
    """Lives on the main thread; jobs emit on it."""

    done = Signal(object, object)  # key, result


class _Job(QRunnable):
    def __init__(self, work: Callable[[Deliver], None], relay: _Relay) -> None:
        super().__init__()
        self._work = work
        self._relay = relay

    @override
    def run(self) -> None:
        try:
            self._work(self._relay.done.emit)
        except Exception:  # pylint: disable=broad-exception-caught
            log.exception("A background job failed")


class _Single:
    """`run`'s work: one result, delivered under its key."""

    def __init__(self, key: object, work: Callable[[], object]) -> None:
        self._key = key
        self._work = work

    def __call__(self, deliver: Deliver) -> None:
        deliver(self._key, self._work())


class Jobs(QObject):
    """See the module docstring."""

    done = Signal(object, object)  # key, result: on the main thread

    def __init__(
        self,
        threads: int = 1,
        *,
        priority: QThread.Priority | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)  # before the relay: see the docstring
        self._pool.setMaxThreadCount(threads)
        if priority is not None:
            self._pool.setThreadPriority(priority)
        self._relay = _Relay(self)
        self._relay.done.connect(self._delivered)
        self._closed = False

    def run(self, key: object, work: Callable[[], object]) -> None:
        """`work()` on a pool thread; `done(key, its result)`."""
        self.run_loop(_Single(key, work))

    def run_loop(self, work: Callable[[Deliver], None]) -> None:
        """`work(deliver)` on a pool thread; `done` per delivered result."""
        if not self._closed:
            self._pool.start(_Job(work, self._relay))

    def wait(self) -> None:
        """Wait until every job has run (their results may still be on their way)."""
        self._pool.waitForDone()

    def close(self) -> None:
        """Drop the jobs not started, wait for the running ones; no more `done`."""
        self._closed = True
        self._pool.clear()
        self._pool.waitForDone()

    @Slot(object, object)
    def _delivered(self, key: object, result: object) -> None:
        if not self._closed:
            self.done.emit(key, result)
