"""
Phase 0 spike: what do the OS and Qt report about monitors, and what changes
when a monitor (e.g. an OLED doing a panel refresh) is switched off?

Read-only: never changes wallpapers.

Usage:
    python spikes/monitor_probe.py            # one snapshot
    python spikes/monitor_probe.py --watch    # keep running, log every change

Watch mode writes to spikes/monitor_probe.log as well as stdout.
Suggested test: start --watch, switch the OLED off, wait ~1 min, switch it
back on, wait ~1 min, Ctrl+C, send the log.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import platform
import signal
import sys
import time
from pathlib import Path
from typing import Any

LOG_PATH = Path(__file__).with_name("monitor_probe.log")
IS_WINDOWS = platform.system() == "Windows"


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def dpi_awareness() -> str:
    if not IS_WINDOWS:
        return "n/a"
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    ctx = user32.GetThreadDpiAwarenessContext()
    value = user32.GetAwarenessFromDpiAwarenessContext(ctx)
    return {0: "unaware", 1: "system-aware", 2: "per-monitor-aware"}.get(
        value, str(value)
    )


def com_snapshot() -> list[dict[str, Any]]:
    """Everything IDesktopWallpaper reports, including detached monitors."""
    if not IS_WINDOWS:
        return []
    # pylint: disable=import-outside-toplevel
    from comtypes import COMError

    from wallpaper_py.windows.desktop_wallpaper import IDesktopWallpaper

    dw = IDesktopWallpaper.co_create_instance()
    result: list[dict[str, Any]] = []
    for index in range(dw.get_monitor_device_path_count()):
        entry: dict[str, Any] = {"index": index}
        try:
            monitor_id = dw.get_monitor_device_path_at(index)
            entry["id"] = monitor_id
        except COMError as err:
            entry["id_error"] = hex(err.hresult & 0xFFFFFFFF)
            result.append(entry)
            continue
        try:
            r = dw.get_monitor_rect(monitor_id)
            entry["rect"] = [r.x1, r.y1, r.x2, r.y2]
            entry["size"] = f"{r.get_width()}x{r.get_height()}"
        except COMError as err:
            entry["rect_error"] = hex(err.hresult & 0xFFFFFFFF)
        try:
            entry["wallpaper"] = dw.get_wallpaper(monitor_id)
        except (COMError, AssertionError) as err:
            entry["wallpaper_error"] = repr(err)
        result.append(entry)
    return result


def qt_snapshot(app: Any) -> list[dict[str, Any]]:
    result = []
    for screen in app.screens():
        g = screen.geometry()
        result.append(
            {
                "name": screen.name(),
                "manufacturer": screen.manufacturer(),
                "model": screen.model(),
                "serial": screen.serialNumber(),
                "geometry_logical": [g.x(), g.y(), g.width(), g.height()],
                "dpr": screen.devicePixelRatio(),
                "native_size": f"{round(g.width() * screen.devicePixelRatio())}x"
                f"{round(g.height() * screen.devicePixelRatio())}",
                "primary": screen == app.primaryScreen(),
            }
        )
    return result


def dump(title: str, data: Any) -> None:
    log(f"--- {title}")
    for item in data:
        log("    " + json.dumps(item, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()

    log(f"===== probe start, platform={platform.platform()}")

    # 1) Before Qt exists: what the current CLI sees (process not DPI-aware).
    if IS_WINDOWS:
        dump(f"COM before Qt (dpi: {dpi_awareness()})", com_snapshot())

    # 2) After Qt: Qt makes the process per-monitor DPI aware.
    # pylint: disable=import-outside-toplevel
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication(sys.argv)
    if IS_WINDOWS:
        dump(f"COM after Qt (dpi: {dpi_awareness()})", com_snapshot())
    dump("Qt screens", qt_snapshot(app))

    if not args.watch:
        return

    log("watching; switch monitors off/on now. Ctrl+C to stop.")
    state = {"com": com_snapshot(), "qt": qt_snapshot(app)}

    def on_screen_event(kind: str, screen: Any) -> None:
        log(f"Qt signal: {kind} {screen.name()} {screen.model()}")

    def connect_screen(screen: Any) -> None:
        screen.geometryChanged.connect(
            lambda _g, s=screen: on_screen_event("geometryChanged", s)
        )

    app.screenAdded.connect(lambda s: (on_screen_event("screenAdded", s), connect_screen(s)))
    app.screenRemoved.connect(lambda s: on_screen_event("screenRemoved", s))
    app.primaryScreenChanged.connect(lambda s: on_screen_event("primaryScreenChanged", s))
    for s in app.screens():
        connect_screen(s)

    def poll() -> None:
        new = {"com": com_snapshot(), "qt": qt_snapshot(app)}
        for key in ("com", "qt"):
            if new[key] != state[key]:
                dump(f"{key.upper()} changed", new[key])
        state.update(new)

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(int(args.interval * 1000))
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    # Let Python see Ctrl+C while Qt's loop runs.
    keepalive = QTimer()
    keepalive.timeout.connect(lambda: None)
    keepalive.start(200)
    app.exec()
    log("===== probe stop")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    main()
