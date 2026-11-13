# Desktop checks

Tests never touch the real desktop (they use fakes), so these are tried by
hand on each platform. Repeat the whole list for the KDE backend (Phase 6,
on Arch and on Kubuntu), and the affected sections after a change to them.

Add `--verbose` to a CLI command to see whether it reached the running app
or ran the command itself. The app logs to `wallpaper.log`, the windows to `ui.log`
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
| [5. CLI and the app](#5-cli-and-the-app) | ok 2026-11-12 (steps 1 and 3 after the CLI started running commands itself) | todo | todo |
| [6. Tray](#6-tray) | ok, except step 2 and a broken config (step 3) | todo | todo |
| [7. Start at login](#7-start-at-login) | todo | todo | todo |
| [8.1 The window: opening, pages, closing](#81-opening-pages-and-closing) | steps 2, 3, 4, 9, 10 ok 2026-11-12 (step 9 before the silver light-mode panel); 1, 5-8 todo | todo | todo |
| [8.2 The window: Home](#82-home) | step 1 ok 2026-11-12; 2-7 todo as rows (as the layout window: ok 2026-11-11, except *Remove* and steps 4, 5, 6) | todo | todo |
| [8.3 The window: Settings](#83-settings) | steps 1, 2, 4, 7 ok 2026-11-12 (step 1 also on a copy of the real config); 3, 5, 6, 8-11 todo | todo | todo |
| [9. Pick, hold and manual](#9-pick-hold-and-manual) | ok 2026-11-11, except waiting past an interval (steps 3, 6) | todo | todo |

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
5. **A list file held open at start.** Quit the app; in PowerShell hold
   to-remove.txt open without sharing:
   `$f = [IO.File]::Open("<data dir>\to-remove.txt", 'Open', 'Read', 'None')`.
   Start the app: the log says once that no image is chosen until the
   to-remove list can be read, nothing changes past the interval, and
   `wallpaper next` answers with that error. `$f.Close()`: within a tick
   rotation goes on, never to an image in the list. The same with
   favourites.txt: the tray's and Home's *Favourite* are off and disabled
   until it can be read.
6. **Explorer restarted (Windows).** With the app running, Task Manager >
   Windows Explorer > Restart. At most one tick logs a wallpaper error (no
   traceback); after it, `wallpaper next` changes the wallpaper and
   rotation goes on at its interval.

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

1. With no app running, `wallpaper --verbose next` says it ran the command
   here; the wallpaper changes, no tray icon appears and no `pythonw` is
   left in Task Manager. `wallpaper status` ends with *wallpaper_py isn't
   running, so nothing rotates*, and `wallpaper reload` says the config is
   valid.
2. With the app running (`wallpaper tray`): `--verbose` says the running app
   answered, and `status` ends with *Rotation active*.
3. `wallpaper tray` while one runs exits without starting a second (after
   about 5 s: it waits in case a CLI command holds the lock).
4. **Prune.** Comment out a playlist in config.toml and *Reload config*,
   then put it back and reload: it continues with its history (loading
   never drops state). Take it out again and run `wallpaper prune`, with
   the app running and without: it names the dropped id; a second run has
   nothing to drop. With config.toml renamed away, `wallpaper prune`
   refuses (no config file) and drops nothing.

## 6. Tray

1. The tray and UI window (title bar/taskbar) show the layered photo-cards
   icon.
2. Left-clicking the tray icon opens the window on Home (right-click
   still opens the menu).
3. The menu shows each playlist with its monitors and current image;
   *Next*, *Previous*, *Favourite*, *Remove* work from it.
4. Clicking the image name opens it.
5. *Reload config* after an edit applies it; a broken config shows the red
   badge and a notification, and fixing it clears both.
6. *Open config folder* opens it. *Quit* stops the app.

## 7. Start at login

1. Tick *Start at login*, sign out and in: the tray app is running, with
   no console window.
2. Untick it: the entry is gone (Windows: the HKCU Run key; Linux:
   `~/.config/autostart`).

## 8. The window (UI process)

One window with a sidebar (Home, Library, Settings), in a process of its
own. What the tests can't see: the real desktop, real images, the
system's theme, the real tray and focus. The logic is tested with fakes
(`tests/test_window.py`: pages, the sidebar, closing with unsaved
settings, the tones; `test_home.py`, `test_settings.py`,
`test_ui_process.py`, `test_thumbnails.py`, `test_theme.py`,
`test_config_edit.py`). Compare the look with the mockups in
[design/](design/README.md) (dark mode; light mode only has to be usable).

### 8.1 Opening, pages and closing

1. Quit the app, run `wallpaper ui`: the app starts (tray icon appears),
   then the window opens on Home and fills with the live state. The
   sidebar's status says "Rotation active" with a green dot.
2. With the window open, tray *Open wallpaper_py*: the same window comes
   to the front on Home, also when minimised or behind another app. No
   second window. Tray *Library* and *Settings…*: the same window, on that
   page.
3. `wallpaper ui settings` (and `ui library`) while the window is open:
   it comes to the front on that page; no second window or process.
4. Close the window: the UI process exits; the tray app keeps running.
5. With no UI process, tray *Open wallpaper_py*: one starts and its window
   comes to the front; *Settings…* the same, on Settings.
6. With the window open, tray *Quit*: the window closes too.
7. With the window open, `wallpaper next`: Home updates (the row shows the
   new image).
8. Lock the session (or wait past `pause_when_idle`) with the window open:
   the sidebar says "Rotation paused: …" (grey dot), then "Rotation
   active" again.
9. Switch the system between light and dark mode (Settings >
   Personalisation > Colours) with the window open: the window follows
   (page, sidebar, its thin line, fields, icons, the selected item's
   accent); in light mode the monitor drawing and thumbnails are on a
   silver panel with dark text, in dark mode on a panel a little lighter
   than the page.
10. The look against [design/](design/README.md): the sidebar the page's
    colour with only a thin lighter line beside it (and under the title
    bar); the selected page with an accent bar, a faint accent fill and a
    bold label; hovering an item gives a faint fill. Tab and arrow keys
    reach every item, with a visible focus outline only from the keyboard.

### 8.2 Home

1. *Monitors (N)* matches the real arrangement: the PLX2783H left of the
   AW3225QF (it sits at -1920,0), sizes to scale, each showing its current
   image, with no outline; under each: its name, resolution (e.g.
   3840×2160) and playlist. A monitor in no playlist says "no playlist".
   Resize the window: the drawing rescales and nothing is cut off. Hover a
   playlist's row: a faint fill on the row and an accent outline on its
   monitors; hover a monitor: its playlist's row gets the fill; move away:
   both go.
2. Each playlist row: the thumbnail (letterboxed), name, monitors (a link
   icon for the mirrored group), the image's file name, then "dimensions ·
   file size · order, every 25m · next in 21m"; dimensions and size match
   the file (e.g. in Explorer's properties). The time left counts down
   (with seconds under 10 minutes), and the row shows the new image when
   it changes. Thin lines between rows, no boxes.
3. Each button, with its tooltip: *Previous* and *Next* change the
   wallpaper on the real monitors; *Favourite* turns the star accent and
   filled on a faint accent fill (and favourites.txt has the image), a
   second click clears it; *Remove* lists the image in to-remove.txt and
   moves on; *Hold* turns the pin accent, shows the *Held* chip and "…
   left once resumed", and a second click resumes. Toggling in the tray
   shows in the window too.
4. The manual playlist (ZeroMOD): a *Manual* chip, no hold button,
   "manual" instead of the time left.
5. An error shows in the row, not as a dialog: e.g. *Next* on a playlist
   whose monitors are all off.
6. Thumbnails never stall the window: with a playlist of large images
   (4K PNGs), press *Next* repeatedly and move / resize the window
   meanwhile; it stays responsive, and each row ends on its current image.
7. Switch a monitor off (e.g. OLED refresh) with the window open: within a
   few seconds it is drawn dimmed, marked "off", to the right of the
   others, at its usual size; the others are drawn where Windows now puts
   them (it may move them). Switched on, it returns to its real position.

### 8.3 Settings

The Settings page (`wallpaper ui settings`, tray *Settings…*). Back up
config.toml first (copy it next to itself).

1. **Round trip keeps your comments.** Open Settings, change nothing,
   close the window: config.toml is byte for byte the same (compare with
   the backup, e.g. `fc /b config.toml config.backup.toml`). Then change
   one interval and Save: a diff against the backup shows that one line
   changed, its comment still in the same column; every other comment and
   blank line as it was. Revert it the same way (or by hand).
2. **Assigning a monitor to another playlist.** Click the PLX2783H in the
   drawing and choose *fantasy*: the caption under it and both playlists'
   *Monitors* lines (and fantasy's summary row) change; Save. The tray's
   sections and Home show the PLX2783H under fantasy within a few seconds,
   and it gets fantasy's current image; the AW3225QF keeps konachan's. The
   file's `monitors` lines are the only ones changed. Move it back.
3. **A conflicting hand edit is detected.** With an unsaved change in the
   page, edit config.toml in an editor (add a comment) and save it there.
   Then Save in the page: it says the file changed on disk and writes
   nothing (your comment is still there); *Load the file again* shows the
   file as it is now.
4. **Unsaved changes stay and are asked about.** Change an interval, go to
   Home and Library and back: the change is still there (the title shows
   "*"). Close the window from Home: it switches to Settings and asks Save
   / Discard / Cancel: *Cancel* keeps the window open on Settings with the
   change; *Discard* closes without writing; *Save* writes, then closes,
   and the app reloads it (the tray shows the new interval; no
   `wallpaper reload` needed). With nothing changed, it closes without
   asking.
5. Validation shows next to the field, not as a dialog, and *Save* says why
   it is off (hover it, or the line beside it): an interval like `30`, a
   folder that doesn't exist, a name with a space, a playlist with no
   monitor. A closed playlist with a problem shows an alert in its summary
   row. With valid changes, the line says what changed ("Changes: interval
   of konachan. Not saved yet.").
6. **Rows and one open playlist.** Settings as rows without boxes: name
   and a one-line description on the left, the control on the right, all
   controls in one column; fields filled, without a border. One playlist
   is open; the others are a summary row ("ZeroMOD · sorted · manually ·
   fill") that opens on click (and the open one closes). *+ Add playlist*
   and *New playlist…* (from a monitor's menu) open the new one. *Browse…*,
   *Delete playlist*, *Choose…* (the monitors menu) are accent text.
7. **Changes.** Set a playlist to *Manually*: the duration field goes; Save:
   the file says `interval = "manual"` and the tray shows *(manual)*. Back
   to *Every*: the duration shows again.
8. Switch the ZeroMOD off (OLED refresh) with the page open: it is drawn
   dimmed and stays in fantasy. If Windows stops listing it, fantasy's
   *Monitors* line says "ZeroMOD (not connected)" and saving other changes
   keeps it in the file.
9. *About*: the version shows the git commit; each *Open folder* opens
   Explorer at the right place (the nearest existing folder for ones not
   created yet, e.g. the thumbnail cache); *Copy path* puts the path on the
   clipboard.
10. `wallpaper reload` after a hand edit: "Config reloaded" (plus any
    warnings); with a broken file, the error, and the tray's *Reload
    config* notifies the same error once.
11. With the app started on a custom config (`wallpaper -c other.toml
    tray`), plain `wallpaper next` and `wallpaper ui settings` (no `-c`)
    reach it, and the page edits other.toml.
12. **A rename keeps the playlist's state.** Hold konachan, note its
    current image, rename it to *anime* in Settings and Save: the same
    image stays on its monitors, it is still held, and *Previous* goes
    back through the same history. Rename it back by hand in the file and
    *Reload config*: the same again (the state goes by `id`, not the name).
13. **First start without a config.** Move config.toml away and start
    the app: no error; a notification says "No playlists yet", and
    clicking it opens the window on Settings. The tray menu starts with
    *No playlists yet: add one…* (opens Settings), the tooltip says "No
    playlists yet", Home shows *Add one in Settings* (switches to
    Settings), and `wallpaper status` warns "No playlists yet". In
    Settings, click a monitor, *New playlist…*, choose a folder, Save:
    the file is created (with the monitor's id and a random playlist id),
    the monitor starts rotating, and the tray item and Home's button are
    gone. Restart the app: no notification now. Put the old config back
    afterwards.
15. **A monitor moved to another port** (Windows). Move a monitor's cable
    to another port with the app running: Windows gives it a new id, so
    its playlist stops changing (the old id shows as off) and every other
    playlist keeps going. Assign the monitor again in Settings: it rotates
    again, with the playlist's history.
16. **Playlist ids.** Delete fantasy in Settings and add a new playlist
    in the same edit, on the ZeroMOD: Save writes it with a new random
    `id`, and it starts with an empty history (not fantasy's). By hand,
    delete the new playlist's `id` line and *Reload config*: the error
    gives a line to add. Restore the config afterwards.
14. **Only UTF-8 without BOM.** Save favourites.txt as "UTF-16 LE" (or
    "UTF-8 with BOM") in Notepad: within a tick the tray shows the error
    naming the file, and the file is left as it is. Save it as UTF-8 and
    *Reload config*: running again, the favourites as before.

## 9. Pick, hold and manual

The state database's schema changed (version 4: playlists are kept by
their text `id`): an older `state.sqlite3` is refused with a message naming the file to delete (in the tray, and from
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
