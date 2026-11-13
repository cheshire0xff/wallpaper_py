# Plan

One cross-platform Python app (Windows first, then KDE Plasma) built on
PySide6: starts at login straight to the tray, single instance, with a CLI
that talks to it. A Qt-free `core` holds all the logic; platform backends stay
thin. See [architecture.md](architecture.md) for the layers.

| Phase | What | Status |
|---|---|---|
| 0 | Spikes: monitor identity on Windows, 20k-image grid, KDE probe | done (KDE probe pending, needs Arch) |
| 1 | Core model, safe setter, Windows backend, CLI `list`/`set`, tooling, CI | done |
| 2 | Playlists, mirrored groups, scheduler, history, favourites, to-remove | done (awaiting a real-desktop trial) |
| 3 | App shell: tray, single instance, IPC, autostart | done (awaiting a real-desktop trial) |
| 4 | UI process, layout and settings windows | done (pick / hold / manual, the layout and settings windows awaiting review and desktop checks) |
| 5 | Library window | |
| 6 | KDE backend | |
| 7 | Polish | |

## Phase 2: playlist service (all in `core`, no Qt)

Goal: the wallpapers actually rotate, safely, and remember everything across
reboots. Pure logic with an injected clock and backend, so it is tested with
fakes like Phase 1.

- **Playlist**: a directory (recursive scan), order *sorted* or *shuffled*,
  interval. Shuffle is a persisted "deck": every image once before any
  repeats, so rotation doubles as a library review. The rest of the deck is
  the *upcoming* queue.
- **Targets**: a playlist drives one monitor or a **mirrored group** (same
  image on every member). Your setup: AW3225QF + PLX2783H mirrored on
  `KONACHAN`, ZeroMOD alone on `Fantasy`.
- **Scheduler**: computes when each target is due. On
  `MonitorNotAttachedError` it **postpones** (never skips, never retargets).
  A group advances while at least one member is attached; a returning
  monitor gets the group's current image.
- **Next / previous** per target, walking history.
- **State** in SQLite, per OS (no sharing between Windows and Arch), only
  app-internal state: history, shuffle deck, per-target state.
- **Favourites / to-remove** live *only* in their `.txt` files (one full
  path per line), not in SQLite. Decision:
  - every change re-reads the file, applies that one add/remove, and
    writes it atomically (temp file + `os.replace`);
  - the app reloads the file when it changes externally (the library
    script or a hand edit may clear it);
  - images whose files no longer exist are hidden in the UI but never
    removed from the file automatically; clearing is explicit (edit the
    file, or a later clear command / button);
  - locations are configurable in the TOML config, defaulting to the data
    dir;
  - a file that can't be opened (an editor or a virus scan holds it) keeps
    the last list read; one never read yet is *unknown*, not empty: while
    to-remove is unknown no image is chosen (ticks wait, next / prev /
    pick fail), and the overview's `favourite` is null.

  To-remove only lists and hides the image from rotation; deleting stays
  with your library script.
- Robust to the library changing: new files join, missing files are skipped.
- **Config**: a human-edited TOML file defining playlists and targets.
- **Interval** counts from the last change; a manual next/prev resets it.
- **Pause while locked or idle** (so no image is missed), behind a small
  backend hook: Windows session/idle APIs now, logind / KDE idle on Linux
  in Phase 6. Best effort; rotation still works if a platform can't report it.
- Usable before the tray exists: a foreground `wallpaper run` plus
  `next`/`prev`/`fav`/`remove` commands.

## Phase 3: app shell (`app/`, PySide6)

Tray icon with per-target menus (monitor names, not numbers), single instance
via `QLocalServer`, and the CLI sending commands to the running app over that
socket. Starts at login to the tray (Windows Run key / XDG autostart). Screen
signals are debounced: re-query until two backend snapshots match, because COM
lags Qt by 1-2 s.

Must fix: while the CLI and `wallpaper run` are separate processes sharing
the database, a command during an in-progress change can double-commit
(same image twice in history). CLI commands always go through the app's
socket and run on the one service, serialized with its ticks. If the app
isn't running, the CLI runs the command itself under the app's instance
lock and exits (it used to start the tray app, which then kept running
after a mere `status`; changed 2026-11-12).

Ticks and events: keep polling as the safety net, use events only where
they make a visible difference.

- A `QTimer` calls `service.tick()` every 5 s and passes that period as
  `tick_period` (gaps up to two periods count as active time, longer ones
  are sleep). The tick stays the source of truth: it re-reads the real state
  each time, so a missed event costs at most one tick.
- Qt screen signals (`screenAdded`, `screenRemoved`, geometry changes)
  restart a ~2 s single-shot debounce timer that runs an extra tick, so a
  monitor coming back gets its image right away.
- Commands (tray, socket) run on the service thread as they arrive,
  between ticks (never during one).
- Not used: native lock / unlock and sleep / resume notifications. The 5 s
  tick already notices both within 5 s (lock via the WTS session flag, sleep
  via the tick gap), so they would add platform code for no visible gain.

Threads: the service runs on **its own thread**, never the UI thread. A
change renders for ~200 ms (more for big PNG sources); on the UI thread that
would freeze the library grid mid-scroll.

- One worker `QThread` owns the `PlaylistService`: its tick `QTimer`, the
  rendering, COM (initialized on that thread) and the SQLite connection
  (created there). The core stays synchronous and unchanged.
- The UI thread only draws. Tray actions and screen signals reach the
  service as queued signals; results (changes, status, errors) come back
  as signals. The command socket lives on the service thread itself. The
  UI never waits on the service.
- Still no locking: the service is used from exactly one thread.
- Windows that read state (library, history) open their own read-only
  SQLite connection; WAL allows readers alongside the writer.

Implemented (decisions made while building it):

- `wallpaper tray` and `wallpaper run` start the same app, with and without
  the tray icon; both hold the instance lock and serve the socket, so
  nothing rotates without taking commands. A second start is refused.
- Single instance: a `QLockFile` next to the state database (a second
  `QLocalServer` *can* listen on the same pipe name on Windows); the socket
  name is derived from the data directory, so it is per user.
- `commands.py` (Qt-free): commands as data (`Request` -> `Reply`, plus
  the wallpaper change a command made), run only by the app; the socket
  carries them as pydantic models. A request naming another config file
  than the app's is refused.
- The command socket lives on the service thread, created once at start
  (it answers across config reloads, with the error while the config is
  broken). Socket requests call `ServiceWorker.execute` directly; the tray
  goes through `ServiceHost`. Commands that change a wallpaper emit
  `changed` like ticks. The UI thread holds only UI.
- The CLI always goes through the app: if the socket doesn't answer, it
  starts the tray app (the autostart command) and retries for up to 10 s,
  else it points to the log file. Two CLIs racing both reach the one app
  the instance lock keeps. `list` and `set` don't need the app. It starts
  the tray rather than a headless `run`: the user can see and quit it, and
  it logs to a file. On Linux without a display it can't start; if that
  matters, fall back to `run` plus a `quit` command.
- Screen signals: a plain 2 s debounce then one extra tick (no snapshot
  comparison); the 5 s tick covers COM lagging longer.
- Tray menu: flat sections per playlist (monitor names with *off* / *not
  found*, current image opens on click, Next, Previous, Favourite toggle,
  Remove), then Paused, Reload config, Open config folder, Start at login,
  Quit. Left-click opens the same menu. Icon: grey while paused, red badge
  on a config error. New CLI command `unfav` for the toggle.
- Config errors keep the app running: shown in the tray, *Reload config*
  retries.
- Autostart: HKCU Run key / `~/.config/autostart`, running
  `pythonw -m wallpaper_py tray` from the current environment. Written only
  when *Start at login* is ticked.
- Logs: the tray app writes `wallpaper.log` in the per-user log directory.

Not done yet: parts of the trial on the real desktop (autostart entry,
monitor switch-off with the app running); see
[desktop-checks.md](desktop-checks.md).

## Phase 4: UI process and layout window

Separate UI process (decided: the GUI is used rarely, so it must cost
nothing while closed):

- The always-running app = service thread + tray + command socket, as
  now; it stays small.
- The window (since step 4 one main window with the pages Home, Library
  and Settings; at first three windows) runs in **its own process**,
  `wallpaper ui [home|library|settings]`, started on demand and exiting
  when the window closes, which gives all its memory back.
- Starting: the tray's entries (or the CLI) first ask a running UI
  process to show that page (its own small socket, same pattern as the CLI
  auto-start); if none answers, they start one. One UI process at most.
- The UI process is a client of the app's socket like any other: commands
  with replies, plus `subscribe` for events. If the app isn't running, the
  UI starts it (the autostart command); if the app quits, the UI
  closes too (its subscription ends).
- Windows depend only on a narrow client interface (send a command and
  get the reply; receive events), never on the service or `ServiceWorker`.

Order inside the phase: first the foundation (data-only replies,
`subscribe`, the `wallpaper ui` process with single instance and an empty
window, tray entries), then the layout window and manual mode / hold /
pick on top.

Layout window: monitors drawn to scale in their real arrangement, each showing a thumbnail
of its current wallpaper. Assign playlists and groups, and run next/prev/fav
from here. Per playlist, a "current wallpaper" card (todo.md, Library
window): thumbnail, name, size, dimensions, fav / remove toggles.

Look (decided, from a mockup the user approved; it holds for every window):

- **Follow the system**: Qt's native style on each OS (Windows 11 style,
  Breeze on KDE), the system's light / dark mode and accent, all read from
  the `QPalette`. No hard-coded colours, except:
- **A dark neutral backdrop behind images** (`#161616`, in light mode
  too): the monitor drawing, thumbnails, later the library grid and the
  enlarged view. Wallpapers look washed out on white.
- **Icons**: a small bundled set of Lucide SVGs (ISC licence, kept with
  them), tinted with the palette's text colour when loaded, so they work in
  light and dark. Not `QIcon.fromTheme`: Windows has no theme icons, and
  mixing sources looks inconsistent.
- **`app/ui/theme.py`**: spacing (8 px steps), corner radii (8 cards,
  4 small), the backdrop colour, the icon loader, and the shared widgets
  (an icon button; the image thumbnail on the backdrop). Not a framework.
- Layout window, top to bottom:
  - *Monitors*: the arrangement to scale on the backdrop, without
    outlines (an accent outline read as a selection, so mirrored groups
    aren't marked in the drawing; the card shows them). Under each: name
    (bold), resolution (`3840×2160`, no spaces) and playlist.
  - Hover: pointing at a playlist's card outlines its monitors in the
    accent, and pointing at a monitor gives its playlist's card an accent
    edge. The accent then only ever means "what you point at".
  - *Playlists*: a grid of cards. Each card: a 16:9 thumbnail
    (letterboxed on the backdrop); the playlist name, its monitors (a link
    icon for a mirrored group) and a *Held* / *Manual* chip; the image's
    file name; "dimensions · file size · order, interval · next in …"
    (or "… left once resumed" / "manual"); then icon buttons previous,
    next | favourite, remove, hold, each with a tooltip and an accessible
    name. A toggle that is on is filled with the accent.
- The title bar is the OS's own.

One window (decided after step 3, from an approved mockup; replaces the
three separate windows; screenshots and the mockup's source in
[design/](design/README.md), which win over the words here):

- **One main window with a sidebar** on the left: *Home*, *Library*,
  *Settings*, each an icon (Lucide `house`, `images`, `sliders-horizontal`)
  and a label. The selected page: an accent bar on its left edge, a faint
  accent fill, a bold label. App-wide status ("Rotation active" / paused)
  at the bottom of the sidebar.
- **Home** is the former layout window (*Monitors* and *Playlists*
  sections, unchanged). `wallpaper ui [home|library|settings]` and the
  tray's entries open the window on that page (one *Open wallpaper_py*
  for Home, plus *Library* and *Settings…*); a running window comes to
  the front on that page. Still the on-demand UI process; still one at
  most.
- **Dark tones** (dark mode is the one that matters; light mode only has
  to stay usable): the sidebar is the page's colour, set apart only by a
  thin lighter line (also under the title area); the page a shade darker
  than Qt's default dark window. Image panels (monitor drawing, grids)
  are a little *lighter* than the page in dark mode, not a dark well;
  light mode keeps a dark panel behind images.
- **Settings as rows, no boxes**: no cards or outlines. Each setting is a
  row: its name (and a one-line description, muted) on the left, its
  control on the right, all controls in one column. Fields are a filled
  background without a border. Sections have a heading and a thin divider
  between them. One playlist is open at a time; the others collapse to a
  summary row ("ZeroMOD · sorted · every 10m · fill") that opens on click.
  Secondary actions are accent-coloured text buttons (*Browse…*, *Delete
  playlist*, *+ Add playlist*). Save / Revert and the status line sit in a
  footer bar. Sub-tabs (Playlists, General, About) with an accent
  underline.
- **Interval**: a *Changes* row, *Every* + a duration (e.g. `25m`, "of
  active time"), or *Manually* (the duration hidden); no checkbox.
- **Library** (Phase 5) follows the same page pattern: tabs with an
  accent underline, a toolbar (filter field, sort, *Hide removed* switch),
  the grid on the lighter panel tone, a slim fast-scroll bar.

Manual mode, hold and pick (decided; kept deliberately simple):

- **A playlist is the unit; a mirrored group always mirrors.** No
  single-monitor overrides inside a group: a monitor that should do its own
  thing gets its own playlist.
- **Pick** ("Show on <playlist>"): the image is inserted right after the
  history cursor and shown, like `next` with a chosen image (the interval
  restarts). Images from any directory work: history entries are plain
  paths, the directory only supplies *new* images. `prev` goes back to what
  was shown before. A pick from the playlist's own directory counts as seen
  in the current shuffle cycle.
- **Hold**: a per-playlist flag in SQLite (not the TOML: the app doesn't
  rewrite the config, and resuming needs no remembered interval). While
  held, ticks don't advance; next / prev / pick still work. Toggle in the
  tray and here.
- **Manual mode** = `interval = "manual"` in the config: the playlist never
  changes by itself, and hold doesn't apply to it. (Intended for the
  ZeroMOD strip.)
- Pick and hold are separate one-click actions; no dialog, no combined
  "show and hold".
- Not doing (until missed): single-monitor overrides in a group, a hidden
  playlist for monitors in no playlist (CLI `set` covers them).

Structured command API (so other programs can build their own UI on the
socket, and our windows use the same models):

- Replies carry **data only**: a pydantic model per command, as a tagged
  union on `command`, or a structured error. No text on the wire (no
  `out` / `err`): the CLI formats its output from the data, so formatting
  lives in one place. Nothing outside uses the protocol yet, so this simply
  replaces the current text replies.
  - `status`: paused, and per playlist: name, order, interval, mode,
    directory, monitors (name, attached), current, favourite,
    next_in_seconds, upcoming.
  - `next` / `pick`: the change (playlist, image, reason, shown_on,
    missed); `prev`: the change or null (nothing earlier).
  - `fav` / `unfav`: image, changed (false if nothing changed).
  - `remove`: image, list_file, change (null if no monitor was attached).
  - `hold` / `resume`: playlist, held.
- Errors are structured too: `{"error": {"kind", "message"}}` with kinds
  like `not_attached`, `unknown_playlist`, `no_images`, `config_error`.
- The tray consumes the same models in-process; the UI process gets
  them over the socket.
- From then on the protocol only ever adds commands and fields (clients
  must ignore unknown fields); a breaking change bumps the version.
- Publish the JSON Schema of requests and results (pydantic generates
  it), plus a short protocol doc in docs/.
- `subscribe` (needed now, by the UI process): the connection stays open
  and the app streams one JSON line per event (`changed`,
  `overview_changed`, later `marks_changed` / `history_changed`). Events
  originate on the service thread, where the socket lives.
- Not needed: paging for history / library lists (the UI reads SQLite
  directly, see Phase 5). Thumbnails stay off the socket; clients read the
  image files by path.

Foundation, implemented (decisions made while building it):

- `protocol.py` (Qt-free) holds the wire models; `commands.py` returns them
  and `cli.py` formats its text from them (output unchanged). The overview
  moved there too, since it now goes over the socket. Protocol version 2.
- Results carry `playlist` (the resolved one: the target may be a monitor),
  `fav` / `unfav` / `remove` also `list_file`, and `status` also
  `warnings`, so the CLI can print what it printed before. Monitors carry
  their `id` (as in the config) besides `name` and `attached`.
- Error kinds: `protocol`, `wrong_config`, `config_error`,
  `unknown_target` (playlist or monitor), `not_attached`, `no_images`,
  `nothing_shown`, `failed`, `internal`, and the client-side
  `unreachable`. The core got exception subclasses for them.
- `subscribe`: the first event is the current overview, so a client needs
  no separate request; a subscriber more than 4 MB behind is dropped.
- The UI process listens on `<app socket>-ui` while holding `ui.lock`; it
  refuses a "show" for another config. Tray and UI process ask without
  blocking (`ipc.LineRequest`); a starting UI process asks blocking, before
  its loop runs. On Windows the asker calls `AllowSetForegroundWindow` so
  the window can come to the front.
- The UI process logs to `ui.log` next to `wallpaper.log`. Run from a
  terminal, `wallpaper ui` stays in the foreground like `run` / `tray`.
- Found while testing: Qt signals must connect to bound methods, not
  lambdas holding `self` (a double delete on teardown; it was latent in
  Phase 3's socket server too), and pydantic must be fully loaded before
  PySide6 (`app/__init__.py`).

Order for the rest of the phase, one session each:

1. **Pick, hold and manual mode**: core, protocol (`pick`, `hold`,
   `resume`), CLI, tray *Hold* toggle. No new windows. **Done** (awaiting
   review and the desktop checks, section 9).
2. **Layout window**: shows the assignment (monitors to scale, current
   wallpaper per playlist, toggles, next / prev); replaces the
   placeholder. **Done** (awaiting review and the desktop checks,
   section 10).
3. **Settings panel** (decided: wanted): assign monitors to playlists and
   groups, and edit a playlist's directory, order, interval and mode. The
   app edits config.toml with a comment-preserving TOML library (tomlkit),
   so hand edits and comments survive, then reloads it. The file stays the
   source of truth; hand editing keeps working. **Done** (awaiting review
   and the desktop checks, section 11); the About page too, without the
   stats.
   Nice to have, in the same window if cheap, else Phase 7: an *About /
   info* page like the phone app's (see todo.md, "Settings / about"): data
   locations with *Open folder* / *Copy path*, version with git hash, and
   library stats per playlist. The stats come from a new `stats` command
   (the service has the scan, the deck and the marks); not read from the
   database directly, so its layout stays private to the service.
4. **One window with a sidebar** (Home, Library, Settings), as in the
   mockups (docs/design/): replaces the three windows. **Done** (awaiting
   review and the desktop checks, section 8).

Pick, hold and manual mode, implemented (decisions made while building it):

- **Manual** playlists don't "start held" (as first planned): they simply
  never rotate, and hold doesn't apply to them (`hold` / `resume` on one is
  an error saying why; the tray has no *Hold* item for it). In the config
  `interval = "manual"`; in the core `PlaylistConfig.interval` is None. A
  manual playlist shows nothing until the first next / pick.
- Held or manual, a tick never chooses a new image (not even a first one)
  and the active-time count stands still, so `resume` continues where it
  stopped. Ticks still restore the current image on a returning monitor,
  and still replace a current image that can't be shown any more (listed
  for removal, deleted): `remove` on a held playlist with no monitor
  attached still moves on once one is.
- **Pick**: the request is its own model, `PickRequest` (`command`,
  `target`, `image`, an absolute path; the CLI makes it absolute without
  resolving symlinks), so `image` is required for pick and refused
  elsewhere. Commands run on `CommandRequest = Request | PickRequest`.
  The image must exist and open in Pillow (`imaging.check_image`, header
  only); otherwise the new error kind `bad_image`. An image on the
  to-remove list is refused (`failed`): the next tick would replace it
  anyway. Pick from the playlist's directory (matched by path key, so
  another spelling works) uses the scan's spelling; in a shuffle it moves
  the card among the drawn ones (`ShuffleDeck.mark_seen`), keeping the
  others' order. In sorted order the walk goes on after the newest history
  entry *from the directory*, so a pick from elsewhere is a detour and one
  from the directory moves the walk there.
- **History order**: a `position` column (REAL), new entries at max + 1, a
  pick at the midpoint after the cursor; positions are renumbered when two
  get closer than 1e-6. **Hold**: a `held` column on `playlist`.
- **No migrations before the first release** (decided during this step):
  the schema changed in place to version 2; a database of another version
  is refused with a message to delete it (only history and shuffle state
  are lost), never deleted automatically. The same goes for the protocol:
  it stayed version 2 although `interval` / `next_in_seconds` in `status`
  can now be null (manual).
- Results: `PickResult` (`change`, reason `picked`), `HoldResult` and
  `ResumeResult` (`held`, `changed`). `PlaylistView` gained `held` and
  `manual`; the tray header shows *(held)* / *(manual)*, also in the
  tooltip.

Layout window, implemented (decisions made while building it):

- **Overview**: gained `monitors` (every listed monitor: `id`, `name`,
  `geometry` x / y / width / height in desktop pixels, null when detached),
  and each playlist member gained `id`, so the window links them without
  names or positions. Still small: no schedule in it, since the time left
  changes every tick and the overview goes to every subscriber. The window
  asks `status` once per new overview (one request at a time) and counts
  the time left down locally while the app does (not paused, held, manual
  or without an attached monitor). `commands.overview` queries the backend
  once for both (`PlaylistService.status(monitors=...)`).
- **Arrangement**: one scale for the whole desktop, aspect kept, centred,
  with room for two caption lines under the bottom row. A detached monitor
  has no position, so it is drawn to the right of the others, at its last
  size seen this session (else 1920×1080), dimmed, "off". No outlines,
  except on hover (decided after review: a permanent accent outline on
  mirrored members read as a selection). A caption that would overlap a
  monitor below goes inside it on a dark band. Unlisted members ("not
  found") aren't drawn; the card names them.
- **Cards**: thumbnail fit (letterboxed) at 16:9; the monitor drawing
  fills (crops) like the default fill mode. Hold uses Lucide `pin`,
  previous / next `skip-back` / `skip-forward`, remove `trash-2`. A toggle
  that is on gets an accent-filled background and the icon in the
  palette's highlighted-text colour; its tooltip and accessible name say
  what a click does ("Remove from favourites", "Resume"). The click sends
  `fav` / `unfav` or `hold` / `resume` from the overview's state. Remove
  has no confirmation, like the tray. Notes that aren't errors ("Nothing
  earlier in the history", removed with no monitor on) show in the card
  too. Favourite and remove are disabled until something is shown.
- **Thumbnails**: one decode per image at 480 x 270 logical px (x DPR)
  serves both the card and the monitor drawing; `QImageReader` with
  `setScaledSize` and auto-transform (EXIF orientation), 2 threads of the
  window's own pool, an LRU of 64 by path (it lives only as long as the
  window). Unreadable images show "can't read: …" in the details.
- No Qt sizing overrides (`sizeHint`, `heightForWidth`): they would shadow
  Qt methods (rule 3); widgets size themselves in `resizeEvent`.

Settings window, implemented (decisions made while building it):

- **Which config**: a client sends `config` only when the user gave
  `--config`; without it, it means whatever the app runs (the envelope's
  `config` is optional; an explicit different one is still refused). The
  overview carries `config`, the file the app runs, so the windows (and
  the UI socket, `UiServer.follow`) use the app's file. Starting an app
  when none runs is unchanged: `--config`, else the default.
- **The UI process edits the file**, like an editor: the app stays the
  only writer of *state* and only reads the file again. `reload` became a
  protocol command (also `wallpaper reload`; the tray's *Reload config*
  sends it too), run by `ServiceWorker.execute` itself (it replaces the
  service, so it isn't a `commands.execute` command). Its reply: `warnings`,
  or the config error. The overview is published before the reply.
- **Loading only reads; `prune` drops.** Opening the service (start,
  reload, a CLI command run here) never deletes state: a briefly missing
  config.toml, or a playlist commented out, would otherwise lose its
  history, deck and hold for good. `prune` (protocol command, `wallpaper
  prune`, in the app or here like `status`) drops the state of playlist
  ids not in the config and names them; it refuses when there is no
  config file.
- **One rule for a `ConfigError`**: it stops the service wherever it comes
  from (a tick, a command, the overview; `ServiceWorker._failed`), and
  commands answer with it until a reload.
- **`core/config_edit.py`** (Qt-free): `read_settings` takes the editable
  values as written (texts, not parsed, so a bad value in the file can be
  fixed in the form); `render` applies only what differs between the
  settings read and the edited ones, through tomlkit: no change = the same
  bytes; a changed value keeps its trailing comment *in its column*; paths
  and monitor ids with backslashes are written as literal strings (basic if
  the text has a `'`); line endings are kept (CRLF files stay CRLF). A
  rename rewrites only the header line (tomlkit renders headers from the
  table's display name; added playlists are appended after re-parsing). A
  new key goes after the table's last key, not after the comment block
  that introduces the next table, and a top-level key after the last
  top-level one. Deleting a table also deletes the comment block right
  above its header (after a blank line) and keeps the one above the next
  table: tomlkit reads those blocks as the end of the previous table, so
  this needs its internals (`_map`, `_insert_after`, the body list), pinned
  by tests. Before writing, the text must pass `config.loads_config` (the
  core's parser, now also on text); the write is atomic and refused if the
  file's *content* changed since it was read (`ConfigConflictError`).
- **Names**: a new or changed playlist name must be a bare TOML key
  (letters, digits, `-`, `_`); names already in the file, quoted ones too,
  are kept and editable. Unique case-insensitively (the core looks names up
  that way).
- **Validation** (`validate`) per field, shown under the field; Save stays
  off and its tooltip and the line beside it say why. A missing folder is
  an error only when it is new or changed, so an unplugged drive doesn't
  block other edits. A playlist without monitors is an error: delete it
  or give it a monitor. No playlists at all is fine (the app rotates
  nothing), and so is no file: the window starts empty and Save creates
  it.
- **Monitors**: the layout window's `MonitorsView`, clickable here
  (`clicked`); a click pops up the playlists, *No playlist* and *New
  playlist…*. Each playlist also has a *Monitors* menu (keyboard
  reachable) listing every monitor ("— now in konachan" for another's) and
  its unlisted monitors (by id, "not connected": unticking removes
  them). Choosing moves the monitor, so one playlist per monitor holds by
  construction (`assign_monitor`). The config holds monitor ids only, never
  names: two monitors of one model share a name, and a name would need
  rules for which one it means. A new playlist gets a random id
  (`new_playlist_id`), so it never takes over a deleted one's state.
- **Saving**: Save and Revert only while something changed; after a save
  the form is rebuilt from the saved text and the app is asked to reload
  (the banner says the outcome). A conflict keeps the edits and offers
  *Load the file again* (which drops them). Closing with changes asks Save
  / Discard / Cancel; a Save that can't happen keeps the window open.
- **About**: version from the package metadata plus `git describe
  --always --dirty` when run from a checkout; locations: the config, the
  state database, the two lists (as the config says, else the defaults),
  both logs, the render cache and the thumbnail cache
  (`paths.thumbnail_dir`, for Phase 5). *Open folder* opens the nearest
  existing folder. No sizes on disk (walking a 20k-image thumbnail cache
  on the UI thread would stall it); stats later, with `stats`.
- Not done: images in the settings window's drawing (the layout window
  has them), per-playlist `sound` (Phase 7), reordering playlists.

One window, implemented (decisions made while building it):

- **Names**: the layout window became `HomePage` (`PlaylistCard` ->
  `PlaylistRow`), the settings window `SettingsPage`, the library
  placeholder `LibraryPage`; `MainWindow` (window.py) holds them in a
  `QStackedWidget` beside its `Sidebar`. The UI socket's request names a
  `page` ("home", "library", "settings"), the CLI `wallpaper ui [page]`
  (default home), the tray *Open wallpaper_py* / *Library* / *Settings…*.
  The UI process has exactly one window now: it is created at start and
  `MainWindow.open_page` shows a page and brings the window to the front;
  its closing quits the process.
- **Tones** (`theme.derive_tones`, from the application's palette): dark
  mode: the page is the palette's Window colour `darker(122)` (Windows 11's
  #202020 gives #1a1a1a, as in the mockup); lines, panels and fields are
  the page mixed towards the text colour (9 %, 4 %, 7 %); muted text 24 %
  towards the page; hover and accent fills are the text / accent at 6 % /
  14 % alpha. The accent is lightened until readable on dark (Windows'
  #0078d4 becomes about #40acff; the mockup's #60cdff is Windows' own
  light accent, which Qt doesn't expose). Light mode: the page is the
  Window colour, the panel behind images silver (the page mixed 13 %
  towards the text: about #d4d4d4 on Windows) with the page's dark text and
  accent; the first near-black panel was too stark (changed after the
  desktop checks, picked from four tones in the mockup).
  The status dot's green / red are the only fixed colours besides the
  backdrop (the palette has no such roles), one pair per mode.
- **Applying them without stylesheets**: the window sets *only* Window,
  Base, Button (both the field tone) and PlaceholderText on its palette,
  from an empty `QPalette()`. Copying the application's palette instead
  marks every role as set, and the window then never hears of a palette
  change (found while testing). Everything drawn here (sidebar items,
  rows, tabs, text buttons, dividers, the panel, chips, the summary rows)
  reads `theme.tones()` while painting. Fields are `setFrame(False)` with
  the Base / Button tone; Save gets the accent through its Button /
  ButtonText roles (its disabled look stays the plain field tone). With
  Qt's windows11 style this gives the borderless filled fields of the
  mockup; Fusion (tests, offscreen) draws the combo boxes with a slight
  bevel. Styles put a combo box's text a few pixels from a line edit's
  (Windows 11: about 7 px against 10), so the drop-downs are
  `theme.FieldCombo`: the style draws the box and arrow, the text is
  drawn at `theme.field_text_x` (the style's line-edit contents + our
  4 px text margin + QLineEdit's own 2 px), as are read-only values
  (`theme.ValueLabel`). Polish after desktop review; still no stylesheet.
- **Text size**: Qt's default font on Windows is 9 pt (12 px); Windows 11's
  body text (and the mockup's) is 14 px. The UI process raises its font to
  at least 10.5 pt (`theme.body_font`), so a larger system font is kept.
- **Home**: rows, not cards (no columns to reflow any more); a hovered row
  gets a faint fill (the old accent edge is gone); toggles that are on are
  the accent icon, filled, on a faint accent fill. Durations are written
  as the config writes them ("every 25m", "next in 21m", "5m50s left once
  resumed": seconds under 10 minutes). The paused state moved from Home's
  banner to the sidebar's status line; errors still show on the page.
- **Settings**: `SettingRow` (name, description, control column 360 px
  wide); `PlaylistEditor` open or a `PlaylistSummary` row (a closed
  playlist with a problem shows an alert there); one open at a time
  (`SettingsPage.open_uid`, kept across revert / save, a new playlist
  opens). The interval became a *Changes* row: *Every* + duration, or
  *Manually* (the duration hidden; it writes `interval = "manual"`, as the
  old checkbox did). Order's description follows the choice. The
  *Monitors* row keeps its menu as an accent *Choose…* button (the
  screenshot shows only the text; the menu is the keyboard way to change
  monitors). The status line in the footer says what changed ("Changes:
  interval of konachan. Not saved yet.", `change_summary`) or why Save is
  off. The drawing here is smaller, with one-line captions ("PLX2783H ·
  konachan"); a monitor too narrow for that gets two lines (the name, then
  the playlist, each elided; at first the playlist was dropped, which at
  150 % scaling hid it), and room for two lines is kept only when some
  caption needs it. Sub-tabs are our own `theme.Tabs` (QTabWidget can't be
  made to look like the mockup without a stylesheet).
- **Closing** with unsaved settings: `MainWindow.closeEvent` shows the
  Settings page, then `SettingsPage.confirm_close` asks Save / Discard /
  Cancel (unchanged logic); the window title carries "*" while modified.
- A left click on the tray icon opens the Home window; the Library page is a
  placeholder (Phase 5).

## Phase 5: library window

Features: the "Library window" section of [todo.md](../todo.md) (enlarge
with path / size / dimensions, toggles, filter, sort, ...), plus **"Show on
<playlist>"** (pick, see Phase 4) in the enlarged view.

Sorting by "added": Favourites and To remove use the line order of their
.txt files (entries are appended). The library uses the file's creation
time on Windows (taken from the symlink itself for links, i.e. when it was
added to the library; not mtime, which copies keep), and mtime on Linux
(no portable creation time in Python).

Reading and updating state (the windows run in the UI process, a client
of the app's socket; see Phase 4):

- All writes are socket commands (fav / remove / pick / hold ...), which
  the app runs through `ServiceWorker.execute`: the service stays the only
  writer.
- Windows may read directly, without a round trip: a read-only SQLite
  connection (history, deck / upcoming; WAL allows it) and the
  favourites / to-remove .txt files.
- They re-read on events received through `subscribe`, not only the
  `Overview`: `marks_changed` (favourites / to-remove, including external
  edits of the .txt files, noticed on a tick by their mtime / size),
  `history_changed(playlist)`, and `changed` (a wallpaper changed, from a
  tick *or* a command). Add these to docs/app-wiring.md.

Tabs: Library, Favourites, Removed, History, Upcoming. Grids that scale to
20k images (`QListView` icon mode with a virtual model; thumbnails decoded in
worker threads with a LIFO queue, Pillow draft mode, an LRU memory budget
(built in, generous: ~1.5 GB, no setting), a
disk cache, at tile size x DPR). Background pre-generation of the disk cache.

Memory: the thumbnails live in the UI process, so closing the last window
ends that process and gives everything back; the always-running app never
holds thumbnails. The disk cache stays, so reopening is still fast. The
budget covers long sessions with the window open; no "Free memory" button
is needed (closing the window does that).

## Phase 6: KDE backend

plasmashell D-Bus (`evaluateScript`) for per-monitor wallpapers (per
activity), KScreen for monitor identity. Run `spikes/kde_probe.sh` on Arch
first. Then CI and checks on Kubuntu 26.04 as well, and the whole of
[desktop-checks.md](desktop-checks.md) by hand on Arch and Kubuntu.

Linux Qt dependencies (promised early on, not done yet):

- README: the system packages per distro (Ubuntu/Kubuntu, Arch), e.g.
  `libxcb-cursor0` for the xcb plugin since Qt 6.5, and the libEGL / libGL
  / xkbcommon / fontconfig set CI installs.
- A readable startup error when Qt can't load its platform plugin or a
  library, naming the package to install (and `QT_DEBUG_PLUGINS=1` for
  anything else), instead of Qt's cryptic message.

## Phase 7: polish

Nice-to-haves from [todo.md](../todo.md): thumbnail fade-in, sound on
wallpaper change, image overlay. Packaging/installer.

Sound on wallpaper change: per playlist, `sound = true` in its config table
(off by default; a toggle in the settings panel). Played by the app (it
runs anyway; the UI process may not), on any change the playlist makes:
scheduled, next / prev / pick. One short bundled sound, through Qt
Multimedia (`QSoundEffect`); a custom sound file per playlist only if
missed.

Virtual backend and page renders (nice to have, for testing; not needed
before anything else):

- A virtual backend that is part of the app, not only of the tests:
  `--backend virtual` (or an environment variable) takes made-up
  monitors from a small file (ids, names, sizes, positions, attached
  or not) and "sets" wallpapers by recording them, never touching the
  desktop. It runs with its own data dir and socket names, so it never
  meets a real running app. Much of it exists in `tests/fakes.py`.
- End-to-end tests on it: the real app process, the real UI process
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
