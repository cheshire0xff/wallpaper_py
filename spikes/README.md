# Phase 0 spikes

Throwaway experiments that answer questions before the rewrite builds on them.
Not part of the package; not checked by `scripts/check.py`. They need PySide6,
which is not a project dependency yet, hence `uv run --with`.

## 1. Monitor probe (Windows, also runs on Linux)

Read-only. Shows what `IDesktopWallpaper` and Qt report about each monitor.

```
uv run --with PySide6-Essentials spikes/monitor_probe.py            # snapshot
uv run --with PySide6-Essentials spikes/monitor_probe.py --watch    # log changes
```

**OLED test:** start `--watch`, switch the OLED off (let it start its panel
refresh), wait ~1 min, switch it on, wait ~1 min, Ctrl+C. Send
`spikes/monitor_probe.log`.

### Findings (Windows 11, 2026-11-11)

Monitors: AW3225QF 4K OLED (DELA246, primary, 150 %), PLX2783H FullHD
(IVM6611, left), ZeroMOD 1920x480 (___3132, right). Three stale entries with an
empty device path and `GetMonitorRECT` = `E_FAIL` are always listed.

Switching the OLED off **fully detaches it**:

| t | Qt | IDesktopWallpaper |
|---|---|---|
| +0 s | `primaryScreenChanged` → PLX2783H, `screenRemoved` AW3225QF | — |
| +1 s | `geometryChanged` on the others | OLED gone; **index 0 is now the FullHD**, all monitors move (FullHD to 0,0) |
| +2 s | — | OLED device path back in the list, `GetMonitorRECT` fails |
| on, +0 s | `screenAdded`, `primaryScreenChanged` | still old state |
| on, +2 s | — | original layout restored |

Consequences for the design:
- Index-based addressing (`--monitor 0`) would hit the wrong monitor - confirmed.
- Geometry also shifts, so it is not an identity either. Only the device path
  is stable across off/on.
- Attached = device path listed **and** `GetMonitorRECT` succeeds.
- COM lags Qt's signals by 1-2 s: on any screen signal, debounce and re-query
  until two consecutive snapshots match before setting anything.

## 2. Grid prototype

```
uv run --with PySide6-Essentials spikes/grid_prototype.py D:\pictures\KONACHAN --repeat 2
```

Scroll around, drag the scrollbar end to end, press *Drop caches*; the status
bar shows queue, decode timings and process memory. `--auto` runs a scripted
benchmark and prints results. Thumbnails are cached in
`%LOCALAPPDATA%\wallpaper_py\spike-thumbs` (safe to delete).

## 3. KDE probe (Arch / Kubuntu)

Read-only.

```
bash spikes/kde_probe.sh                      # full report
bash spikes/kde_probe.sh --snapshot oled-on   # then switch OLED off, wait 30 s:
bash spikes/kde_probe.sh --snapshot oled-off
```

Send the `spikes/kde_probe-*.log` files.

### Findings (Arch, Plasma 6.8.2, Wayland, 2026-11-14)

Same three monitors. `kscreen-doctor -j` lists only connected outputs (no
EDID in its JSON); sizes are pixels, already rotated (the ZeroMOD's
480x1920 mode at rotation 8 is 1920x480); positions are logical. Plasma's
`screenGeometry` is logical too (the 4K at 150 %: 2560x1440). The
scripting API has `screenForConnector(name)`, so connector -> Plasma screen
needs no geometry matching. EDIDs are readable in
`/sys/class/drm/card*-<connector>/edid`. `grabContainmentImage(connector,
0, 0, path)` saves one desktop without windows: a way to see a wallpaper.
`ScreenSaver.GetSessionIdleTime` is not supported on Wayland; `GetActive`
works. With `QT_QPA_PLATFORM` set, `kscreen-doctor` lists no outputs.
