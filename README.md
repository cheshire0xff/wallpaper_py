# wallpaper_py

Per-monitor wallpaper management for **Windows** and (planned) **KDE Plasma**.

The goal is a tray app that runs wallpaper playlists per monitor (or per group
of mirrored monitors), keeps history and favourites, and browses large
libraries (10k+ images). See [todo.md](todo.md) for the feature list,
[docs/plan.md](docs/plan.md) for the phases and
[docs/architecture.md](docs/architecture.md) for the design.

**Status:** the core, the Windows backend, the playlist service and the tray
app exist, plus the UI process the windows will run in (for now one
placeholder window). The layout and library windows are next.

## Install

Python 3.12+, managed with [uv](https://docs.astral.sh/uv/).

```
uv sync                           # creates .venv with the package and dev tools
uv run wallpaper list             # or activate .venv and run `wallpaper` directly
uv run python scripts/check.py    # black, mypy, pylint, pytest (as CI does)
```

## CLI

```
wallpaper list [--all]
wallpaper set IMAGE --monitor NAME [--mode fill|fit|stretch]
```

- `list` shows each monitor's name, position, size, current wallpaper and id;
  `--all` includes monitors that are known but detached (switched off).
- `--monitor` takes a monitor name, a unique part of it (`zero` for
  `ZeroMOD`), or the full id. **Numbers are refused on purpose**: monitor
  order changes when a monitor is switched off, which is exactly how a
  wallpaper ends up on the wrong screen.
- `--mode`: Windows applies one mode to all monitors, so the image is rendered
  to the monitor's exact resolution instead, letting each monitor use its own
  mode. If the global mode is *tile* or *span* (used by tools that set one
  combined image, e.g. John's Background Switcher) it is switched to *fill*.
  Close such tools first, or they will overwrite the result.

### Playlists

```
wallpaper [-c CONFIG] tray                   # the app, in the tray (what starts at login)
wallpaper [-c CONFIG] run [--poll SECONDS]   # the app, headless in this terminal (Ctrl+C stops)
wallpaper status                             # playlists, monitors, current, upcoming
wallpaper next|prev|fav|unfav|remove [TARGET]  # TARGET: playlist or monitor name
wallpaper ui [layout|library]                # open a window (see below)
```

Playlists are defined in a TOML file; start from
[docs/config.example.toml](docs/config.example.toml) and save it where
`wallpaper --help` says (or pass `--config`).

- A playlist rotates a directory (recursively), *sorted* (natural order) or
  *shuffled* (every image once before any repeats; the order is kept across
  reboots), on one monitor or a **mirrored group**.
- The interval counts *active* time since the last change: it pauses while
  the session is locked or idle, while none of the playlist's monitors is
  attached, and while the PC sleeps. `next` / `prev` start it again.
- A monitor that is switched off is never written to: its playlist waits
  (a group keeps going on its other members) and the monitor gets the
  current image when it comes back.
- `fav` and `remove` append the current image to `favourites.txt` /
  `to-remove.txt` (one full path per line, editable by hand); `unfav` takes
  it out of favourites again. `remove` only lists the image and hides it
  from rotation; deleting is up to you.

### The app

`tray` and `run` start the same app; only one can run at a time (per
user). It rotates the playlists, and the other commands (`status`, `next`,
...) are sent to it, so they never collide with a change in progress. When
no app is running, those commands start it in the tray first (as *Start at
login* would) and then run in it; if it doesn't come up within ~10 s, they
say so and point to the log file. `list` and `set` work without the app.
`--verbose` (before the command: `wallpaper --verbose next`) logs whether a
command reached the running app or started one, and the app's raw reply.

The tray menu has a section per playlist (its monitors by name, the current
image, which opens on click, *Next*, *Previous*, a *Favourite* toggle,
*Remove*), then *Open layout*, *Open library*, *Reload config* (after
editing it), *Open config folder*, *Start at login* and *Quit*. Left-click opens the same menu. The icon turns
grey while rotation is paused and shows a red badge if the config can't be
loaded. Start at login uses the per-user Run key on Windows and
`~/.config/autostart` on Linux; it starts `pythonw -m wallpaper_py tray`
from the current environment, so no console window opens. The tray app logs
to `wallpaper.log` in the per-user log directory (on Windows
`%LOCALAPPDATA%\wallpaper_py\Logs`).

The windows run in a separate process (`wallpaper ui`), started when one
is opened and gone when the last one closes, so they cost nothing while
closed. Opening a window while that process runs just brings the window up;
it starts the app if needed and closes when the app quits. For now it shows
a placeholder with the live state. It logs to `ui.log` next to
`wallpaper.log`.

Other programs can drive the app over the same socket: see
[docs/protocol.md](docs/protocol.md).

`python -m wallpaper_py ...` works too.

## Development

```
python scripts/check.py            # black --check, mypy (strict), pylint, pytest
python scripts/check.py black      # format
```

CI runs the same on Windows and Ubuntu. Tests marked `windows` read the real
desktop (never change it) and are skipped elsewhere. Qt tests run offscreen
(`tests/conftest.py`) with per-test data directories, so they never show an
icon or reach a running app. What only a real desktop can show is checked
by hand: [docs/desktop-checks.md](docs/desktop-checks.md).

`spikes/` holds throwaway experiments (monitor behaviour when switched off,
the 20k-image grid prototype); see [spikes/README.md](spikes/README.md).
