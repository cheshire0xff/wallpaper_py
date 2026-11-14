# Desktop checks

Tests never touch the real desktop: they use fakes, and the logic behind
every step here is tested with them. These steps check only what a real
desktop shows: the image really on the right monitor, monitors coming and
going, the session and login, the tray, the window on real screens. About
15 minutes. Run them on each platform before a release, after a change to
a backend, and for the KDE backend (Phase 6) on Arch and on Kubuntu.

Keep the list short: a step only for something no test can see, and only
if no step here shows it yet (extend that one instead). How a new feature
works is tried once when it is built, from the session's report, not added
here. The old, long list: `git show 2aabb5e:docs/desktop-checks.md`.

The app logs to `wallpaper.log`, the window to `ui.log` (both in the
per-user log directory: `%LOCALAPPDATA%\wallpaper_py\Logs` on Windows).
`--verbose` on a CLI command says whether the running app answered it.

| | Windows 11 | KDE (Arch) | KDE (Kubuntu) |
|---|---|---|---|
| Last run (and the steps that failed) | not yet | not yet | not yet |

## Wallpapers

1. **The right monitor.** `wallpaper list --all` lists every monitor (a
   switched-off one as detached). `wallpaper set IMAGE --monitor <part of
   a name>` changes only that monitor.
2. **Rotation.** With the app running, each playlist changes after its
   interval; a mirrored group shows the same image on all its monitors,
   each in its own look (put one on Fill, the other on Fit). The tray's
   *Next* and *Previous* change the right monitors.
3. **Looks match the previews.** A monitor on Fit with *Edges*: the bars
   on the screen have the colour of Home's preview (compare a screenshot of
   each); the same part of the image shows. Changing a monitor's mode or
   background in Settings and saving shows on the screen at once.
4. **The system's own setting (Windows).** Personalisation > Background >
   *Choose a fit* > Tile, then `wallpaper next`: back to Fill, and every
   monitor shows its image as its look says.
5. **The desktop restarted.** With the app running, restart Windows
   Explorer (Task Manager), or `plasmashell --replace` on KDE: at most one
   tick logs an error, then `wallpaper next` works and rotation goes on.

## Monitors coming and going

6. **Switched off.** With the app and the window running, switch the main
   monitor off (e.g. the OLED's panel refresh): its playlist waits, the
   other monitors keep their images, a mirrored group goes on with its
   other members, and Home shows it as off. Switch it on: within a few
   seconds it gets its playlist's current image.
7. **Resolution changed.** Change a monitor's resolution: within a few
   seconds its image is rendered again for the new size. Change it back.

## Session and login

8. **Lock and idle.** Lock the session: within 5 s the log says `paused`,
   the tray icon turns grey and the window's sidebar says paused; unlock:
   `resumed`. The same after idling past the idle pause.
9. **Restart the PC** with *Start at login* ticked: the tray app runs, with
   no console window; every monitor has its current image, and shuffle goes
   on where it was. Untick it: the entry is gone (Windows: the HKCU Run key;
   Linux: `~/.config/autostart`).

## Tray, window and CLI

10. **Tray.** The app icon in the tray (and the window's title bar and
    taskbar). Left click opens the window on Home, right click the menu:
    each playlist with its monitors and current image (a click opens it);
    *Next*, *Previous*, *Favourite*, *Remove*, *Hold* work. Save
    favourites.txt as UTF-8 with BOM in Notepad: within a tick a red badge
    and a notification name the file; saved as UTF-8 again, both go.
11. **One window, to the front.** With the window behind another app or
    minimised, tray *Open wallpaper_py*, *Settings…* and `wallpaper ui
    library`: the same window comes to the front on that page, never a
    second one. Closing it ends the UI process (Task Manager); tray *Quit*
    closes the window too.
12. **CLI without the app.** Quit the app: `wallpaper --verbose next` runs
    the command itself, the wallpaper changes, and no process is left
    behind.
13. **The look.** At 150 % display scale, dark mode: Home and Settings
    look like the mockups in [design/](design/README.md), and the previews
    and thumbnails are sharp. Switch the system to light mode with the
    window open: it follows. Tab reaches every control, with a visible
    focus outline.
14. **The system's colour dialog.** A monitor on Fit, *Colour*, click the
    swatch: the system's colour dialog opens, and dragging in it moves the
    preview's bars live (on KDE, note here if they change only after OK).
