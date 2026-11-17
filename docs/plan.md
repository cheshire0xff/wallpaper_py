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
| 5 | Library page: stats, grids, enlarged view | done |
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
and Phase 5 steps 1–4's notes: `5696a9f`; Phase 5's: `268a951`).

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
  painted in code ([design/](design/README.md)).
- **Golden-image tests** (see Phase 7).
- **QML for the windows.** Qt Quick would give bindings, animations and an
  asynchronous image grid, but it means rewriting ~6,000 lines and their
  offscreen tests, mypy can't check QML, and the Qt for Python rules
  become other rules. Only if the windows keep growing a lot.

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
- Once: change the accent in KDE with the window open (does it follow at
  once, or on the next start?). The window takes `Highlight` there, as Qt
  puts KDE's accent only in it.

## Phase 7: polish

Nice-to-haves from [todo.md](../todo.md): sound on wallpaper change.
Packaging/installer.

Library, later (left out of Phase 5):

- **Selection** (several tiles, then F / Del or a button). Open: what F
  does on a mix of marked and unmarked; Del on a favourite (both marks,
  or one); a selection partly hidden by a filter or *Hide removed*;
  History's repeats of one image; Ctrl+A over 20k images (one command
  each rewrites the list file every time: a command for many images, a
  protocol change); a failure partway (what is reported, an undo); the
  selection kept when the list is read again. With it, a keyboard way to
  open a tile (today there is no current tile).
- **Clicking a badge** to toggle the mark: on Favourites the tile then
  vanishes under the pointer; better with selection, or in the enlarged
  view.
- An *Unmarked only* chip on All images (neither listed).

Sound on wallpaper change: a per-playlist setting (off by default), a row
in the playlist's settings. Played by the app (it runs anyway; the UI
process may not), on any change the playlist makes: scheduled, next / prev
/ pick. One short bundled sound, through Qt Multimedia (`QSoundEffect`); a
custom sound file per playlist only if missed.

Sizes on disk of the render and thumbnail caches in About: never worked
out on the main thread (walking a 20k-image thumbnail cache would stall
it).

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
  smoothing difference between machines; maybe once the look settles.
