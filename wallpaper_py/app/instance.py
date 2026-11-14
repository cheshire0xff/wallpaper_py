"""
Single instance: a lock file next to the state database.

The command socket alone can't guarantee this: on Windows a second
`QLocalServer` can listen on the same pipe name. The lock is per state
directory, which is exactly the scope that needs one writer. A CLI
command run while no app is running holds it too, for that command.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QLockFile


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = QLockFile(str(path))
        # Never treat a held lock as stale by age (the default is 30 s);
        # a lock whose process died is still detected and taken over.
        self._lock.setStaleLockTime(0)

    def acquire(self, wait_s: float = 0.0) -> bool:
        """False if another live process holds it (still, after `wait_s`)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return bool(self._lock.tryLock(round(wait_s * 1000)))

    def release(self) -> None:
        self._lock.unlock()
