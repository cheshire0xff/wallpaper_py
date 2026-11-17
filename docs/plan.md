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
| S | Simplification: words, protocol and plumbing, tray / CLI / window as clients | done |
| 5 | Library page: stats, grids, enlarged view | steps 1–5 done; 6 next |
| 6 | KDE backend | backend done; Kubuntu and the checks by hand left |
| 7 | Polish | |

Finished phases are described as they work now, not as they were planned:
[architecture.md](architecture.md) (layers, app shell, playlist service,
monitor identity, looks), [app-wiring.md](app-wiring.md),
[protocol.md](protocol.md), and [design/](design/README.md) for the look.
What is still to try by hand: [desktop-checks.md](desktop-checks.md). The
plans of the finished phases, with the notes made while building them, are
in git: `git show d771a26:docs/plan.md` (Phase 4b step 5's: `92837ce`;
Phase 4c's: `cd44203`; Phase 4d's: `280a3ef`; the Simplification's
and Phase 5 steps 1–4's notes: `5696a9f`).

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

## Phase 5: Library page

The sidebar's Library page (a placeholder now): what a playlist picks
up, first in numbers, then as images. Features: the "Library window"
section of [todo.md](../todo.md), plus **"Show on <playlist>"** (`pick`)
in the enlarged view. The library stats of todo.md's "Settings / about"
item go to the Stats tab, not to About: About keeps the version and
the data locations (facts about the app, not the library), and loses its
"come in a later version" line.

### The page

The title, a playlist drop-down and the count of the open tab
(`Library  konachan ▾  · 20,431 images · 1,204 seen`; "312 of 20,431"
while filtered), then the tabs **All images**, **Favourites**, **To
remove**, **History**, **Upcoming**, **Stats**; under a grid tab, its
toolbar (filter, sort, chips) and the grid, as in
[design/library.png](design/library.png). Stats has no toolbar.

The images come first: a library is its images (the phone app's opens on
All Images), and the numbers go last, as such tabs usually do; the seen
count in the header shows the cycle on every tab.

- The drop-down picks the playlist for Stats, All images, History and
  Upcoming. Favourites and To remove are one list each for the whole
  app: their tabs show the whole list, with an *Only <playlist>* chip
  (off by default) for the entries in the playlist's folder.
- Remembered: the playlist, the tab, each tab's sort and chips. In
  `ui-state.json` in the data directory, written only by the window process
  (a pydantic model; missing or unreadable: the defaults, logged, never
  an error shown). It is view state, not settings: no revision, no
  socket, nothing the app reads. Not `QSettings`: that is the registry on
  Windows, outside the data locations About lists.

### Stats tab

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

- **Stats** is the last tab, and All images the first (where a new user
  lands). The remembered tab is stored by name; a file that still says
  `overview` falls back to the defaults once (pre-release, no
  migration).
- **All images**: the folder; *Hide removed* on by default.
- **Favourites**, **To remove**: the list; *Only <playlist>*.
- **History**: the playlist's entries, newest first, each time it was
  shown (repeats kept), captioned with when ("2 h ago"); *Hide removed*
  on by default, as on All images.
- **Upcoming**: the next 500 in order (`upcoming`); no sort. The count
  says "500+" when more come after them.
- Sort, per tab: name A–Z / Z–A, newest / oldest added (History: newest
  / oldest shown).
- Filter by name: a case-insensitive part of the file name; the grid
  narrows as you type (debounced) and the count follows. While filtered,
  each tile has a caption: the name around the match, the match in bold
  (as the phone app's search lists names: you see why a tile is there).
  A click opens the image enlarged (step 6).
- Badges as in the mockup: favourite star, to-remove mark, *On screen*
  (the current image of any playlist). A *Badges* switch on every grid tab
  (on by default, remembered per tab) hides them; History's "2 h ago"
  and the captions stay.
- A slim, draggable fast-scroll bar; back to the top when the sort,
  filter or playlist changes.
- Smooth with 20k images: [desktop-checks.md](desktop-checks.md) has
  one step for it (scrolling and memory, which no test sees).

Decided while building them (step 5):

- Default sorts: All images name A–Z (as the mockup), Favourites, To
  remove and History newest first. Name sorts go by the file name
  (natural order, any case), not the folder: a list mixes folders.
- The stats are asked on every tab while the page is shown (the header's
  seen count), and again when the Stats tab is shown.
- The badges sit on the image, so their colours are fixed: a white outline
  star and bin (the star as on Home) on dark discs, white text on dark
  pills. Not the accent: it fights the images. The bin is the to-remove
  mark, left of the star.
- The fast-scroll bar: dragging the handle moves the grid with it;
  pressing the track elsewhere jumps there first. No letter bubble.
- A tab with nothing says so in place of the grid ("No favourites.";
  "No file name has “x” in it." while filtered); a list that can't be
  read (a BOM, busy) says why there.
- A list read again (a marks change, the folder listed again on showing
  the tab) keeps the scroll; only the sort, the filter, the chip and
  another playlist go back to the top.
- History and Upcoming are read again on their playlist's `changed`, but
  on `overview_changed` only after a save (the playlists' ids, names or
  folders changed), not after every new image of another playlist.
- The click to open an image enlarged comes with the enlarged view
  (step 6).
- What narrows the list is a chip, not a switch: *Hide removed* and
  *Only <playlist>*, right after the sort. Drawn as Mode's segments in
  Settings (4 px corners, field height), with a ✓ while on. *Badges* changes
  what the tiles show, not which, so it is a switch, at the end (a chip
  there read as a filter). Settings keeps its switches.
- Favourites and To remove stay tabs, not chips on All images: they are
  app-wide lists (All images is one playlist's folder), sorted by when
  added, and hold entries outside every folder. *Only <playlist>* stays
  off by default.
- Upcoming stays at 500: asking more costs the daemon's service thread a
  file check per image (10k: about 325 ms).
- Nothing is selected in the grid (a click drew a ring that did
  nothing).

Later (not in Phase 5):

- **Selection** (several tiles, then F / Del or a button). Open: what F
  does on a mix of marked and unmarked; Del on a favourite (both marks,
  or one); a selection partly hidden by a filter or *Hide removed*;
  History's repeats of one image; Ctrl+A over 20k images (one command
  each rewrites the list file every time: a command for many images, a
  protocol change); a failure partway (what is reported, an undo); the
  selection kept when the list is read again.
- **Clicking a badge** to toggle the mark: on Favourites the tile then
  vanishes under the pointer; better with selection, or in the enlarged
  view.
- An *Unmarked only* chip on All images (neither listed).

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
- **Switches** for on / off, not check boxes: the two in Settings,
  *Badges* in a grid's toolbar. As in the NVIDIA app (changed after the
  mockup, in step 5): a thin track under a bigger knob; on, an accent
  knob on the accent dimmed; off, a grey knob on a grey track. **Chips** for what narrows a grid
  (*Hide removed*, *Only <playlist>*).
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

1. **Overview** (done).
2. **Controls** (done).
3. **Data for the grids** (done).
4. **Thumbnails** (done).
5. **Grid tabs** (done).
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
it). (The library stats moved to Phase 5's Stats tab.)

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
