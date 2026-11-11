"""
Screen changes -> one extra tick, so a monitor that comes back gets its
image right away instead of within the next 5 s tick.

Qt reports a change 1-2 s before the backend (COM) does, and one switch
fires several signals, so they restart a single-shot timer and only its
timeout counts. The tick itself re-reads the real state, so a missed or
early signal costs at most one regular tick.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QScreen

DEBOUNCE_MS = 2_000


class ScreenWatcher(QObject):
    settled = Signal()  # screens changed, and then nothing for DEBOUNCE_MS

    def __init__(self, app: QGuiApplication, delay_ms: int = DEBOUNCE_MS) -> None:
        super().__init__()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(self.settled)
        app.screenAdded.connect(self._added)
        app.screenRemoved.connect(self.poke)
        app.primaryScreenChanged.connect(self.poke)
        for screen in app.screens():
            self._watch(screen)

    def poke(self, *_: object) -> None:
        """Something changed: (re)start the countdown."""
        self._timer.start()

    def _added(self, screen: QScreen) -> None:
        self._watch(screen)
        self.poke()

    def _watch(self, screen: QScreen) -> None:
        screen.geometryChanged.connect(self.poke)
