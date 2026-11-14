"""Safely put one image on one monitor, rendered for it."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .backend import Backend
from .imaging import RENDER_SUFFIX, load, render
from .model import Look, Monitor, MonitorId, MonitorNotAttachedError


def find_attached(backend: Backend, monitor_id: MonitorId) -> Monitor:
    """
    Re-query the backend and return the monitor if it is attached *now*.

    Always called right before setting a wallpaper: monitors can detach at
    any time (e.g. switched off), and an old snapshot must never be trusted.
    """
    for monitor in backend.monitors():
        if monitor.id == monitor_id:
            if not monitor.attached:
                raise MonitorNotAttachedError(f"{monitor.name} is not attached")
            return monitor
    raise MonitorNotAttachedError(f"No monitor with id {str(monitor_id)!r}")


def rendered_path(render_dir: Path, monitor: Monitor) -> Path:
    """
    Alternate between two files per monitor.

    Windows may not re-read a wallpaper whose path did not change, so the
    new render goes to whichever slot is not currently shown.
    """
    key = hashlib.sha1(monitor.id.key.encode()).hexdigest()[:12]
    slots = [render_dir / f"{key}-{slot}{RENDER_SUFFIX}" for slot in (0, 1)]
    current = monitor.wallpaper
    if current is not None and Path(current) == slots[0]:
        return slots[1]
    return slots[0]


def apply_wallpaper(
    backend: Backend,
    monitor_id: MonitorId,
    image: Path,
    look: Look,
    render_dir: Path,
) -> Path:
    """
    Render `image` with `look` at the monitor's exact resolution and set the
    render on it; returns the render's path.

    The backend only ever gets exact-size images: they look the same in any
    system position, so every look works on every platform.

    Raises:
        MonitorNotAttachedError: the monitor is not attached right now, or
            its resolution changed while rendering.
        ImageLoadError: the image can't be loaded.
    """
    monitor = find_attached(backend, monitor_id)
    assert monitor.rect is not None
    size = monitor.rect.size
    destination = rendered_path(render_dir, monitor)
    rendered = render(load(image, size), size, size, look)
    destination.parent.mkdir(parents=True, exist_ok=True)
    rendered.save(destination, "BMP")
    # Re-check: rendering a big image takes time, the monitor may have gone,
    # and its size may have changed.
    current = find_attached(backend, monitor_id)
    if current.rect != monitor.rect:
        raise MonitorNotAttachedError(
            f"{current.name} changed resolution while rendering; try again"
        )
    backend.set_wallpaper(current.id, destination)
    return destination
