"""
What the tray, the window's pages and the CLI say about a playlist, a
monitor or an image, so that they say it alike; made from the protocol's
models. Qt-free: the CLI uses it. Texts only one of them shows stay where
they are shown.

Durations come two ways: core.settings.format_duration is exact (it writes
a setting back: "1h30m15s"), `rounded_duration` is for reading at a glance
(the time left: "1h30m").
"""

from __future__ import annotations

from .core.settings import format_duration
from .protocol import MonitorView, PlaylistDetails, PlaylistView


def monitor_label(monitor: MonitorView) -> str:
    """A playlist's monitor: "PLX2783H", "ZeroMOD (off)", "DELA1B2 (not found)"."""
    if monitor.attached is None:
        return f"{monitor.name} (not found)"
    return monitor.name if monitor.attached else f"{monitor.name} (off)"


def members(playlist: PlaylistView) -> str:
    """Its monitors: "AW3225QF + PLX2783H (off)"."""
    return " + ".join(monitor_label(m) for m in playlist.monitors)


def not_rotating(playlist: PlaylistView) -> str | None:
    """Why it doesn't change by itself: "manual" or "held"; None: it does."""
    if playlist.manual:
        return "manual"
    return "held" if playlist.held else None


def playlist_label(playlist: PlaylistView) -> str:
    """The name, and why it doesn't change by itself: "konachan (held)"."""
    why = not_rotating(playlist)
    return playlist.name if why is None else f"{playlist.name} ({why})"


def traits(playlist: PlaylistDetails) -> list[str]:
    """Order, interval, and held if it is: ["shuffle", "every 30m", "held"]."""
    if playlist.interval is None:
        rotation = "manual"
    else:
        rotation = f"every {format_duration(playlist.interval)}"
    return [playlist.order.value, rotation, *(["held"] if playlist.held else [])]


def rounded_duration(seconds: float) -> str:
    """
    Written as the settings write durations, rounded to what matters: 45s,
    5m50s (under 10 minutes), 21m, 1h, 1h5m, 2d, 2d3h.
    """
    whole = max(round(seconds), 0)
    if whole < 60:
        return f"{whole}s"
    if whole < 600:
        minutes, rest = divmod(whole, 60)
        return f"{minutes}m{rest}s" if rest else f"{minutes}m"
    minutes = round(whole / 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours}h" if hours else f"{days}d"


def format_size(size: int) -> str:
    """Bytes for people: 980 B, 12 KB, 4.2 MB, 1.1 GB."""
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1000:
            return f"{value:.0f} {unit}" if unit != "MB" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} GB"
