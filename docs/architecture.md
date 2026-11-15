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
  icon.py        the bundled app icon and tray-state variants
  icons/         shared app icon image, package data
  ui/            the UI process (`wallpaper ui`), on demand
    process.py   run_ui: single instance, the window, quits with the app / window
    launcher.py  its own socket: "show this page" (UiServer, WindowOpener)
    client.py    Client: all the pages may use (commands, events); SocketClient
    window.py    MainWindow: the sidebar (NavButton per page, the app's status)
                 and the pages; asks before closing with unsaved settings
    home.py      Home: the monitors on a panel, then a row per playlist
    library.py   Library: a playlist drop-down, the tabs; asks `stats` and
                 `reset_cycle` (the grid tabs come later in Phase 5)
    library_overview.py  OverviewTab: a playlist's numbers, Rescan, Reset cycle…
    ui_state.py  UiState: the window's view state (ui-state.json), not settings
    settings.py  Settings, monitor-first: the selected monitor's playlist and
                 look, then the playlists; General; About tab in about.py
    settings_form.py  its rows: SettingRow, PlaylistEditor (open or a summary
                 row), field problems
    settings_look.py  LookEditor: the selected monitor's look rows (mode,
                 background, blur and darkening)
    settings_draft.py  the edit without widgets (Draft): problems per field,
                 monitor labels, what changed
    arrangement.py  the monitors drawn to scale (arrange(): pure geometry),
                 each with its preview (the renderer's, at a small output)
    thumbnails.py   ThumbnailLoader: a small copy of each image, its thumbnail
                 and details, off the UI thread
    theme.py     the shared look: tones derived from the palette, spacing,
                 tinted icons, painted widgets (IconButton, TextButton, Tabs,
                 Divider, Panel, Chip, ImageView)
    icons/       bundled Lucide SVGs (ISC licence alongside), package data
commands.py                   runs a Request on the service -> a reply model (+ the
                              change made); overview(). Qt-free
protocol.py                   the socket protocol as pydantic models: requests, one
                              result per command, errors, events, Overview. Qt-free
        │
core/                         pure Python, no Qt, no platform APIs
  model.py       Monitor, MonitorId, Rectangle, ImageMode, Look, error types
  backend.py     Backend protocol (the only thing platforms implement)
  selection.py   user-typed monitor selector -> Monitor (names/ids, never numbers)
  setter.py      put an image on a monitor safely (renders it, re-checks)
  imaging.py     render(image, screen, output, look): fill / fit / stretch;
                 load, load_small (a preview's source), preview_size
  paths.py       per-user data, log and cache locations (per OS, never shared)
  settings.py    Settings (pydantic): playlists, monitors (playlist, look), pausing,
                 list files; their checks and warnings; durations
  library.py     recursive image scan (natural order, cached), path keys
  order.py       next image: sorted walk, or the persisted shuffle deck
  store.py       SQLite: the settings, history, deck, per-playlist and
                 per-monitor state
  marks.py       favourites / to-remove .txt files (the only copy); decode_text
  presence.py    Presence protocol: session locked? seconds idle?
  autostart.py   Autostart protocol: start the app at login
  service.py     PlaylistService: tick, next / previous, fav / remove, status,
                 stats, reset_cycle
        │
backends/                     one per platform, thin
  __init__.py    get_backend() / get_presence() / get_autostart(), and
                 thread_scope(): per-thread setup (COM) for the backend's thread
  windows/       IDesktopWallpaper (COM) + EDID names via QueryDisplayConfig,
                 lock / idle via user32 (presence.py), Run key (autostart.py)
  xdg.py         XDG autostart entry (any Linux desktop)
  kde/           plasmashell's scripting over D-Bus (evaluateScript), KScreen
                 outputs, EDID names from sysfs, lock via the ScreenSaver
                 interface; through `kscreen-doctor` and `busctl` (tools.py)
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
  - A method that does override a Qt virtual (an event handler,
    `QRunnable.run`) is marked `@override`, like every override (mypy's
    `explicit-override`): a misspelt one, which Qt would silently never
    call, is a mypy error.
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
thread. The UI thread holds only UI (tray, screen watcher) and `ServiceHost`, a thin proxy: tray actions and extra ticks
reach the worker as queued signals; replies, changes and the
`Overview` come back the same way. Socket requests need no hop: the socket
lives on the service thread and calls `ServiceWorker.execute` directly, the
one entry point for every command. Commands run one at a time between
ticks, in order, so the core needs no locking; nothing on that thread may
block on a client or spin a nested event loop. A command that changes a
wallpaper (`next`, `prev`, `pick`, `remove`) emits `changed` like a tick
does; after every command the overview is published if it changed (that is
how `hold` / `resume` reach the tray and subscribers). The
socket is created once at start and closed at stop, so it keeps answering
while the service is down. The UI waits on the worker only at shutdown.

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

CLI: playlist commands run in the app if it runs, sent as one line of
JSON (`ipc.py`, pydantic models) and run with `commands.execute()`. If no
app answers, the CLI takes the app's instance lock and runs the command
itself (the same `commands.execute()`, no tick first), then exits: it never
starts the app, so nothing keeps running after a command. The lock keeps
one writer: two CLIs take turns, and an app starting meanwhile waits up to
5 s for it. `list` and `set` don't need the app. The client blocks
without an event loop; PySide keeps the GIL while it waits, so it must run
in a different process from the server (tests use child processes,
`tests/clients.py`).

A list file that can't be used doesn't stop the app: a `ConfigError` stops
the service wherever it comes from (opening it, a tick, a command, the
overview, e.g. a list file saved with a BOM while running), all through
`ServiceWorker._failed`. The tray shows the error and commands answer with
it; every tick tries to open the service again, so it runs once the file
is fixed. Settings that fail the checks are never stored, so they can't
break it.

Two kinds of expected error, both `AppError`: a `WallpaperError` fails one
operation and the rest goes on; a `ConfigError` (not a `WallpaperError`, so
`except WallpaperError` lets it through) stops the service.
A list file that won't decode but changed in the last few seconds may be
half-written by a script: it is busy, not broken, until it settles.

UI process (`wallpaper ui [home|library|settings]`, `app/ui/`): one main
window with a sidebar (Home, Library, Settings) runs in a process of its
own, started on demand and ended when the window closes, so whatever it
holds (later: thumbnails) is given back; the app never holds it. At most
one runs: it listens on its own small socket (`UiServer`, "show this
page") while holding `ui.lock`. The tray's *Open wallpaper_py*, *Library*
and *Settings…* ask that socket without blocking (`WindowOpener`) and
start `wallpaper ui <page>` detached if nothing answers; a starting UI
process asks it too and exits if another one answered. A running window
comes to the front on the page asked for. The UI process is a client of
the app's socket like the CLI: it subscribes (starting the app first if
none runs, as the CLI does) and quits when the subscription ends, i.e.
when the app quits. The pages depend only on `Client` (`run` a command
with a reply callback; `overview_changed`, `changed`, `failed`), never on
the service or `ServiceWorker`; reading state for the library's grids
(SQLite read-only, the .txt files, the folders) comes in Phase 5's later
steps. The UI process logs to `ui.log` next to the app's log.

The pages live as long as the window, so switching pages keeps unsaved
settings; closing the window with them asks Save / Discard / Cancel on the
Settings page. The look follows the system (native style, the palette,
light / dark): the tones (page, sidebar line, panels behind images,
filled fields, accent fills) are derived from the application's palette
(`theme.derive_tones`), set on the window as palette roles (Window, Base,
Button, PlaceholderText; nothing else, so a palette change still reaches
it) and derived again when the palette changes; widgets painted here
(sidebar items, rows, tabs, text buttons) read them when they paint. No
stylesheets. In light mode the panels behind images stay dark. Icons are
Lucide SVGs tinted from the tones and re-tinted when they change.

Home redraws from `overview_changed` (which carries each monitor's id,
name and geometry, attached or not) and asks `status` once per new
overview for the schedule, counting the time left down itself in between;
it never polls. Toggles show the overview's state, never the click.
Images are read from the files, not the socket: `ThumbnailLoader` decodes
each once with Pillow at a reduced size, on a thread pool of its own, into
a small copy (covering a square of the preview's longer side) and the
row's thumbnail made from it; it caches by path (never dropping an image
shown now) and drops results for images no longer shown. The window has
one loader, shared by Home and Settings (they show the same images).

The Library page asks the app for a playlist's numbers (`stats`, counted
on the service thread from the cached scan, the shuffle deck and the
lists) only while its Overview tab is on screen, and again on
`overview_changed` and on a `changed` of that playlist; one request at a
time. Which playlist and tab it shows is view state, in `ui-state.json`
(`paths.ui_state`, `ui_state.py`), written only by the UI process: not
settings (no revision, never on the socket, nothing the app reads), and
not `QSettings` (the registry on Windows, outside the data locations
About lists). Missing or unreadable, it is the defaults, logged.

The monitor drawings show previews, not thumbnails: virtual monitors.
`MonitorsView` renders each monitor's image with `imaging.render`, for the
monitor's real screen size and its look, at a small output (480 px on the
longer side, times the display scale), and paints it whole into the
monitor's box. Home draws each monitor's saved look (`DesktopMonitor.look`
in the overview); Settings draws the look as edited, so an unsaved change
shows at once. Renders are cached by image, look and sizes, only while
drawn, and made on the UI thread (a few ms; the decode is the slow part).
No small preview shows sharpness (a small image enlarged on a 4K screen
looks crisp in it) or pixel-level effects. The small copies are decoded
for the screen with the highest scale when the window opens; a sharper
screen attached later shows softer previews until the window is opened
again. A known limitation, on purpose: decoding again while the window is
open (new sizes, the cache dropped, late results refused) would add
concurrency for a cosmetic issue that reopening fixes.

The settings live in the state database (`core/store.py`), written only
by the app: `settings` reads them with a revision, `save_settings` replaces
them as a whole (refused if the revision moved on, or if a value can't be
used) and uses them at once. There is no settings file. They are stored
as one JSON document (`Settings`, as on the socket), since they are only
ever read and written whole: a new setting with a default (general, per
playlist or in a look) loads from a database saved before it, with no new
schema. Renaming or removing one still needs a new schema (unknown fields
are refused). What changes piece by piece stays in tables: each
playlist's state (cursor, time shown, deck position, hold; updated on
every tick), history, the shuffle decks and the applied images.

The Settings page is monitor-first: the drawing (attached monitors only;
a click selects one, outlined), then the selected monitor's rows (a
drop-down that also lists switched-off and not-connected monitors, its
playlist, its mode), then the playlists. It keeps the edit as a `Draft`
(`settings_draft.py`: texts as typed, monitor rows as chosen) and checks
it after every change, per field, as the app will (a new or changed folder
must exist; one already saved may be away). Save sends the whole edit
with the revision it read; the reply carries the new revision and any
warnings. A monitor is never named in the settings: the page names it from
the current listing, numbering monitors that share a name left to right
(`arrangement.twins`), and one the desktop doesn't list by the model code
in its id (`settings_draft.short_id`).

## Playlist service

State lives in two places, by design:

- **SQLite** (`store.py`) holds the settings (`core/settings.py`) and the
  app-internal state: history with a cursor per playlist (ordered by an explicit `position`, not by id, so a
  pick can go between older entries), the shuffle deck, active seconds
  since the last change, the hold flag, and which image each monitor was
  given. It is the single source of truth. Whoever holds the instance lock is
  its only writer: the app, or a CLI command run while no app runs (with
  the app running, CLI commands go through its socket). WAL mode
  lets later read-only windows read alongside. Pre-release, the schema
  changes in place: a database of another schema version is refused with
  a message to delete it (the settings, history and shuffle state in it
  are lost).
- **Favourites / to-remove** live only in their `.txt` files. Every change
  re-reads the file, applies one add or remove and writes it atomically;
  reads reload when the file's mtime/size changes. Entries for missing
  images are hidden, never pruned automatically. A file that has never
  been read (another program holds it) is *unknown*, not empty: while
  to-remove is unknown no image is chosen (ticks wait, `next` / `prev` /
  `pick` fail), and an unknown favourite is `null` in the overview.
- **A playlist's state goes with it.** Saving settings that delete a
  playlist deletes its history, deck and hold in the same transaction; a
  renamed one keeps them (they go by its id). A save that changes a
  playlist's folder resets its shuffle deck and marks it
  (`PlaylistState.folder_changed`, stored, so a restart keeps it): the next
  tick shows
  a new image from that folder, even when held or manual, and the mark
  goes with the next image shown (also by next / prev / pick).

A change is *peek, show, commit*: the next image is chosen without consuming
it, set on every attached member, and only then written to history / the
deck. If no member could be set (`MonitorNotAttachedError`), nothing is
consumed and the change is retried on later ticks. Unreadable images are
skipped (remembered for the process lifetime).

Time is counted per playlist in *active* seconds, added on each tick: not
while paused (locked / idle via `Presence`), not while no member is
attached, and not across gaps longer than a minute (sleep). A manual change
(next / prev / pick) resets the count.

Stats change nothing: `stats` counts a playlist's scanned images as seen
(drawn in this cycle: cards up to the deck position, picked ones moved
there), skipped (listed to remove, or failed to load this run) and
unseen (the rest, new files not yet shuffled in too); a sorted walk counts
the images up to the newest one it showed from the folder as seen.
`reset_cycle` replaces the deck with a new shuffle of the cached scan, the
newest image shown not first (the one `next` avoids too). It reads the
to-remove list before it changes anything, so a busy list leaves the
cycle as it was.

Pick, hold and manual mode: a *pick* shows a chosen image (any directory)
like `next` would, inserted in the history right after the cursor; one
from the playlist's directory is moved among the drawn cards of the
shuffle deck (counts as seen), and a sorted walk goes on after the newest
history entry from the directory (so a pick from elsewhere is a detour).
A *held* playlist (flag in SQLite) and a *manual* one (no interval) never get a new image from a tick, not even a first one, and
their count stands still; ticks still restore the current image on a
returning monitor and replace one that can't be shown any more (listed for
removal, deleted). Hold doesn't apply to a manual playlist.

A member that was detached (or vanished from the monitor list) has its
"applied" record dropped, so on return it is given the group's current
image. The app drops all of them when it opens the service (at start, or
when it comes back after a list file was fixed), so after a reboot every monitor gets its playlist's current
image back, rendered with its current look; `save_settings` drops those of
the monitors whose playlist or look changed.

## Monitor identity

A monitor is identified only by `Monitor.id` (on Windows the device path,
e.g. `\\?\DISPLAY#DELA246#...`). Measured on the developer's machine (see
`spikes/README.md`), switching the OLED off:

- removes it from the list and **re-indexes** the remaining monitors,
- **moves** the remaining monitors (positions change),
- then lists it again under the same device path, but detached.

So neither index nor position is an identity. `Monitor.rect is None` means
known-but-detached.

Ids are `MonitorId` (`core/model.py`), never plain strings: Windows spells
one id in different cases in different APIs (IDesktopWallpaper,
DisplayConfig), and settings saved from an older listing may use either. A `MonitorId` keeps the spelling it was made from (`str()`
gives it back, for the backend and for display) and compares and hashes
case-insensitively, so plain `==`, `in` and dict keys are right everywhere
(the settings, the service, the protocol, the backends); no code
lower-cases an id by hand. In pydantic models (the settings, the protocol)
it reads and writes a plain string; SQLite stores its `key` (case-folded).
Where a protocol model shows a playlist's member (`MonitorView.id`), it is
spelt as the desktop spells it when listed, else as the settings do.

On KDE the id is the connector (`DP-4`): Plasma and KWin go by it, it
stays while the monitor is off, and two monitors of one model differ by it.
Like the Windows device path it belongs to the port (a monitor moved to
another port is a new one). The name comes from the EDID in sysfs
(`/sys/class/drm/card*-DP-4/edid`; the name descriptor, else the maker and
product code as Windows spells it). KScreen lists only the outputs
connected now, so the backend remembers every monitor it saw (with its
name, as EDID is unreadable while off) and lists it as detached, as
Windows does; one connected but disabled, or without a Plasma screen
(mirroring another), is detached too.

`Monitor.rect` is in desktop coordinates, with the screen's size in pixels
beside it (`Rectangle.pixels`, `Geometry.pixels` on the socket). On
Windows they are the same; on Wayland the desktop is logical, so a 4K
screen at 150 % spans 2560x1440 of it but is rendered at 3840x2160
(`Rectangle.size`). The drawings lay monitors out by their area and name
and render them by their pixels. The KDE backend takes the area from
Plasma (`screenGeometry`) and the pixels from KScreen (already rotated).

Plasma is driven by desktop scripts (`evaluateScript`): one per
`monitors()` (each connector's screen, geometry and current image, via
`screenForConnector`) and one per `set_wallpaper`, which sets the image on
that screen's desktop in every activity (so switching activity keeps the
playlist) and keeps its positioning at *Scaled and Cropped* (Fill). The
two render files per monitor are kept here too: a new path makes Plasma
load the new picture instead of trusting one it cached. Both tools run without
`QT_QPA_PLATFORM`: set (offscreen in tests, `xcb` by hand on Wayland),
`kscreen-doctor` picks a KScreen backend that sees no outputs, silently.
Presence on KDE knows only the lock (the ScreenSaver interface's
`GetActive`): on Wayland the compositor keeps idle time to itself, so the
idle pause doesn't apply there.

Consequences in code:

- `selection.resolve_monitor` refuses numbers.
- `setter.apply_wallpaper` re-queries the backend right before setting, and
  again after rendering (which takes time); it raises
  `MonitorNotAttachedError` instead of setting if the monitor is gone or its
  resolution changed. Callers (later: the scheduler) postpone on that error.

## Image modes and looks

Every image is rendered: `setter` renders it to the monitor's exact
resolution with the monitor's `Look` (its mode, and Fit's background) and
hands the render to the backend, which only ever shows exact-size images
and keeps the system's own position at Fill (Windows has one for all
monitors, set only when it differs; KDE does the same per desktop).
So every look works the same on every platform. Renders alternate between
two files per monitor, because Windows may ignore a wallpaper whose path
did not change.

`imaging.render(image, screen, output, look)` separates the screen (whose
size the look is worked out for) from the output (the size drawn): the
app renders with output = screen; the windows' previews render the same
look at a small output, from a small copy of the image
(`imaging.load_small`). A look depends on the screen, never on the output:
tests render at both sizes, from the file and from the small copy, and
compare.

A rendered image depends on the look and the monitor's size, so the
service renders again when either changes: a monitor whose resolution
changed (noticed by the tick) has its "applied" record dropped, and so
does every monitor when the service opens, and a
monitor whose look or playlist `save_settings` changed; the app ticks
before replying, so a changed look shows at once.

The look is per monitor (`MonitorSettings.look`, `Settings.look`), the
timing per playlist: how an image looks depends on a screen's shape,
while mirrored monitors must change together.

Fit's bars are filled with the look's `background`: a fixed colour
(`#rrggbb`); the image's average colour; its most common one (`dominant`:
the pixels sorted into boxes of similar colour, the fullest box's
average); the average of its edges next to the bars (the outer 1/128 on
each side: left and right, or top and bottom, as the screen's shape puts
them); or `blur`, the image as Fill would show it, blurred (and darkened, as set),
under the sharp one: how much blur (`Look.blur`) and darkening
(`Look.darken`) is the monitor's setting, as it is taste and depends on
the screen (Settings: a slider each, with even steps: the radius doubles
every four). Automatic colours come from a fixed tiny copy of
the image (`imaging.TINY`, 128x128, one column or row per edge), never
from the output, so the wallpaper (from the file) and its preview (from
the small copy) get the same colour. Except, rarely, `dominant` when two
colours are about as common: the copies differ by a few levels, and each
may pick another (accepted). Blur's radius is a part of the screen's
longer side, so a preview has the same blur, smaller; it is worked out
at a size where the radius is a few pixels and enlarged (the same to the
eye, 5x faster at 4K). The tests above cover every background, and blur
at both ends of its range. The background is kept in the other modes,
unused, so going back to Fit brings it back; blur and darkening are kept
with the other backgrounds alike. A new look field with a default needs
no new schema (the settings are stored as JSON, "Settings" above).
