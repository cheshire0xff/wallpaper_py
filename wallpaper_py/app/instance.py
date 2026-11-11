"""
Single instance: a lock file next to the state database.

The command socket alone can't guarantee this: on Windows a second
`QLocalServer` can listen on the same pipe name. The lock is per state
directory, which is exactly the scope that needs one writer.
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

    def acquire(self) -> bool:
        """False if another live process holds it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return bool(self._lock.tryLock(0))

    def release(self) -> None:
        self._lock.unlock()
