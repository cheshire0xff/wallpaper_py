# Architecture

## Layers

```
cli.py                        command line; playlist commands go to the app (started if
                              needed) and their text is formatted here from the reply data
app/                          PySide6 (Qt only here); two processes
  runtime.py     the app: lock, service thread, screens, tray
  worker.py      ServiceWorker (owns the service and the socket, on its own
                 QThread) + ServiceHost (the UI thread's proxy)
  ipc.py         sockets: line framing (LineServer, LineRequest, blocking send),
                 the app's CommandServer (commands, subscriptions)
  processes.py   command lines, starting detached, logging, Ctrl+C (both processes)
  instance.py    single instance: a QLockFile next to the state database
  screens.py     Qt screen signals -> one debounced extra tick
  tray.py        tray icon and menu, drawn from an Overview
  icon.py        the icon, drawn in code
  ui/            the UI process (`wallpaper ui`), on demand
    process.py   run_ui: single instance, windows, quits with the app / last window
    launcher.py  its own socket: "show this window" (UiServer, WindowOpener)
    client.py    Client: all a window may use (commands, events); SocketClient
    window.py    windows (for now a placeholder showing the live Overview)
commands.py                   runs a Request on the service -> a reply model (+ the
                              change made); overview(). Qt-free
protocol.py                   the socket protocol as pydantic models: requests, one
                              result per command, errors, events, Overview. Qt-free
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
  `protocol.py`, `commands.py` and `core` stay Qt-free and synchronous.
- `app/__init__.py` loads `protocol.py` (all of pydantic) before any Qt
  module: PySide6's import hook breaks pydantic's lazy imports if pydantic
  was only partly loaded before Qt.
- Qt for Python rules, checked by `tests/test_qt_rules.py`:
  - Signals connect to bound methods, not lambdas or partials that hold
    `self` (use `sender()`, or data on the sender such as
    `QAction.setData`, for context). Qt keeps such a lambda alive, and the
    object it holds can then be freed from inside the deletion of its own
    child, which deletes that child twice (seen as a random access
    violation in tests).
  - Every method used as a slot has `@Slot(...)`, as Qt for Python
    recommends. Only real slots run in the receiver's thread on a queued
    connection; any other callable runs in the sender's thread.
  - No method shadows an inherited Qt method (e.g. a slot called
    `update` on a widget): PySide then connects it as a plain callable,
    without thread affinity (PYSIDE-2990).
  - `sender()` only for same-thread connections (invalid for a direct
    call from another thread); disconnect only our own connections.
  - The worker's `QThread.started` slot only sets up and returns, so the
    thread's event loop runs the timer and socket; work never runs there.

## App shell

Every process, thread, signal and connection, with the request / tick /
window / shutdown flows: [app-wiring.md](app-wiring.md). The socket's
messages: [protocol.md](protocol.md).

Two processes: the always-running **app** (service thread, tray, command
socket), which stays small, and the **UI process** with the windows, which
runs only while a window is open (see below).

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
wallpaper (`next`, `prev`, `pick`, `remove`) emits `changed` like a tick
does; after every command the overview is published if it changed (that is
how `hold` / `resume` reach the tray and subscribers). The
socket is created once at start and closed at stop, so it keeps answering
across config reloads. The UI waits on the worker only at shutdown.

Ticks: the 5 s tick is the source of truth (it re-reads the real state).
Qt screen signals only restart a 2 s single-shot timer that triggers one
extra tick, so a returning monitor gets its image without waiting. Lock /
unlock and sleep / resume are noticed by the tick itself (WTS flag, tick
gap); no native notifications.

Protocol: replies carry data only (`protocol.py`): one pydantic model per
command, tagged on `command`, or `{"error": {"kind", "message"}}`. The CLI
formats its text from that data; the tray and the windows use the same
models (the tray in-process, through `ServiceHost`). `subscribe` keeps a
connection open and streams one event per line (`changed`,
`overview_changed`), written by the service thread where the socket lives;
the first is the current overview. The protocol only adds things; clients
ignore unknown fields. The JSON Schema (`docs/protocol.schema.json`) is
generated from the models and checked by a test.

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

UI process (`wallpaper ui [layout|library]`, `app/ui/`): the windows run in
a process of their own, started on demand and ended when its last window
closes, so whatever they hold (later: thumbnails) is given back; the app
never holds it. At most one runs: it listens on its own small socket
(`UiServer`, "show this window") while holding `ui.lock`. The tray's *Open
layout / Open library* ask that socket without blocking (`WindowOpener`)
and start `wallpaper ui <window>` detached if nothing answers; a starting
UI process asks it too and exits if another one answered. The UI process is
a client of the app's socket like the CLI: it subscribes (starting the app
first if none runs, as the CLI does) and quits when the subscription ends,
i.e. when the app quits. Windows depend only on `Client` (`run` a command
with a reply callback; `overview_changed`, `changed`, `failed`), never on
the service or `ServiceWorker`; reading state for the later windows (SQLite
read-only, the .txt files) is planned in Phase 5. The UI process logs to
`ui.log` next to the app's log.

## Playlist service

State lives in two places, by design:

- **SQLite** (`store.py`) holds app-internal state only: history with a
  cursor per playlist (ordered by an explicit `position`, not by id, so a
  pick can go between older entries), the shuffle deck, active seconds
  since the last change, the hold flag, and which image each monitor was
  given. It is the single source of truth. The app is its only writer (CLI
  commands go through its socket, starting the app if needed). WAL mode
  lets later read-only windows read alongside. Pre-release, the schema
  changes in place: a database of another schema version is refused with
  a message to delete it (only history and shuffle state are lost).
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
(next / prev / pick) resets the count.

Pick, hold and manual mode: a *pick* shows a chosen image (any directory)
like `next` would, inserted in the history right after the cursor; one
from the playlist's directory is moved among the drawn cards of the
shuffle deck (counts as seen), and a sorted walk goes on after the newest
history entry from the directory (so a pick from elsewhere is a detour).
A *held* playlist (flag in SQLite) and a *manual* one (`interval =
"manual"`) never get a new image from a tick, not even a first one, and
their count stands still; ticks still restore the current image on a
returning monitor and replace one that can't be shown any more (listed for
removal, deleted). Hold doesn't apply to a manual playlist.

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
