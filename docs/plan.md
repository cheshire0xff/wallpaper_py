# Plan

One cross-platform Python app (Windows first, then KDE Plasma) built on
PySide6: starts at login straight to the tray, single instance, with a CLI
that talks to it. A Qt-free `core` holds all the logic; platform backends stay
thin. See [architecture.md](architecture.md) for the layers.

| Phase | What | Status |
|---|---|---|
| 0 | Spikes: monitor identity on Windows, 20k-image grid, KDE probe | done |
| 1 | Core model, safe setter, Windows backend, CLI `list`/`set`, tooling, CI | done |
| 2 | Playlists, mirrored groups, scheduler, history, favourites, to-remove | done |
| 3 | App shell: tray, single instance, IPC, autostart | done |
| 4 | Window process, one window (Home, Library, Settings), pick / hold / manual | done |
| 4b | Settings in the database, per-monitor looks, previews, backgrounds | done |
| 4c | More backgrounds for Fit: dominant, blur | done |
| 4d | Blur strength and darkening per monitor; the look stored as one value | done |
| S | Simplification: words, protocol and plumbing, tray / CLI / window as clients | steps 1–4 done |
| 5 | Library page: overview (stats), grids, enlarged view | steps 1–4 done; 5 next, then 6 |
| 6 | KDE backend | backend done; Kubuntu and the checks by hand left |
| 7 | Polish | |

Finished phases are described as they work now, not as they were planned:
[architecture.md](architecture.md) (layers, app shell, playlist service,
monitor identity, looks), [app-wiring.md](app-wiring.md),
[protocol.md](protocol.md), and [design/](design/README.md) for the look.
What is still to try by hand: [desktop-checks.md](desktop-checks.md). The
plans of the finished phases, with the notes made while building them, are
in git: `git show d771a26:docs/plan.md` (Phase 4b step 5's: `92837ce`;
Phase 4c's: `cd44203`; Phase 4d's: `280a3ef`).

## Decided against (until missed)

- **Overrides inside a mirrored group.** A playlist is the unit and a group
  always mirrors; a monitor that should do its own thing gets its own
  playlist.
- **A hidden playlist for monitors in no playlist.** `wallpaper set`
  covers them.
- **"Show and hold" in one.** Pick and hold stay separate one-click
  actions, with no dialog.
- **Native lock / unlock and sleep / resume notifications.** The 5 s tick
  notices both (the session flag, the tick gap); they would add platform
  code for no visible gain.
- **Stylesheets.** The colours come from the palette, and the controls are
  painted in code (Phase 5, "Controls"; [design/](design/README.md)).
- **Golden-image tests** (see Phase 7).
- **QML for the windows.** Qt Quick would give bindings, animations and an
  asynchronous image grid, but it means rewriting ~6,000 lines and their
  offscreen tests, mypy can't check QML, and the Qt for Python rules
  become other rules. Only if the windows keep growing a lot.

## Simplification

From a read of the whole codebase (2026-11-16, after Phase 5 step 4). The
core (`PlaylistService`, `order`, `store`, `marks`) is in good shape; what
grew is the plumbing around it: two ways into the service thread, line
framing written four times, the settings checks written twice, the windows
keying playlists by name, and a second socket only to open the window. Done
now, before Phase 5's step 5, while the protocol and stored state still
change in place. Each step is one session, updates the docs it touches
(architecture.md where it reverses a decision), and ends with a few things
to try by hand.

### Words

The names say what things are. "App" meant the product, the background
process and the `app/` package; "UI" both the window process and the
daemon's main thread; "service" sounds like an OS service.

| Was | Now | What it is |
|---|---|---|
| the app, the app process | **daemon** | `wallpaper tray` / `wallpaper run`: runs all session, holds the instance lock, serves the socket, rotates; with `tray` it also shows the icon |
| UI thread | **main thread** | Qt's main thread, in either process; the daemon's holds the tray |
| the service | **`PlaylistService`** | the core object; "the service is down" becomes "`PlaylistService` is stopped": the daemon keeps answering |
| service thread | *(kept)* | the daemon's thread that runs `PlaylistService` |
| `ServiceHost` | **`ServiceThread`** | the main thread's handle on the service thread |
| UI process | **window process** | `wallpaper ui`: the window, on demand |

Kept: what users read ("wallpaper_py isn't running"), Qt's `app` (the
`QApplication`), and the packages (`app/` is the Qt layer, `app/ui/` the
window process).

### Steps

1. **Words** (done). The renames above in code, tests and docs: `run_app` →
   `run_daemon`, `AppOptions` → `DaemonOptions`, `start_app` /
   `app_command` → `start_daemon` / `daemon_command`, `send_to_app` →
   `send_to_daemon`, test_app.py → test_daemon.py, and every other name
   that says "app" for the daemon. architecture.md opens with the table
   above. Nothing else changes. First and alone: it touches most files.
2. **Easy fixes**, two sessions side by side:
   - **a. Protocol** (done).
     - The windows key playlists by id, not by name: Home's rows and
       status details (`HomePage.rows`, `HomePage._details`), the
       Library's drop-down, `LibraryPage.directory` and the playlist in
       ui-state.json (a stored name falls back to the default once),
       `settings_draft.current_images` (its case-insensitive name matching
       goes), `arrangement.playlists_by_monitor`. `Change` gets the
       playlist's id, which `LibraryPage._changed` compares. A rename then
       breaks none of them.
     - One `match request:` in `commands.execute`, a case per request
       model, instead of the `isinstance` chain, the four helpers and
       `_TARGET_HANDLERS` with its assert. Each request model says whether
       it changes anything (a class variable), replacing
       `ServiceWorker.execute`'s hand-kept list of read-only commands. The
       command `Literal`s and unions in protocol.py keep only what that
       still needs. `HoldResult` and `ResumeResult` become one, as
       `MarkResult` already covers three commands.
     - protocol.md and the schema follow.
   - **b. Plumbing and the windows' repeats** (done).
     - The settings checks once: `check_settings`, `check_folders` and
       `check_list_files` find every problem with its place (playlist id
       or none, field, message) and raise the first where they check
       today. `settings_draft.problems` keeps only reading the texts
       (durations, paths) and "this is a folder, not a file";
       `SettingsPage._check`'s second check goes.
     - One `LineBuffer` (`feed(bytes)` → complete lines; raises past the
       size limit) for `ipc._read_line`, `LineRequest._read`,
       `SocketClient._read` and `LineServer._read`.
     - One helper for "one request at a time, ask again if more came
       meanwhile" (Home's `_status_running` / `_status_again`, the
       Library's `_asked_for` / `_ask_again`).
     - `client.ReplyTo` takes extra arguments; `home._ReplyTo` goes.
     - A `theme.Banner` for the banners of Home, Library and Settings.
     - One Qt-free texts module for what the tray, Home and the CLI say
       about a playlist or a monitor (`tray._monitor_label` /
       `home._member`; `tray._playlist_label`, `home.chip_text`,
       `cli._traits`).
     - The two `format_duration`s say different things: core.settings's
       is exact (it writes a settings field back), home.py's rounds the
       time left. One name each; the rounded one goes to the texts
       module.
     - The service thread logs each command or tick that takes over
       100 ms, with its time: by step 4, the daemon's log shows what a
       click could wait for.
3. **Every client the same** (done), two sessions side by side:
   - **a. The tray on `Client`** (done). The tray uses the `Client` the pages
     use, implemented in-process. Not over the socket: both ends of each
     connection would sit in the daemon, a socket pair per connection to
     pass messages between two threads.
     - The service thread sends each event once, as the protocol's
       `Event`, to both kinds of listener: `ServiceWorker._send(event)`
       emits one `sent` signal (not `event`: that would hide
       `QObject.event`) and broadcasts to the subscribers. It replaces the
       `changed` and `overview_changed` signals.
     - `LocalClient(Client)` on the main thread: `run` is today's
       `ServiceThread.submit` with its callbacks (queued to the service
       thread, the reply back through `replied`); events go through
       `Client._dispatch(event)`, moved out of `SocketClient._read` and
       shared by both. The models travel as they are (frozen, so safe to
       share between threads): no JSON, envelope, version check or
       handshake; connecting before the thread starts is the
       subscription.
     - `ServiceThread` keeps only the thread: `start`, `request_tick`,
       `shutdown`, `failed`, `ticked`, and hands out its `client`.
     - `Client`, `ReplyTo` and `SocketClient` move from app/ui/client.py
       to app/; the tray takes a `Client`, and test_tray uses the
       `FakeClient` the page tests use.
   - **b. The CLI as a plain client** (done). Playlist commands need the daemon:
     without one, the CLI says so (`NOT_RUNNING`: "`wallpaper tray`
     starts it") and exits non-zero. It still never starts the daemon,
     and no longer runs the command itself: `send_to_daemon`'s
     lock-and-retry loop, `Context.run_here`, `Answer.here` and its texts
     go, and so does the daemon's wait for a CLI holding its lock
     (`LOCK_WAIT_S`, `lock_wait_s`): the instance lock has one kind of
     holder. The tests inject a client that runs the command against the
     fakes (what `run_here` did, now only there). Monitor commands
     (`list`, `set`) still work alone. architecture.md's "The CLI never
     starts the app" paragraph changes: it no longer runs commands itself.
4. **The daemon opens the window** (done). The window process stops
   listening on a socket of its own; the daemon, which it subscribes to
   anyway, knows whether a window exists. Built as below, except:
   `wallpaper ui` doesn't ask `show_window` first, it subscribes as the
   window at once (the refusal already shows its page in the window, so
   the first request added nothing); and a window the daemon hasn't
   answered within 2 s shows anyway, "Connecting…", so a stuck daemon
   leaves no invisible process. The slow-work log, read first: 100 to
   400 ms, at most ~600 ms in a burst of *Next*.
   - Three additions, handled by `CommandServer` itself (like
     `subscribe`), not by `commands.execute`, so they work while
     `PlaylistService` is stopped (the window must open to show why):

     | Addition | Meaning |
     |---|---|
     | `subscribe {window: page}` | "I am the window." Only the first such subscriber is; a later one has its page forwarded to the first, is refused, and exits. |
     | `show_window {page}` → `{shown}` | A window gets `show_page`: `shown: true`. None: `shown: false`. |
     | event `show_page {page}` | Only to the window: it raises itself on that page. |

     `CommandServer` remembers which connection is the window; when it
     closes, there is none. `PageName` moves to protocol.py.
   - Tray click: `allow_foreground()` (the daemon has the click), then
     `show_window` through its `LocalClient`; `ServiceWorker` hands it to
     `CommandServer`. On `shown: false` the tray starts `wallpaper ui
     <page>` on the main thread: the service thread never starts
     processes.
   - `wallpaper ui <page>`: `show_window` on the daemon's socket; shown:
     exit. Else it subscribes as the window. No daemon: start it as now,
     and show the window at once with "Connecting…".
   - Two starting at once: the service thread takes their subscriptions
     one at a time, and the first wins. This replaces `ui.lock`. If both
     started the daemon, the loser shows "Connecting…" briefly and
     closes; with a daemon running, a window waits for its acceptance
     (milliseconds) before showing.
   - The window closes: it ends its subscription at once (no window from
     then on); commands in flight finish on their own connections; it
     quits once idle.
   - Goes: most of launcher.py (`UiServer`, `ShowRequest` / `ShowReply`,
     `show_in_running_ui`, the "ui" socket name, `WindowOpener`'s socket
     path), `ui.lock` and `paths.ui_lock`, `_wait_for_running_ui`, and
     the `LineServer` / `CommandServer` split. About 200 fewer lines; one
     socket and one lock.
   - The cost: a click waits for whatever runs on the service thread (a
     render ~200 ms, a first scan of a 20k-image folder, `stats
     --rescan`); today the tray asks the window process directly. Look at
     the slow-work log (step 2b) first: if a click could wait more than
     about half a second in normal use, decide before building.

Then Phase 5 goes on with step 5.

## Phase 5: Library page

The sidebar's Library page (a placeholder now): what a playlist picks
up, first in numbers, then as images. Features: the "Library window"
section of [todo.md](../todo.md), plus **"Show on <playlist>"** (`pick`)
in the enlarged view. The library stats of todo.md's "Settings / about"
item go to the Overview tab, not to About: About keeps the version and
the data locations (facts about the app, not the library), and loses its
"come in a later version" line.

### The page

The title, a playlist drop-down and the count of the open tab
(`Library  konachan ▾  · 20,431 images · 1,204 seen`; "312 of 20,431"
while filtered), then the tabs **All images**, **Favourites**, **To
remove**, **History**, **Upcoming**, **Stats**; under a grid tab, its
toolbar (filter, sort, toggles) and the grid, as in
[design/library.png](design/library.png). Stats has no toolbar.

The images come first: a library is its images (the phone app's opens on
All Images), and the numbers go last, as such tabs usually do; the seen
count in the header shows the cycle on every tab. Until the grid tabs
exist, the Stats tab is called Overview and comes first (the others only
say they come later).

- The drop-down picks the playlist for Overview, All images, History and
  Upcoming. Favourites and To remove are one list each for the whole
  app: their tabs show the whole list, with an *Only <playlist>* toggle
  (off by default) for the entries in the playlist's folder.
- Remembered: the playlist, the tab, each tab's sort, the toggles. In
  `ui-state.json` in the data directory, written only by the window process
  (a pydantic model; missing or unreadable: the defaults, logged, never
  an error shown). It is view state, not settings: no revision, no
  socket, nothing the app reads. Not `QSettings`: that is the registry on
  Windows, outside the data locations About lists.

### Overview tab (Stats, once the grid tabs exist)

Named as in the phone app's Information dialog (WallpaperSwitcher), so
both read the same:

```
This cycle                                                 6 %  ▓░░░░░░
  Images      D:\pictures\KONACHAN, scanned 2 min ago    20,431  Rescan
  Seen        shown in this shuffle cycle                 1,204
  Unseen      still to come before the next reshuffle    19,179
  Skipped     listed to remove, or unreadable                48
                                                       Reset cycle…
Lists (shared by all playlists)
  Favourites  favourites.txt      312 listed · 298 available · 120 here
  To remove   to-remove.txt        60 listed ·  48 available ·  48 here
```

- Seen + Unseen + Skipped = Images, always.
- **Images**: the files the playlist's scan found (the app's cached scan,
  up to 5 min old; *Rescan* scans again). **Skipped**: of those, the ones
  listed to remove or that failed to load. **Seen**: of the rest, the
  ones drawn in this cycle (picked ones count). **Unseen**: the rest;
  new files count as unseen before they are shuffled into the deck.
- **Listed**: entries in the file. **Available**: the file exists.
  **Here**: in the playlist's folder (as scanned).
- A sorted playlist has no cycle: the heading is "This walk", Seen /
  Unseen are the images before / after the current one in name order
  (the current one counts as seen; "at 1,205 of 20,383"), and there is
  no *Reset cycle*.
- *Reset cycle…* asks first ("Start konachan's shuffle cycle over? All
  20,383 images become unseen."); the image on screen stays, and isn't
  first in the new cycle.
- Asked again when the tab is shown, and on `overview_changed`,
  `changed` and `marks_changed` while it is.

### Commands and events

- `stats` (`target` optional: one playlist, else all; `rescan`): per
  playlist `name`, `directory`, `order`, `scan_age` (seconds), `images`,
  `seen`, `unseen`, `skipped`, `favourites_here`, `to_remove_here`; and
  `favourites` / `to_remove`: `file`, `listed`, `available`. Counted on
  the service thread from the scan, the deck and the lists: one `stat`
  per listed entry for *available* (the lists, never the folders).
- `reset_cycle` (`target`): a new shuffle cycle from the current scan;
  `failed` for a sorted playlist.
- `fav`, `unfav`, `remove` take an optional `image` (any path; without
  it, the playlist's current image as now; with it, no `target` is
  needed), and a new `unremove` takes an image off the list. `remove`
  with an `image` only lists it: a playlist showing it moves on at its
  next tick, as for a hand-edited list.
- `upcoming` (`target`, `limit` up to 1000): the next images in order,
  like `status`'s three (history ahead first, after *Previous*; then the
  deck or the sorted walk). Asked from the app, not read from SQLite: the
  order logic stays in one place (`PlaylistService._upcoming`).
- Event `marks_changed`: favourites or to-remove changed, by a command or
  by hand (the service compares each file's mtime / size on every tick;
  `MarkFile` already reloads on that signature).
- No `history_changed`: history only changes with a `changed` (a new
  image, *Previous*, pick) or a settings save (`overview_changed`).
- CLI: `wallpaper stats [target] [--rescan]`, `wallpaper reset-cycle
  target` (asks first, unless `--yes`), `wallpaper upcoming [target]
  [-n N]`, and `--image PATH` on `fav` / `unfav` / `remove` / `unremove`.
- docs/protocol.md gets the new commands and the event.

### What the window process reads itself

Writes are always commands: the service stays the only writer. For the
grids, the window process reads these itself, off the main thread:

- The playlist's folder: `core.library.scan`, plus each file's "added"
  time for sorting: the creation time on Windows (of the link itself for
  symlinks, i.e. when it was added to the library; not mtime, which
  copies keep), mtime on Linux (no portable creation time in Python). It
  may differ from the app's cached scan for up to 5 min; the header
  counts the grid's own.
- favourites.txt and to-remove.txt, through `MarkFile`, again on
  `marks_changed`. Their "added" order is the line order (entries are
  appended).
- History, from a read-only SQLite connection (WAL allows it), again on
  `changed` for the playlist.
- Not the settings (the `settings` command) and not the deck
  (`upcoming`).

### Thumbnails

A second pipeline beside `ThumbnailLoader` (which stays for Home's few
images), built from the Phase 0 spike (`spikes/grid_prototype.py`):

- `QListView` in icon mode over a virtual model, uniform item sizes:
  only visible tiles are painted or asked for. Tiles have a fixed size
  (16:9, the image filling it, cropped to the middle as Fill does: every
  tile alike, as in the phone app); the number of columns
  follows the width.
- Decoded in worker threads with Pillow (draft mode for JPEGs), newest
  request first (LIFO); a request for a tile scrolled out of view is
  dropped before decoding. A tile fades in from the panel tone when its
  thumbnail arrives (as in the phone app).
- In memory: an LRU with a byte budget, built in and generous (~1.5 GB,
  no setting). The thumbnails live only in the window process: closing the
  window ends it and gives everything back, so no "Free memory" button;
  the always-running app never holds or makes thumbnails.
- On disk: `paths.thumbnail_dir()`, one JPEG per image at tile size x
  the sharpest screen's DPR (`Sizes.sharpest`), named by a hash of the
  path, mtime, size and tile size (an edited image gets a new one).
  Reopening the window is fast.
- Pre-generation: while the window is open, one low-priority thread
  fills the disk cache, the open playlist's folder first, then the other
  playlists and the lists; it pauses while the grid asks for tiles. Having
  seen every current image, it deletes the cache files none of them names
  (old tile sizes, deleted or edited images).
- A listed entry whose file is gone is a "missing" tile with its name,
  never an error.

### Grid tabs

- With them, Overview becomes **Stats** and moves last, and All images
  is the first tab (where a new user lands). The remembered tab is
  stored by name: `overview` becomes `stats`, and a file that still says
  `overview` falls back to the defaults once (pre-release, no
  migration).
- **All images**: the folder; *Hide removed* on by default.
- **Favourites**, **To remove**: the list; *Only <playlist>*.
- **History**: the playlist's entries, newest first, each time it was
  shown (repeats kept), captioned with when ("2 h ago").
- **Upcoming**: the next 500 in order (`upcoming`); no sort.
- Sort, per tab: name A–Z / Z–A, newest / oldest added (History: newest
  / oldest shown).
- Filter by name: a case-insensitive part of the file name; the grid
  narrows as you type (debounced) and the count follows. A click (or
  Enter on the selected tile) opens the image enlarged.
- Badges as in the mockup: favourite star, to-remove mark, *On screen*
  (the current image of any playlist).
- A slim, draggable fast-scroll bar; back to the top when the sort,
  filter or playlist changes.
- Smooth with 20k images: [desktop-checks.md](desktop-checks.md) gets
  one step for it (scrolling and memory, which no test sees).

### Enlarged view

Over the page (not a window of its own), on the panel tone:

- Fitted at first; the wheel zooms at the pointer (up to ~5x), a
  double-click toggles zoom / fit, dragging pans. Decoded at full size
  off the main thread; the thumbnail stands in until then.
- An info overlay (a click hides / shows it): the full path (selectable,
  with *Copy path*), dimensions, file size.
- Toggles for favourite and to remove (by path, so un-favourite and
  un-remove too), and **Show on <playlist>** (`pick`; a drop-down with
  several playlists).
- ← / → go to the image before / after in the tab's order; Esc closes.

### Controls

Flat controls in the NVIDIA app's manner, in the system's accent. Today
the native style draws a few controls, and it differs by OS: Windows 11
style on Windows, but **Fusion on Linux**, not Breeze (PySide6's own Qt
can't load the system's Breeze; design/README.md and theme.py say Breeze,
wrongly). So these are painted in code too, as the tabs, text buttons,
icon buttons and panels already are: one look on Windows and KDE, colours
from the palette (light / dark and the accent still follow the system),
no stylesheet.

- **Drop-down** (`FieldCombo`): a filled box without a border, a solid ▾
  at the right, lighter on hover; its popup list in the same tones, the
  current entry marked.
- **Segmented buttons** for a few choices, like NVIDIA's Orientation row:
  Mode (Fill / Fit / Stretch, each with a small icon), Order (Shuffle /
  Sorted). Long lists (Background, playlists, monitors, sort) stay
  drop-downs.
- **Switches** for on / off, not check boxes: the two in Settings, and
  *Hide removed* / *Only <playlist>* later.
- **Buttons**: the main action (Save) filled with the accent, the others
  (Revert) a neutral fill. Text buttons stay for secondary actions.
- **Slider** (Blur, Darken): a thin track, accent up to a round handle.
- Text fields stay as they are (filled, no border).
- Each handles hover, the keyboard focus ring, disabled, its accessible
  name and high DPI, as `PaintedButton` does; tested offscreen
  (`tests/test_theme.py`).
- **The accent on Linux**: Qt reads KDE's colours (kdeglobals) but puts
  the accent only in `Highlight`; `Accent` stays Fusion's built-in
  `#308cc6`, which is what the app uses now, so a KDE accent other than
  Breeze's blue is ignored (tried with a copy of kdeglobals: Highlight
  followed a purple accent, Accent didn't). Take `Highlight` on Linux,
  `Accent` on Windows. To try once: change the accent in KDE with the
  window open (does it follow at once, or on the next start?).
- **The mockup**: [design/controls.dc.html](design/controls.dc.html)
  (approved): the exact sizes, tones and states; build from it, not from
  this list. Decided with it: corners stay 4 px; the open list marks the
  current entry like the sidebar marks the current page (an accent bar
  on a faint accent fill); the keyboard focus is a 2 px solid accent
  ring, also on the tabs, text buttons and icon buttons (today 1 px at
  60 %).
- design/README.md's "Follow the system" becomes: the system's colours,
  light / dark mode and accent, with our own controls; the page mockups
  stay the reference for layout.

### Steps, one session each

1. **Overview** (done): `stats`, `reset_cycle` and their CLI; the page
   (title, playlist drop-down, tab bar, `ui-state.json`) with the Overview
   tab; About's line goes. The grid tabs say they come later. *Reset
   cycle…* sits beside Seen, as *Rescan* beside Images; a sorted walk's
   place is the newest image it showed (where it goes on), which is the
   current one unless *Previous* went back.
2. **Controls** (done), from design/controls.dc.html: drop-down,
   segmented buttons, switches, buttons and slider, used in Settings and
   the Library page; the 2 px focus ring everywhere; the accent from
   `Highlight` on Linux; the docs say Fusion. Decided while building:
   the drop-down's open list is a popup of our own (restyling Qt's would
   mean overriding Qt's non-event methods, AGENTS.md rule 3), placed
   under the box (above if there is no room), with all entries (the
   app's lists are short); a click drops the focus ring until the next
   Tab; the ring lies on the control's edge with the fill inside it (Qt
   can't paint outside a widget), and a switch keeps 4 px around its
   pill for it; *Changes* (Every / Manually) stays a drop-down beside the
   interval field. Mode's icons are our own, drawn like Lucide's.
3. **Data for the grids** (done): marks by `image`, `unremove`,
   `marks_changed`, `upcoming` (and their CLI); the UI-side scan with
   "added" times (`library.scan_files`); the read-only history reader
   (`store.read_history`). Decided while building: the list commands are
   a request model of their own (`MarkRequest`), an `image` or a `target`,
   never both; `unremove` needs an `image` (the current image is never
   listed for long); `remove` replies `changed` too; the tray and Home
   build their buttons' requests with `protocol.target_request`.
   `marks_changed` carries no fields and goes only to subscribers; the
   Overview tab asks for its numbers again on it. `scan_files` reads the
   times from the directory listing (free on Windows, an `lstat` each on
   Linux) and shares its walk with `scan`. The history reader takes a
   playlist's id: step 5 gets it from the `settings` command, or adds
   `id` to the overview's playlists.
4. **Thumbnails** (done): the grid pipeline, the disk cache,
   pre-generation and pruning; tested offscreen with generated images
   (`tests/generate_test_images.py`, `generate_library`). Decided while
   building: All images already shows its folder in the grid (name order,
   no toolbar, count or badges yet), so the pipeline can be tried by hand.
   Tiles are 192 x 108 with 8 px between them; a thumbnail is the image
   filling the tile (cropped to the middle; chosen by the user over
   fitting, as the phone app does: every tile alike) at the sharpest
   screen's scale, a JPEG at quality 88 (`core.thumbnail_cache`,
   `imaging.load_filled`); the style is part of the file's name.
   Four tile threads; the grid tells the loader the rows on screen and one
   more above and below, found by bisection over the item rectangles. The
   overview now carries each playlist's `id` and `directory` and the two
   list files, which the pre-generation follows (and step 5's History
   can use). A pass prunes only after reading every folder and list (a
   folder not mounted keeps its thumbnails), never from an overview with
   an error, and keeps files written since it started. Threads are
   `QRunnable`s on each object's own `QThreadPool` (the pre-generation's:
   one thread, low priority), as `ThumbnailLoader`'s, all through one
   helper (`Jobs`: a pool, results on the main thread); the Qt-rules test
   now allows an item model's `rowCount` / `data` and a delegate's
   `paint` / `sizeHint` (AGENTS.md rule 3). The window stops the threads
   when it closes, and on `aboutToQuit` when the app quits with it open.
5. **Grid tabs**: the five tabs, toolbars, badges, counts, fast-scroll;
   Overview becomes Stats, last.
6. **Enlarged view**.

Each step updates the docs it touches and ends with a few things to try
by hand.

## Phase 6: KDE backend

Done (Arch, Plasma 6.8, Wayland; how it works:
[architecture.md](architecture.md), "Monitor identity"):
`backends/kde/`, through `kscreen-doctor -j` and `busctl` (plasmashell's
`evaluateScript`, the ScreenSaver interface); ids are connectors, names
from EDID; `Rectangle.pixels` for Wayland's logical desktop; the lock
pauses rotation; the README lists the system packages. Tried for real:
`list`, `set` in each mode, the app rotating a mirrored pair (Fill + Fit)
and a strip (Fit, blur), next / prev, with `grabContainmentImage` showing
each desktop.

Decided:

- **Command-line tools, not a D-Bus library.** No Qt below `app/`
  (QtDBus), no new dependency (jeepney); both ship with Plasma and
  systemd, and a call takes a few ms (kscreen-doctor ~15 ms).
- **Every activity** gets the image: switching activity keeps the
  playlist.
- **No idle pause on KDE.** Wayland gives idle time only to Wayland
  clients (ext-idle-notify); the ScreenSaver interface refuses
  GetSessionIdleTime and Plasma doesn't set logind's IdleHint. Lock only.

Left:

- CI and the checks on Kubuntu 26.04, and the whole of
  [desktop-checks.md](desktop-checks.md) by hand on Arch and Kubuntu
  (monitor switched off, resolution change, `plasmashell --replace`).
- A readable startup error when Qt can't load its platform plugin or a
  library, naming the package to install (and `QT_DEBUG_PLUGINS=1` for
  anything else), instead of Qt's cryptic message.
- Maybe: Settings says the idle pause does nothing on KDE.

## Phase 7: polish

Nice-to-haves from [todo.md](../todo.md): sound on wallpaper change,
image overlay. Packaging/installer. (Thumbnail fade-in moved to Phase 5.)

Open (todo.md, normal priority): whether Save asks first when it deletes
a playlist's history (today the footer names it: "delete X and its
history").

Sound on wallpaper change: a per-playlist setting (off by default), a row
in the playlist's settings. Played by the app (it runs anyway; the UI
process may not), on any change the playlist makes: scheduled, next / prev
/ pick. One short bundled sound, through Qt Multimedia (`QSoundEffect`); a
custom sound file per playlist only if missed.

Sizes on disk of the render and thumbnail caches in About: never worked
out on the main thread (walking a 20k-image thumbnail cache would stall
it). (The library stats moved to Phase 5's Overview tab.)

Virtual backend and page renders (nice to have, for testing; not needed
before anything else):

- A virtual backend that is part of the app, not only of the tests:
  `--backend virtual` (or an environment variable) takes made-up
  monitors from a small file (ids, names, sizes, positions, attached
  or not) and "sets" wallpapers by recording them, never touching the
  desktop. It runs with its own data dir and socket names, so it never
  meets a real running app. Much of it exists in `tests/fakes.py`.
- End-to-end tests on it: the real app process, the real window process
  and the CLI over real sockets (today each process is tested alone
  against fakes; the wiring between them only by hand). Monitor
  scenarios become data: the OLED switched off and back, 4K + a
  vertical monitor, five monitors.
- A demo mode from the same pieces: screenshots, the README, working on
  Linux without KDE.
- `scripts/render_pages.py`: every page from fixed demo data, offscreen
  (no display needed, also on Ubuntu CI), dark and light, at 100 % and
  150 %, to PNGs. CI uploads them as an artifact on each push, to look
  at, not pass / fail.
- Not now: golden-image tests (comparing renders with approved images).
  They fail on every intended change of the look, Qt upgrade and font
  smoothing difference between machines; maybe once the look settles,
  after Phase 5.
