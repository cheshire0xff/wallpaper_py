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
| [2. Playlists](#2-playlists) | ok, except a reboot (step 4; an app restart is ok 2026-11-11) | todo | todo |
| [3. Pause on lock and idle](#3-pause-on-lock-and-idle) | ok | todo | todo |
| [4. Monitor switched off](#4-monitor-switched-off) | ok 2026-11-11 for a mirrored group (from the log); a single-monitor playlist not yet | todo | todo |
| [5. CLI and the app](#5-cli-and-the-app) | ok, except step 3 | todo | todo |
| [6. Tray](#6-tray) | ok, except step 2 and a broken config (step 3) | todo | todo |
| [7. Start at login](#7-start-at-login) | todo | todo | todo |
| [8. Windows (UI process)](#8-windows-ui-process) | ok 2026-11-11 | todo | todo |
| [9. Pick, hold and manual](#9-pick-hold-and-manual) | ok 2026-11-11, except waiting past an interval (steps 3, 6) | todo | todo |
| [10. Layout window](#10-layout-window) | ok 2026-11-11, except *Remove* (step 3) and steps 4, 5, 7 | todo | todo |

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
7. With a window open, `wallpaper next`: the window updates (the layout
   window's card shows the new image; see section 10).

## 9. Pick, hold and manual

The state database's schema changed (version 2): an older `state.sqlite3`
is refused with a message naming the file to delete (in the tray, and from
any CLI command). Delete it first; only history and shuffle state are lost.

The behaviour behind every step below is already tested with the fake
backend (`tests/test_service.py`, `test_commands.py`, `test_cli.py`,
`test_tray.py`, `test_worker.py`, including hold across an app restart).
These steps only confirm what a test can't see: the image really appearing
on the right monitors, the real tray menu, and a real quit and restart.

1. `wallpaper pick <an image from another directory> <playlist>`: it shows
   on every attached monitor of the playlist (all members of a mirrored
   group). `wallpaper prev <playlist>` brings back what was shown before,
   and `wallpaper next <playlist>` the picked image again.
2. `wallpaper pick <missing file>` and `wallpaper pick <a .txt file>`: a
   clear error, and the wallpaper doesn't change.
3. Tray *Hold* on a playlist: the menu header says *(held)*, the item is
   ticked, and the playlist doesn't change past its interval (the other
   playlists do). *Next* still works. `wallpaper status` says *held*.
4. With a playlist held, quit the app (tray *Quit*) and start it again: it
   is still held, and its monitors get its current image back.
5. Untick *Hold* (or `wallpaper resume`): it changes again once the rest of
   its interval has passed.
6. Set a playlist to `interval = "manual"` and *Reload config*: the header
   says *(manual)*, there is no *Hold* item, and it never changes by itself
   (leave it well past its old interval, also across an app restart).
   `wallpaper hold <it>` says why it can't be held; `next` and `pick` work.

## 10. Layout window

`wallpaper ui layout` (or tray *Open layout*). What the tests can't see:
the real arrangement, real images, the system's theme. The logic (scaling,
card text, which command each button sends, toggles following the app,
thumbnails off the UI thread) is tested with fakes (`tests/test_layout.py`,
`test_thumbnails.py`, `test_theme.py`).

1. *Monitors* matches the real arrangement: the PLX2783H left of the
   AW3225QF (it sits at -1920,0), sizes to scale, each showing its current
   image, with no outline; under each: its name, resolution (e.g.
   3840×2160) and playlist. A monitor in no playlist says
   "no playlist". Resize the window: the drawing rescales, nothing is cut
   off, and the cards rearrange into fewer or more columns. Hover a card:
   its monitors get an accent outline; hover a monitor: its playlist's card
   gets an accent edge; move away: both go.
2. Each playlist card: the thumbnail (letterboxed, on the dark backdrop),
   name, monitors (a link icon for the mirrored group), the image's file
   name, then "dimensions · file size · order, interval · next in …";
   dimensions and size match the file (e.g. in Explorer's properties). The
   time left counts down, and the card shows the new image when it
   changes.
3. Each button, with its tooltip: *Previous* and *Next* change the
   wallpaper on the real monitors; *Favourite* fills the star with the
   accent (and favourites.txt has the image), a second click clears it;
   *Remove* lists the image in to-remove.txt and moves on; *Hold* fills the
   pin, shows the *Held* chip and "… left once resumed", and a second
   click resumes. Toggling in the tray shows in the window too.
4. The manual playlist (ZeroMOD): a *Manual* chip, no hold button, "manual"
   instead of the time left.
5. An error shows in the card, not as a dialog: e.g. *Next* on a playlist
   whose monitors are all off.
6. Switch the system between light and dark mode (Settings > Personalisation
   > Colours) with the window open: the window and its icons follow;
   the monitor drawing and thumbnails stay on the dark backdrop.
7. Thumbnails never stall the window: with a playlist of large images
   (4K PNGs), press *Next* repeatedly and move / resize the window
   meanwhile; it stays responsive, and each card ends on its current image.
8. Switch a monitor off (e.g. OLED refresh) with the window open: within a
   few seconds it is drawn dimmed, marked "off", to the right of
   the others, at its usual size; the others are drawn where Windows now
   puts them (it may move them). Switched on, it returns to its real
   position.
