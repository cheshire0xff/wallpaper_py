# wallpaper_py

Per-monitor wallpaper management for **Windows** and (planned) **KDE Plasma**.

The goal is a tray app that runs wallpaper playlists per monitor (or per group
of mirrored monitors), keeps history and favourites, and browses large
libraries (10k+ images). See [todo.md](todo.md) for the feature list,
[docs/plan.md](docs/plan.md) for the phases and
[docs/architecture.md](docs/architecture.md) for the design.

**Status:** the core and the Windows backend exist, with a small CLI. The
playlist service, tray and GUI are next.

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
