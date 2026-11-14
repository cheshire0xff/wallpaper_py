# Plan

One cross-platform Python app (Windows first, then KDE Plasma) built on
PySide6: starts at login straight to the tray, single instance, with a CLI
that talks to it. A Qt-free `core` holds all the logic; platform backends stay
thin. See [architecture.md](architecture.md) for the layers.

| Phase | What | Status |
|---|---|---|
| 0 | Spikes: monitor identity on Windows, 20k-image grid, KDE probe | done (KDE probe pending, needs Arch) |
| 1 | Core model, safe setter, Windows backend, CLI `list`/`set`, tooling, CI | done |
| 2 | Playlists, mirrored groups, scheduler, history, favourites, to-remove | done |
| 3 | App shell: tray, single instance, IPC, autostart | done |
| 4 | UI process, one window (Home, Library, Settings), pick / hold / manual | done |
| 4b | Settings in the database, per-monitor looks, previews, backgrounds | done |
| 4c | More backgrounds for Fit: dominant, blur | done |
| 5 | Library window | low priority |
| 6 | KDE backend | next |
| 7 | Polish | |

Finished phases are described as they work now, not as they were planned:
[architecture.md](architecture.md) (layers, app shell, playlist service,
monitor identity, looks), [app-wiring.md](app-wiring.md),
[protocol.md](protocol.md), and [design/](design/README.md) for the look.
What is still to try by hand: [desktop-checks.md](desktop-checks.md). The
plans of the finished phases, with the notes made while building them, are
in git: `git show d771a26:docs/plan.md` (Phase 4b step 5's: `92837ce`;
Phase 4c's: `cd44203`).

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
- **Stylesheets.** The look comes from the native style and the palette
  ([design/](design/README.md)).
- **Golden-image tests** (see Phase 7).

## Phase 5: library window

Low priority: the app already covers most of what is needed day to day.

Features: the "Library window" section of [todo.md](../todo.md) (enlarge
with path / size / dimensions, toggles, filter, sort, ...), plus **"Show on
<playlist>"** (`pick`) in the enlarged view.

Sorting by "added": Favourites and To remove use the line order of their
.txt files (entries are appended). The library uses the file's creation
time on Windows (taken from the symlink itself for links, i.e. when it was
added to the library; not mtime, which copies keep), and mtime on Linux
(no portable creation time in Python).

Reading and updating state (the pages run in the UI process, a client of
the app's socket; see [architecture.md](architecture.md), "App shell"):

- All writes are socket commands (fav / remove / pick / hold ...), which
  the app runs through `ServiceWorker.execute`: the service stays the only
  writer.
- Pages may read directly, without a round trip: a read-only SQLite
  connection (history, deck / upcoming; WAL allows it) and the
  favourites / to-remove .txt files. Not the settings tables: those are
  read with the `settings` command.
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

Suggested split, one session each: read-only state and the new
`subscribe` events; the thumbnail pipeline; the library page; the enlarged
view; reset cycle and About.

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

Open (todo.md, normal priority): whether Save asks first when it deletes
a playlist's history (today the footer names it: "delete X and its
history").

Sound on wallpaper change: a per-playlist setting (off by default), a row
in the playlist's settings. Played by the app (it runs anyway; the UI
process may not), on any change the playlist makes: scheduled, next / prev
/ pick. One short bundled sound, through Qt Multimedia (`QSoundEffect`); a
custom sound file per playlist only if missed.

About page stats: library size per playlist, from a new `stats` command
(the service has the scan, the deck and the marks). No sizes on disk
worked out on the UI thread (walking a 20k-image thumbnail cache would
stall it).

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
