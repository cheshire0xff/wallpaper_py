# wallpaper_py

Per-monitor wallpaper management for **Windows** and (planned) **KDE Plasma**.

The goal is a tray app that runs wallpaper playlists per monitor (or per group
of mirrored monitors), keeps history and favourites, and browses large
libraries (10k+ images). See [todo.md](todo.md) for the feature list,
[docs/plan.md](docs/plan.md) for the phases and
[docs/architecture.md](docs/architecture.md) for the design.

**Status:** the core, the Windows backend and the playlist service exist,
driven from the CLI (`wallpaper run` in a terminal). The tray and GUI are
next.

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
wallpaper [-c CONFIG] run [--poll SECONDS]   # rotate in the foreground (Ctrl+C stops)
wallpaper status                             # playlists, monitors, current, upcoming
wallpaper next|prev|fav|remove [TARGET]      # TARGET: playlist or monitor name
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
  `to-remove.txt` (one full path per line, editable by hand). `remove` only
  lists the image and hides it from rotation; deleting is up to you.
- `next` / `prev` / `fav` / `remove` work while `run` is running in another
  terminal: both use the same state database.

`python -m wallpaper_py ...` works too.

## Development

```
python scripts/check.py            # black --check, mypy (strict), pylint, pytest
python scripts/check.py black      # format
```

CI runs the same on Windows and Ubuntu. Tests marked `windows` read the real
desktop (never change it) and are skipped elsewhere.

`spikes/` holds throwaway experiments (monitor behaviour when switched off,
the 20k-image grid prototype); see [spikes/README.md](spikes/README.md).
