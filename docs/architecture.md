# Architecture

## Layers

```
cli.py, (later) app/          user interfaces: CLI now, PySide6 tray + windows later
        │
core/                         pure Python, no Qt, no platform APIs
  model.py       Monitor, Rectangle, ImageMode, error types
  backend.py     Backend protocol (the only thing platforms implement)
  selection.py   user-typed monitor selector -> Monitor (names/ids, never numbers)
  setter.py      put an image on a monitor safely (re-checks, renders if needed)
  imaging.py     fill / fit / stretch rendering to an exact size
  paths.py       per-user data and cache directories (per OS, never shared)
        │
backends/                     one per platform, thin
  __init__.py    get_backend(): picks by platform / XDG_CURRENT_DESKTOP
  windows/       IDesktopWallpaper (COM) + EDID names via QueryDisplayConfig
  (kde/)         planned: plasmashell D-Bus + KScreen
```

Rules:

- **Dependencies point down.** `core` never imports `backends`, Qt or a
  platform API. Code that needs a backend receives it as a parameter (the CLI
  gets a `backend_factory`), so everything above the backend is tested with
  `tests/fakes.py`.
- **Platform modules are guarded** with `assert sys.platform == "win32"`, so
  mypy skips them on other platforms and importing them elsewhere fails fast.
- Later phases add playlists, history and a scheduler in `core`, and the
  PySide6 app in `app/`, which calls into `core`.

## Monitor identity

A monitor is identified only by `Monitor.id` (on Windows the device path,
e.g. `\\?\DISPLAY#DELA246#...`). Measured on the developer's machine (see
`spikes/README.md`), switching the OLED off:

- removes it from the list and **re-indexes** the remaining monitors,
- **moves** the remaining monitors (positions change),
- then lists it again under the same device path, but detached.

So neither index nor position is an identity. `Monitor.rect is None` means
known-but-detached. Consequences in code:

- `selection.resolve_monitor` refuses numbers.
- `setter.apply_wallpaper` re-queries the backend right before setting, and
  again after rendering (which takes time); it raises
  `MonitorNotAttachedError` instead of setting if the monitor is gone or its
  resolution changed. Callers (later: the scheduler) postpone on that error.

## Image modes

Windows has one mode for all monitors (`per_monitor_modes = False`). To give
each monitor its own fill/fit/stretch, `setter` renders the image to the
monitor's exact resolution; such an image looks the same in any of those
modes. Renders alternate between two files per monitor, because Windows may
ignore a wallpaper whose path did not change. KDE will set the mode per
monitor natively and skip rendering.
