"""Safely put one image on one monitor, whatever the backend supports."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .backend import Backend
from .imaging import RENDER_SUFFIX, render_file
from .model import ImageMode, Monitor, MonitorNotAttachedError


def find_attached(backend: Backend, monitor_id: str) -> Monitor:
    """
    Re-query the backend and return the monitor if it is attached *now*.

    Always called right before setting a wallpaper: monitors can detach at
    any time (e.g. switched off), and an old snapshot must never be trusted.
    """
    for monitor in backend.monitors():
        if monitor.id.lower() == monitor_id.lower():
            if not monitor.attached:
                raise MonitorNotAttachedError(f"{monitor.name} is not attached")
            return monitor
    raise MonitorNotAttachedError(f"No monitor with id {monitor_id!r}")


def rendered_path(render_dir: Path, monitor: Monitor) -> Path:
    """
    Alternate between two files per monitor.

    Windows may not re-read a wallpaper whose path did not change, so the
    new render goes to whichever slot is not currently shown.
    """
    key = hashlib.sha1(monitor.id.lower().encode()).hexdigest()[:12]
    slots = [render_dir / f"{key}-{slot}{RENDER_SUFFIX}" for slot in (0, 1)]
    current = monitor.wallpaper
    if current is not None and Path(current) == slots[0]:
        return slots[1]
    return slots[0]


def apply_wallpaper(
    backend: Backend,
    monitor_id: str,
    image: Path,
    mode: ImageMode | None,
    render_dir: Path,
) -> Path:
    """
    Set `image` on the monitor; returns the file actually handed to the OS.

    If the backend can't apply `mode` per monitor, the image is rendered to
    the monitor's exact resolution first. Such an image looks right in any
    fill/fit/stretch global mode, but not in tile or span (which other
    wallpaper tools, e.g. John's Background Switcher, use for one combined
    image), so the global mode is switched to FILL in that case.

    Raises:
        MonitorNotAttachedError: the monitor is not attached right now.
    """
    monitor = find_attached(backend, monitor_id)
    if mode is None or backend.per_monitor_modes:
        backend.set_wallpaper(monitor.id, image, mode)
        return image
    assert monitor.rect is not None
    rendered = render_file(
        image, monitor.rect.size, mode, rendered_path(render_dir, monitor)
    )
    # Re-check: rendering a big image takes time, the monitor may have gone,
    # and its size may have changed.
    current = find_attached(backend, monitor_id)
    if current.rect != monitor.rect:
        raise MonitorNotAttachedError(
            f"{current.name} changed resolution while rendering; try again"
        )
    if backend.global_mode() is None:
        backend.set_global_mode(ImageMode.FILL)
    backend.set_wallpaper(current.id, rendered)
    return rendered
