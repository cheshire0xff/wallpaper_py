# Desktop checks

Tests never touch the real desktop (they use fakes), so these are tried by
hand on each platform. Repeat the whole list for the KDE backend (Phase 6,
on Arch and on Kubuntu), and the affected sections after a change to them.

Add `--verbose` to a CLI command to see whether it reached the running app
or started one. The app logs to `wallpaper.log`, the windows to `ui.log`
(both in the per-user log directory: `%LOCALAPPDATA%\wallpaper_py\Logs` on
Windows).

Status: **ok** (with the date, when known) all steps seen working;
**todo** not tried yet; otherwise the steps still to try.

| Section | Windows 11 | KDE (Arch) | KDE (Kubuntu) |
|---|---|---|---|
| [1. Monitors and `set`](#1-monitors-and-set) | ok | todo | todo |
| [2. Playlists](#2-playlists) | ok, except step 4 | todo | todo |
| [3. Pause on lock and idle](#3-pause-on-lock-and-idle) | ok | todo | todo |
| [4. Monitor switched off](#4-monitor-switched-off) | todo | todo | todo |
| [5. CLI and the app](#5-cli-and-the-app) | ok, except `--verbose` and step 3 | todo | todo |
| [6. Tray](#6-tray) | steps 2 and 3 todo | todo | todo |
| [7. Start at login](#7-start-at-login) | todo | todo | todo |
| [8. Windows (UI process)](#8-windows-ui-process) | ok 2026-11-11 | todo | todo |

## 1. Monitors and `set`

1. `wallpaper list --all`: every monitor with its name, size and id; a
   switched-off one shows as detached.
2. `wallpaper set IMAGE --monitor <part of a name>` changes only that
   monitor; `--mode fit` renders it to the monitor's resolution.
3. `wallpaper set IMAGE --monitor 1` is refused (numbers are never used).

## 2. Playlists

With the real config (a mirrored group and a single monitor):

1. The first run creates the data directory with the state database.
2. Each playlist changes after its interval; a mirrored group shows the
   same image on all its monitors.
3. `next`, `prev`, `fav`, `unfav`, `remove` act on the right playlist, by
   playlist name and by monitor name; `fav` / `remove` append the full path
   to `favourites.txt` / `to-remove.txt`.
4. After a reboot (or quitting and starting the app), each monitor gets its
   current image back and shuffle continues where it was.

## 3. Pause on lock and idle

1. Lock the session: the log says `paused` within a tick (5 s), and
   `resumed` after unlocking. The tray icon turns grey meanwhile.
2. Leave the PC idle past `pause_when_idle`: `paused` (idle), then
   `resumed` on input.

## 4. Monitor switched off

The case that matters most: a wallpaper must never land on the wrong
monitor.

1. With the app running, switch the main monitor off (e.g. OLED refresh).
   Its playlist waits; the other monitors keep their own images; a mirrored
   group keeps going on its other members.
2. Switch it back on: it gets its playlist's current image within a few
   seconds.

## 5. CLI and the app

1. With no app running, `wallpaper --verbose next` says it started one;
   the tray icon appears and the command runs. No console window opens,
   not even briefly, and closing the terminal leaves the app running.
2. Again with the app running: `--verbose` says the running app answered.
3. `wallpaper tray` while one runs exits without starting a second.

## 6. Tray

1. The menu shows each playlist with its monitors and current image;
   *Next*, *Previous*, *Favourite*, *Remove* work from it.
2. Clicking the image name opens it.
3. *Reload config* after an edit applies it; a broken config shows the red
   badge and a notification, and fixing it clears both.
4. *Open config folder* opens it. *Quit* stops the app.

## 7. Start at login

1. Tick *Start at login*, sign out and in: the tray app is running, with
   no console window.
2. Untick it: the entry is gone (Windows: the HKCU Run key; Linux:
   `~/.config/autostart`).

## 8. Windows (UI process)

1. Quit the app, run `wallpaper ui`: the app starts (tray icon appears),
   then the window fills with the live state.
2. With the window open, tray *Open layout*: the same window comes to the
   front, also when minimised or behind another app. No second window.
3. Tray *Open library*: a second window in the same UI process.
4. Close both windows: the UI process exits; the tray app keeps running.
5. With no UI process, tray *Open layout*: one starts and its window comes
   to the front.
6. With a window open, tray *Quit*: the window closes too.
7. With a window open, `wallpaper next`: its "Last change" line updates.
