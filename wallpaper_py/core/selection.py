"""
Resolve a user-typed monitor selector to one monitor.

Deliberately no index-based selection: indices change when a monitor
detaches (see spikes/README.md), which is how the wrong monitor gets hit.
"""

from __future__ import annotations

from collections.abc import Sequence

from .model import Monitor, WallpaperError


class MonitorSelectionError(WallpaperError):
    """The selector matched no monitor, or more than one."""


def resolve_monitor(monitors: Sequence[Monitor], selector: str) -> Monitor:
    """
    Match, in order of precedence:
      1. the full monitor id,
      2. the monitor name,
      3. a unique part of the name.
    All comparisons are case-insensitive. Parts of ids are not matched: ids
    share long common parts (on Windows they all end in the same GUID).
    """
    wanted = selector.strip().lower()
    if not wanted:
        raise MonitorSelectionError("Empty monitor selector")
    for exact in (
        [m for m in monitors if m.id.lower() == wanted],
        [m for m in monitors if m.name.lower() == wanted],
    ):
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise _ambiguous(selector, exact)
    # Before partial matching, so "0" can't silently pick e.g. "U2720Q".
    if wanted.isdigit():
        raise MonitorSelectionError(
            "Monitor numbers are not supported: they change when a monitor is "
            "switched off. Use a name from `wallpaper list`."
        )
    partial = [m for m in monitors if wanted in m.name.lower()]
    if len(partial) == 1:
        return partial[0]
    if partial:
        raise _ambiguous(selector, partial)
    names = ", ".join(m.name for m in monitors) or "none"
    raise MonitorSelectionError(f"No monitor matches {selector!r}; known: {names}")


def _ambiguous(selector: str, matches: Sequence[Monitor]) -> MonitorSelectionError:
    listed = "; ".join(f"{m.name} ({m.id})" for m in matches)
    return MonitorSelectionError(
        f"{selector!r} matches several monitors: {listed}. Use the full id."
    )


def find_exact(monitors: Sequence[Monitor], selector: str) -> Monitor | None:
    """
    The monitor whose full id or name equals `selector` (case-insensitive),
    or None if there is none right now (e.g. switched off and not listed).

    For selectors stored in the config: no partial matching, because a part
    of a name could match a *different* monitor while the intended one is
    missing.
    """
    wanted = selector.strip().lower()
    for matches in (
        [m for m in monitors if m.id.lower() == wanted],
        [m for m in monitors if m.name.lower() == wanted],
    ):
        if len(matches) > 1:
            raise _ambiguous(selector, matches)
        if matches:
            return matches[0]
    return None
