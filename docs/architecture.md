# Architecture

## Layers

```
cli.py                        command line; playlist commands go to the app (started if needed)
app/                          PySide6 app shell (Qt only here)
  runtime.py     wires it up: lock, service thread, screens, tray
  worker.py      ServiceWorker (owns the service and the socket, on its own
                 QThread) + ServiceHost (the UI thread's proxy)
  ipc.py         command socket (QLocalServer), pydantic messages, blocking client
  instance.py    single instance: a QLockFile next to the state database
  screens.py     Qt screen signals -> one debounced extra tick
  tray.py        tray icon and menu, drawn from an Overview
  icon.py        the icon, drawn in code
commands.py                   commands as data: Request -> Reply (+ the change made),
                              run by the app; Overview (what the tray shows). Qt-free
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
  autostart.py   Autostart protocol: start the app at login
  service.py     PlaylistService: tick, next / previous, fav / remove, status
        │
backends/                     one per platform, thin
  __init__.py    get_backend() / get_presence() / get_autostart(), and
                 thread_scope(): per-thread setup (COM) for the backend's thread
  windows/       IDesktopWallpaper (COM) + EDID names via QueryDisplayConfig,
                 lock / idle via user32 (presence.py), Run key (autostart.py)
  xdg.py         XDG autostart entry (any Linux desktop)
  (kde/)         planned: plasmashell D-Bus + KScreen
```

Rules:

- **Dependencies point down.** `core` never imports `backends`, Qt or a
  platform API. Code that needs a backend receives it as a parameter (the CLI
  gets a `backend_factory`), so everything above the backend is tested with
  `tests/fakes.py`.
- **Platform modules are guarded** with `assert sys.platform == "win32"`, so
  mypy skips them on other platforms and importing them elsewhere fails fast.
- Qt is used only in `app/` (and by the CLI's socket client, `app/ipc.py`).
  `commands.py` and `core` stay Qt-free and synchronous.

## App shell

Every thread, signal and connection, with the request / tick / shutdown
flows: [app-wiring.md](app-wiring.md).

One process rotates: `wallpaper tray` (with the tray icon) or `wallpaper
run` (headless, in a terminal) start the same app. It holds a `QLockFile`
next to the state database (the socket alone can't enforce this: on
Windows a second `QLocalServer` can listen on the same pipe name) and
listens on a per-user `QLocalServer` (name derived from the data
directory).

Threads: the `PlaylistService` lives on a worker `QThread`, which creates
everything it uses there (the command socket, COM via
`backends.thread_scope()`, the backend, the SQLite connection, the 5 s tick
`QTimer`). A change renders for ~200 ms, which must never happen on the UI
thread. The UI thread holds only UI (tray, screen watcher, later the
windows) and `ServiceHost`, a thin proxy: tray actions, extra ticks and
reload reach the worker as queued signals; replies, changes and the
`Overview` come back the same way. Socket requests need no hop: the socket
lives on the service thread and calls `ServiceWorker.execute` directly, the
one entry point for every command. Commands run one at a time between
ticks, in order, so the core needs no locking; nothing on that thread may
block on a client or spin a nested event loop. A command that changes a
wallpaper (`next`, `prev`, `remove`) emits `changed` like a tick does. The
socket is created once at start and closed at stop, so it keeps answering
across config reloads. The UI waits on the worker only at shutdown.

Ticks: the 5 s tick is the source of truth (it re-reads the real state).
Qt screen signals only restart a 2 s single-shot timer that triggers one
extra tick, so a returning monitor gets its image without waiting. Lock /
unlock and sleep / resume are noticed by the tick itself (WTS flag, tick
gap); no native notifications.

CLI: playlist commands always run in the app, sent as one line of JSON
(`ipc.py`, pydantic models) and run with `commands.execute()`. If no app
answers, the CLI starts the tray app detached (the autostart command;
`pythonw` on Windows, a new session elsewhere, so it outlives the CLI) and
retries for up to 10 s; if it doesn't come up, it points to the log file.
Two CLIs racing both start an app; the instance lock keeps one and both
reach it. `list` and `set` don't need the app. A request naming a
different config file than the app's is refused. The client blocks
without an event loop; PySide keeps the GIL while it waits, so it must run
in a different process from the server (tests use child processes,
`tests/clients.py`).

Config errors don't stop the app: it shows the error in the tray and
answers commands with it until *Reload config* succeeds.

## Playlist service

State lives in two places, by design:

- **SQLite** (`store.py`) holds app-internal state only: history with a
  cursor per playlist, the shuffle deck, active seconds since the last
  change, and which image each monitor was given. It is the single source
  of truth. The app is its only writer (CLI commands go through its
  socket, starting the app if needed). WAL mode lets later read-only
  windows read alongside.
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
image. The app drops all of them when it starts, so after a reboot every
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
