"""
Start the app at login: the Autostart protocol. Platforms implement it in
`backends` (Windows: the Run registry key; Linux: an XDG autostart entry).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class Autostart(Protocol):
    def enabled(self) -> bool:
        """True if an entry exists (whatever command it runs)."""

    def enable(self, command: Sequence[str]) -> None:
        """Create or replace the entry; `command` is argv, quoted as needed."""

    def disable(self) -> None:
        """Remove the entry; does nothing if there is none."""
