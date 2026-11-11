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
| 3 | App shell: tray, single instance, IPC, autostart | next |
| 4 | Layout window | |
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
    dir.

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
(same image twice in history). Once the app runs, CLI commands go through
its socket and run on the one service, serialized with its ticks. If the
app isn't running, the CLI may still act directly (as now).

The tick timer passes its period as `tick_period`, so gaps up to two
periods count as active time (longer ones are sleep).

## Phase 4: layout window

Monitors drawn to scale in their real arrangement, each showing a thumbnail
of its current wallpaper. Assign playlists and groups, and run next/prev/fav
from here.

## Phase 5: library window

Tabs: Library, Favourites, Removed, History, Upcoming. Grids that scale to
20k images (`QListView` icon mode with a virtual model; thumbnails decoded in
worker threads with a LIFO queue, Pillow draft mode, an LRU memory budget, a
disk cache, at tile size x DPR). Background pre-generation of the disk cache.

## Phase 6: KDE backend

plasmashell D-Bus (`evaluateScript`) for per-monitor wallpapers (per
activity), KScreen for monitor identity. Run `spikes/kde_probe.sh` on Arch
first. Then CI and checks on Kubuntu 26.04 as well.

## Phase 7: polish

Nice-to-haves from [todo.md](../todo.md): thumbnail fade-in, sound on
wallpaper change, image overlay. Packaging/installer.
