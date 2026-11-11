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
  paths.py       per-user config, data and cache locations (per OS, never shared)
  config.py      the human-edited TOML: playlists, monitors, pausing
  library.py     recursive image scan (natural order, cached), path keys
  order.py       next image: sorted walk, or the persisted shuffle deck
  store.py       SQLite: history, deck, per-playlist and per-monitor state
  marks.py       favourites / to-remove .txt files (the only copy)
  presence.py    Presence protocol: session locked? seconds idle?
  service.py     PlaylistService: tick, next / previous, fav / remove, status
        │
backends/                     one per platform, thin
  __init__.py    get_backend(): picks by platform / XDG_CURRENT_DESKTOP
  windows/       IDesktopWallpaper (COM) + EDID names via QueryDisplayConfig,
                 lock / idle via user32 (presence.py)
  (kde/)         planned: plasmashell D-Bus + KScreen
```

Rules:

- **Dependencies point down.** `core` never imports `backends`, Qt or a
  platform API. Code that needs a backend receives it as a parameter (the CLI
  gets a `backend_factory`), so everything above the backend is tested with
  `tests/fakes.py`.
- **Platform modules are guarded** with `assert sys.platform == "win32"`, so
  mypy skips them on other platforms and importing them elsewhere fails fast.
- The PySide6 app (Phase 3+) goes in `app/` and calls into `core`; it
  drives `PlaylistService.tick()` from a timer, as `wallpaper run` does now.

## Playlist service

State lives in two places, by design:

- **SQLite** (`store.py`) holds app-internal state only: history with a
  cursor per playlist, the shuffle deck, active seconds since the last
  change, and which image each monitor was given. It is the single source
  of truth, so a one-shot `wallpaper next` and a running `wallpaper run`
  (two processes, WAL mode) agree without IPC.
- **Favourites / to-remove** live only in their `.txt` files. Every change
  re-reads the file, applies one add or remove and writes it atomically;
  reads reload when the file's mtime/size changes. Entries for missing
  images are hidden, never pruned automatically.

A change is *peek, show, commit*: the next image is chosen without consuming
it, set on every attached member, and only then written to history / the
deck. If no member could be set (`MonitorNotAttachedError`), nothing is
consumed and the change is retried on later ticks. Unreadable images are
skipped (remembered for the process lifetime).

Time is counted per playlist in *active* seconds, added on each tick: not
while paused (locked / idle via `Presence`), not while no member is
attached, and not across gaps longer than a minute (sleep). A manual change
resets the count.

A member that was detached (or vanished from the monitor list) has its
"applied" record dropped, so on return it is given the group's current
image. `wallpaper run` drops all of them at start, so after a reboot every
monitor gets its playlist's current image back.

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
