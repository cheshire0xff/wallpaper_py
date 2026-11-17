# Architecture

## Words

The names say what things are. The product is wallpaper_py; **app** is
only Qt's `QApplication` and the `app/` package.

| Name | What it is |
|---|---|
| **daemon** | `wallpaper tray` / `wallpaper run`: runs all session, holds the instance lock, serves the socket, rotates; with `tray` it also shows the icon |
| **window process** | `wallpaper ui`: the window, on demand |
| **main thread** | Qt's main thread, in either process; the daemon's holds the tray |
| **service thread** | the daemon's thread that runs `PlaylistService` |
| **`ServiceThread`** | the main thread's handle on the service thread |
| **`PlaylistService`** | the core object; "`PlaylistService` is stopped" while the daemon keeps answering |

## Layers

```
cli.py                        command line; playlist commands go to the daemon (never
                              started here) and their text is formatted here from the reply data
app/                          PySide6 (Qt only here); two processes
  runtime.py     the daemon: lock, service thread, screens, tray
  worker.py      ServiceWorker (owns the service and the socket, on its own
                 QThread) + ServiceThread (the main thread's handle) and its
                 LocalClient (the tray's Client: queued signals, no socket)
  client.py      Client: all the tray and the pages may use (commands,
                 events); SocketClient (the window process's);
                 ReplyTo, OneAtATime (a request asked again only once)
  ipc.py         sockets: line framing (LineBuffer, for CommandServer, LineRequest,
                 blocking send), the daemon's CommandServer (commands,
                 subscriptions, which one is the window)
  processes.py   command lines, starting detached, logging, Ctrl+C (both processes)
  instance.py    single instance: a QLockFile next to the state database
  screens.py     Qt screen signals -> one debounced extra tick
  tray.py        tray icon and menu, drawn from an Overview
  icon.py        the bundled app icon and tray-state variants
  icons/         shared app icon image, package data
  ui/            the window process (`wallpaper ui`), on demand
    process.py   run_ui: subscribes as the window (the daemon picks one), shows
                 it, quits with the daemon / window
    window.py    MainWindow: the sidebar (NavButton per page, the daemon's status)
                 and the pages; asks before closing with unsaved settings
    home.py      Home: the monitors on a panel, then a row per playlist
    library.py   Library: the count and a playlist drop-down, the tabs; reads
                 what each grid tab shows (or asks `upcoming`), asks `stats`
                 and `reset_cycle`; owns the grids' thumbnails
    library_grids.py  GridTab: a grid tab's toolbar (filter, sort, chips),
                 its grid and fast-scroll bar
    library_stats.py  StatsTab: a playlist's numbers, Rescan, Reset cycle…
    enlarged.py  EnlargedView: one image over the page, read at full size,
                 zoom and pan (ImageCanvas), the info card, the toggles, Show on
    entries.py   Entry (a tile: path, order, keys worked out once), the
                 readers (folder, lists, history), sorting and filtering
    grid.py      ImageGrid: a QListView of fixed-size tiles over a list model
                 of entries, tiles fading in, badges; FastScroll
    tiles.py     TileLoader: the grid's thumbnails (newest request first,
                 dropped off screen, a memory budget); Pregenerator: fills
                 and prunes the disk cache while the window is open
    ui_state.py  UiState: the window's view state (ui-state.json), not settings
    settings.py  Settings, monitor-first: the selected monitor's playlist and
                 look, then the playlists; General; About tab in about.py
    settings_form.py  its rows: SettingRow, PlaylistEditor (open or a summary
                 row), field problems
    settings_look.py  LookEditor: the selected monitor's look rows (mode,
                 background, blur and darkening)
    settings_draft.py  the edit without widgets (Draft): problems per field
                 (its texts read here, the rest core/settings.py's checks),
                 monitor labels, what changed
    arrangement.py  the monitors drawn to scale (arrange(): pure geometry),
                 each with its preview (the renderer's, at a small output)
    jobs.py      Jobs: background work on a thread pool of its own, results
                 delivered on the main thread (every pool of the window process);
                 LatestRead: only the latest of a kind of read is delivered
    thumbnails.py   ThumbnailLoader: a small copy of each image, its thumbnail
                 and details, off the main thread
    theme.py     the shared look: tones derived from the palette, spacing,
                 tinted icons, the focus ring, painted widgets (IconButton,
                 TextButton, Tabs, Divider, Panel, Banner, Chip, ImageView)
    header.py    PageHeader: a page's header band, its title and its controls
    controls.py  the controls painted in code: FieldCombo (drop-down and its
                 list), Segments, Switch, FilterChip, Button, Slider
    icons/       bundled Lucide SVGs (ISC licence alongside), package data
commands.py                   runs a request on the service (one `match`, a case per
                              request model) -> a reply model (+ the change made);
                              overview(). Qt-free
protocol.py                   the socket protocol as pydantic models: requests, one
                              result per command, errors, events, Overview. Qt-free
texts.py                      what the tray, Home and the CLI say about a playlist or
                              a monitor, made from those models. Qt-free
        │
core/                         pure Python, no Qt, no platform APIs
  model.py       Monitor, MonitorId, Rectangle, ImageMode, Look, error types
  backend.py     Backend protocol (the only thing platforms implement)
  selection.py   user-typed monitor selector -> Monitor (names/ids, never numbers)
  setter.py      put an image on a monitor safely (renders it, re-checks)
  imaging.py     render(image, screen, output, look): fill / fit / stretch;
                 load, load_small (a preview's source), load_filled (a grid
                 tile's), preview_size
  thumbnail_cache.py  the grid's thumbnails on disk: one JPEG per image and
                 tile size, named by path, mtime and size; prune
  paths.py       per-user data, log and cache locations (per OS, never shared)
  settings.py    Settings (pydantic): playlists, monitors (playlist, look), pausing,
                 list files; their checks (every problem with its place, for the
                 form and the daemon alike) and warnings; durations
  library.py     recursive image scan (natural order, cached), path keys
  order.py       next image: sorted walk, or the persisted shuffle deck
  store.py       SQLite: the settings, history, deck, per-playlist and
                 per-monitor state
  marks.py       favourites / to-remove .txt files (the only copy); decode_text
  presence.py    Presence protocol: session locked? seconds idle?
  autostart.py   Autostart protocol: start the daemon at login
  service.py     PlaylistService: tick, next / previous, fav / remove, status,
                 stats, reset_cycle
        │
backends/                     one per platform, thin
  __init__.py    get_backend() / get_presence() / get_autostart(),
                 play_sound(), and thread_scope(): per-thread setup (COM)
                 for the backend's thread
  windows/       IDesktopWallpaper (COM) + EDID names via QueryDisplayConfig,
                 lock / idle via user32 (presence.py), Run key (autostart.py),
                 the rotation sound via winsound (sound.py)
  xdg.py         XDG autostart entry (any Linux desktop)
  pipewire.py    the rotation sound via `pw-play`, else `paplay` (any Linux)
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
  `protocol.py`, `commands.py`, `texts.py` and `core` stay Qt-free and
  synchronous.
- `app/__init__.py` loads `protocol.py` (all of pydantic) before any Qt
  module: PySide6's import hook breaks pydantic's lazy imports if pydantic
  was only partly loaded before Qt.
- Qt for Python rules: AGENTS.md, "Qt for Python rules", each with its
  reason; `tests/test_qt_rules.py` checks them.

## App shell

How it is wired (processes, threads, flows): [app-wiring.md](app-wiring.md).
The socket's messages: [protocol.md](protocol.md). What each module does:
its docstring. Here: the decisions, and why.

**Two processes.** The daemon (service thread, tray, command socket) always
runs and stays small. The windows run in the window process (`wallpaper ui`),
started on demand and ended when the window closes: only ending a process
surely gives its memory (the grids' thumbnails) back, as memory freed
inside a running one may stay with it (allocator, fragmentation). Each
runs at most once: the daemon holds an instance lock next to the state
database (instance.py: the socket alone can't ensure it on Windows).

**The daemon knows the window.** At most one window process has the
window, and the daemon's socket decides which: a window process subscribes
as the window (it subscribes for the events anyway), and the first one
is; a later one has its page shown there and exits. The tray asks the
socket to show the window (`show_window`) and starts `wallpaper ui` only
if none has it. So there is one socket and one lock: no socket of the
window process's own, no lock file for it, and no race between two
starting at once, as the service thread takes their subscriptions one at
a time. The socket answers these requests itself, not `PlaylistService`,
so the window opens while it is stopped (to show why). The cost: a tray
click waits for whatever runs on the service thread. Measured before
building (the slow-work log, 2026-11-16): commands and ticks of 100 to
400 ms, at most ~600 ms in a burst of *Next*; a first scan of a very big
folder would be longer, once.

**One thread owns the service.** A change renders for ~200 ms, which must
never happen on the main thread, so the `PlaylistService` lives on a worker
`QThread` with everything it uses (worker.py). Socket requests, tray
actions and ticks all run there through `ServiceWorker.execute`, one at a
time, so the core needs no locks; nothing there may block on a client or
spin a nested event loop, which would hold up the ticks and every other
client. The socket is made once and closed at stop, so it keeps answering
(with the error) while `PlaylistService` is stopped. As everything there
waits its turn, a command or tick over 100 ms is logged with its time:
the daemon's log shows what a click could have waited for.

**The tick is the source of truth.** Every 5 s it re-reads the real
state; Qt's screen signals only trigger one extra tick (screens.py), and
lock / unlock and sleep are noticed by the tick itself (native
notifications are decided against: plan.md). A missed or early signal
costs at most one tick.

**Replies are data.** One pydantic model per command (protocol.py): the
CLI makes its text from it, the tray and the windows show the same
models, so a command is written once for all of them.

**The CLI is a plain client and never starts the daemon.** Without one, a
playlist command fails with `NOT_RUNNING` ("`wallpaper tray` starts it") and
exits non-zero: it never runs a command itself, so only the daemon rotates and
the instance lock (instance.py) has one kind of holder. Monitor commands
(`list`, `set`) need no daemon (cli.py).

**Errors stop only what they must.** A `WallpaperError` fails one
operation and the rest goes on; a `ConfigError` (a list file that can't be
used) stops `PlaylistService`, wherever it comes from (`ServiceWorker._failed`),
and every tick tries to open it again, so it runs once the file is fixed.
Settings that fail the checks are never stored, so they can't stop it.

**The tray and the windows are clients like the CLI.** They see only
`Client` (client.py), never `PlaylistService`: the same commands, replies
and events as any other client, and tests give them a fake (`FakeClient`).
The tray's is a `LocalClient` (worker.py), in the daemon: queued signals
to and from the service thread, the models passed as they are (frozen, so
safe to share between threads). Not over the socket: both ends would sit
in the daemon, a socket pair per command to pass messages between two of
its threads. The service thread sends each event once, as the protocol's
`Event` (`ServiceWorker._send`), to both kinds of listener, and both
clients turn it into the same signals (`Client._dispatch`).

The windows read images from the files, never sent over the socket; for
the library's grids the window process also reads the folders, the list
files and the history itself (read-only, off the main thread; the history
through a read-only SQLite connection, WAL). Writes are always commands.
Its view state (ui_state.py) is not settings: no revision, nothing the
daemon reads.

**What the windows and the tray show is the daemon's.** They redraw from
overviews and never poll; a toggle shows the overview's state, never the
click.

**One look on Windows and KDE.** The tones are derived from the system's
palette (light / dark, the accent) and the controls are painted here
(theme.py, controls.py), with no stylesheets, so the app looks the same on
both and still follows the system. The rules: docs/design/README.md, "The
look".

**Two image pipelines.** Home and Settings show a few images, the grids
thousands. `ThumbnailLoader` decodes one small copy per image, which the
monitor previews render from; merging it with the grids' would split that
one decode into two. The grids' `TileLoader` reads tiles from a disk cache
(tiles.py, core/thumbnail_cache.py), newest request first, dropping tiles
scrolled away, within a memory budget (no setting). A tile is the image
filling it, cropped as Fill shows it, so every tile looks alike (the
user's choice, as in the phone app).

**The enlarged view shows no thumbnail while it reads.** A tile is a
blurry 16:9 crop: drawn while the image decodes, it flashes low-res and
then jumps to the image's own shape. Most images decode in a few tens of
ms anyway, so the image before stays a moment instead (`HOLD`, enlarged.py)
and a slow file shows the panel.

**Previews are virtual monitors.** `MonitorsView` renders each monitor's
image with the app's own renderer (`imaging.render`), for the monitor's
real screen size and its look, at a small output: every look shows as it
will on the screen, with no preview code of its own, and Settings draws
the look as edited, so an unsaved change shows at once. No small preview
shows sharpness or pixel-level effects. A known limitation, on purpose:
the small copies are decoded for the sharpest screen when the window
opens, so a sharper screen attached later shows softer previews until the
window is opened again; decoding again while it is open would add
concurrency for a cosmetic issue that reopening fixes.

**The settings are one JSON document** in the state database, written only
by the daemon (`save_settings`, refused if the revision moved on since they
were read); there is no settings file. They are only ever read and written
whole, so a new setting with a default loads from a database saved before
it, with no new schema; renaming or removing one still needs one (unknown
fields are refused). What changes piece by piece (each playlist's state,
history, the shuffle decks, the applied images) stays in tables
(store.py).

**The settings are checked once**, in core/settings.py: each check finds
every problem with its place (playlist, field). The Settings page shows
them next to their fields before it sends anything, and the daemon raises
the first when it saves: one wording, and the page can't let through what
the daemon refuses. The page itself only reads its texts (durations, a
folder chosen, a list file that is a folder).

## Playlist service

State lives in two places, by design:

- **SQLite** (`store.py`) holds the settings (`core/settings.py`) and the
  app-internal state: history with a cursor per playlist (ordered by an explicit `position`, not by id, so a
  pick can go between older entries), the shuffle deck, active seconds
  since the last change, the hold flag, and which image each monitor was
  given. It is the single source of truth. Whoever holds the instance lock is
  its only writer: the daemon, or a CLI command run while no daemon runs (with
  the daemon running, CLI commands go through its socket). WAL mode
  lets the library window read the history alongside (`read_history`,
  read-only). Pre-release, the schema
  changes in place: a database of another schema version is refused with
  a message to delete it (the settings, history and shuffle state in it
  are lost).
- **Favourites / to-remove** live only in their `.txt` files. Every change
  re-reads the file, applies one add or remove and writes it atomically;
  reads reload when the file's mtime/size changes, and the daemon tells
  subscribers (`marks_changed`) when that moved since its last tick or
  command. Entries for missing images are hidden, never pruned
  automatically. A file that has never
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
image. The daemon drops all of them when it opens the service (at start, or
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
monitor whose look or playlist `save_settings` changed; the daemon ticks
before replying, so a changed look shows at once.

The look is per monitor (`MonitorSettings.look`, `Settings.look`), the
timing per playlist: how an image looks depends on a screen's shape,
while mirrored monitors must change together.

Fit's bars are filled with the look's `background`: a fixed colour, one
taken from the image (its average, its most common one, its edges next to
the bars: imaging.py), or `blur`, the image as Fill would show it, blurred
and darkened under the sharp one. How much blur and darkening
(`Look.blur`, `Look.darken`) is the monitor's setting, as it is taste and
depends on the screen. Automatic colours come from a fixed tiny copy of
the image (`imaging.TINY`), never from the output, so the wallpaper (from
the file) and its preview (from the small copy) get the same colour.
Except, rarely, `dominant` when two colours are about as common: the
copies differ by a few levels, and each may pick another (accepted).
Blur's radius is a part of the screen's longer side, so a preview has the
same blur, smaller; it is worked out at a size where the radius is a few
pixels and enlarged (the same to the eye, 5x faster at 4K). The
background is kept in the other modes, unused, so going back to Fit
brings it back.
