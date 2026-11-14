"""
Is someone looking at the screen? Rotation pauses while the session is
locked or idle, so no image goes by unseen.

Best effort: a platform that can't tell returns None, and rotation then
simply never pauses.
"""

from __future__ import annotations

from typing import Protocol


class Presence(Protocol):
    def session_locked(self) -> bool | None:
        """True while the session is locked; None if unknown."""

    def idle_seconds(self) -> float | None:
        """Seconds since the last keyboard / mouse input; None if unknown."""


class NoPresence:
    """For platforms without a presence implementation (yet)."""

    def session_locked(self) -> bool | None:
        return None

    def idle_seconds(self) -> float | None:
        return None
