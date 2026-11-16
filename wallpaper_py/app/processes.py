"""
Process plumbing shared by the daemon, the window process and the CLI: the command
lines that start them, starting one detached, logging, Ctrl+C.

Two long-lived processes exist: the daemon (`wallpaper tray` / `run`: the
service, the tray, the command socket) and, on demand, the window process
(`wallpaper ui`: the window). Each starts the other when needed: the tray
opens windows, the window process starts the daemon if it isn't running. (The CLI
never starts the daemon: without one, it runs its command itself.)

No Qt at import time except in `quit_on_interrupt`, so the CLI can use the
rest cheaply.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtCore import QCoreApplication

log = logging.getLogger(__name__)


def _python_command() -> list[str]:
    """This interpreter (windowless on Windows), running the package."""
    python = Path(sys.executable)
    if sys.platform == "win32" and python.name.lower() == "python.exe":
        windowless = python.with_name("pythonw.exe")
        if windowless.is_file():
            python = windowless
    return [str(python), "-m", "wallpaper_py"]


def autostart_command() -> list[str]:
    """
    How login (and the window process, if no daemon runs) starts the tray daemon.
    """
    return [*_python_command(), "tray"]


def ui_command(page: str) -> list[str]:
    """How the tray starts the window process, its window on `page`."""
    return [*_python_command(), "ui", page]


def start_detached(command: Sequence[str]) -> None:
    """Start a process that outlives this one, without a console or our pipes."""
    log.info("Starting %s", " ".join(command))
    if sys.platform == "win32":
        # A hidden console of its own, not none (DETACHED_PROCESS): a venv's
        # python.exe is a launcher that starts the real interpreter, which
        # would open a visible console if the launcher had none to pass on.
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        try:  # leave our job object too, so closing the terminal can't end it
            _popen(command, flags | subprocess.CREATE_BREAKAWAY_FROM_JOB)
        except OSError:  # the job doesn't allow that
            _popen(command, flags)
    else:
        _popen(command)


def _popen(command: Sequence[str], creationflags: int = 0) -> None:
    # Never waited for: it runs on after this process exits.
    subprocess.Popen(  # pylint: disable=consider-using-with
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,  # Windows only
        start_new_session=True,  # POSIX only: not ended with our session
    )


def setup_logging(log_file: Path | None) -> None:
    """The terminal (if there is one) and a rotating file, if given."""
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:  # None under pythonw
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        handlers.append(stream)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file = RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        file.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        handlers.append(file)
    # A no-op if logging is already set up (e.g. by an embedding test run).
    logging.basicConfig(level=logging.INFO, handlers=handlers)


@contextmanager
def quit_on_interrupt(app: QCoreApplication) -> Iterator[None]:
    """
    Ctrl+C quits cleanly; a second Ctrl+C exits at once (e.g. if stopping
    hangs). Python only runs signal handlers between bytecodes, so a timer
    wakes the interpreter while Qt's loop waits.
    """
    # pylint: disable=import-outside-toplevel
    from PySide6.QtCore import QTimer

    if threading.current_thread() is not threading.main_thread():
        yield
        return
    presses = 0

    def interrupted(*_: object) -> None:
        nonlocal presses
        presses += 1
        if presses == 1:
            log.info("Ctrl+C: stopping (press it again to exit at once)")
            app.quit()
            return
        log.warning("Ctrl+C again: exiting without waiting")
        logging.shutdown()
        os._exit(130)

    previous = signal.signal(signal.SIGINT, interrupted)
    wake = QTimer()
    wake.timeout.connect(lambda: None)
    wake.start(250)
    try:
        yield
    finally:
        wake.stop()
        signal.signal(signal.SIGINT, previous)
