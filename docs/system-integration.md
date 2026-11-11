# How wallpaper_py fits into your system

What wallpaper_py adds to your computer, where it keeps things, and how to
remove all of it. Nothing here needs admin rights: everything is per user.

## At a glance

| What | Windows | Linux |
|---|---|---|
| Start at login | a value in the registry's per-user Run key | a `.desktop` file in `~/.config/autostart` |
| Your config | `%LOCALAPPDATA%\wallpaper_py\config.toml` | `~/.config/wallpaper_py/config.toml` |
| State (history, shuffle order) | `%LOCALAPPDATA%\wallpaper_py\state.sqlite3` | `~/.local/share/wallpaper_py/state.sqlite3` |
| Favourites, to-remove lists | `favourites.txt`, `to-remove.txt` next to the state (or where the config says) | same |
| Logs | `%LOCALAPPDATA%\wallpaper_py\Logs\` | `~/.local/state/wallpaper_py/log/` |
| Rendered wallpapers (cache) | `%LOCALAPPDATA%\wallpaper_py\Cache\rendered\` | `~/.cache/wallpaper_py/rendered/` |
| How its processes talk | named pipes `\\.\pipe\wallpaper_py-…` | socket files `wallpaper_py-…` in the temp folder (usually `/tmp`) |

Linux support is planned (KDE Plasma, Phase 6): start at login and the
paths above are already in place, but the app can't set wallpapers on
Linux yet.

## Start at login

Off until you tick **Start at login** in the tray menu. Ticking it writes
one entry; unticking removes it. Nothing else is installed: no service, no
scheduled task, no driver.

**What the entry runs**: the tray app, with the same Python it runs with
now, without a console window:

```
C:\path\to\wallpaper_py\.venv\Scripts\pythonw.exe -m wallpaper_py tray
```

plus `--config <path>` if you started the app with a config file other
than the default. Only the tray app starts at login; the windows (layout,
library, settings) start when you open them.

**Windows**: the entry is the value `wallpaper_py` under

```
HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run
```

the standard place for per-user programs that start at login. It shows in
Task Manager's *Startup apps* tab. If you disable it *there*, Windows keeps
the entry but skips it, and the tray menu still shows the box ticked (it
only sees that the entry exists); untick and tick it again in the tray to
be sure of the state.

**Linux**: the entry is the file `~/.config/autostart/wallpaper_py.desktop`
(or under `$XDG_CONFIG_HOME` if you set it), the freedesktop.org autostart
standard that KDE Plasma, GNOME and most desktops honour. KDE lists it in
*System Settings → Autostart*.

**If you move things**: the entry names the exact Python path at the time
you ticked the box. If you move or delete the folder wallpaper_py lives in
(or its `.venv`), the entry points nowhere and nothing starts at login.
Untick and tick *Start at login* again after moving it. Recreating the
environment in the same place (`uv sync`) is fine.

## The processes and how they talk

- **The app** (`wallpaper tray`, or `wallpaper run` in a terminal): the
  one process that changes wallpapers. It shows the tray icon and listens
  for commands. Only one runs per user; a second one exits at once (it
  holds `app.lock` in the state folder).
- **The windows** (`wallpaper ui …`): a separate process, started when you
  open a window and gone when you close the last one, so it uses no memory
  while closed. At most one runs (`ui.lock`).
- **The CLI** (`wallpaper next`, `status`, …): sends one command to the
  app and prints the answer. If the app isn't running, it starts it first
  (the same command as start at login), so a CLI command may leave the tray
  icon behind: that is the app, now running.

They talk over a local socket: a named pipe on Windows, a socket file in
the temp folder on Linux. Only your user account can connect to it; it is not a
network port, and nothing listens on the network. The name ends in a code
derived from your data folder, so two users on one machine never meet.
The messages are documented in [protocol.md](protocol.md).

## What it changes on your desktop

- **The wallpaper of each monitor**, through Windows' own per-monitor
  wallpaper interface. An image that needs fitting (`mode = "fill"` etc.)
  is first rendered to the monitor's exact size and saved in the cache
  folder; Windows is then pointed at that file. Each monitor uses two cache
  files in turn, so the cache never grows.
- **Windows' wallpaper position**, once: Windows applies one fit mode to
  all monitors, so wallpaper_py sets it to *fill* if it was *tile* or
  *span* (modes some other wallpaper tools use). With the images rendered
  to size, *fill* shows them exactly.
- Nothing else: it doesn't change the lock screen, themes or colours.

It *reads* (never changes): the list of monitors and their positions,
whether the session is locked (to pause rotation), and how long since your
last keyboard or mouse input (to pause while you're away).

## Removing everything

1. In the tray menu, untick **Start at login**, then **Quit**.
2. Delete the folders from the table above: on Windows
   `%LOCALAPPDATA%\wallpaper_py` holds all of them; on Linux
   `~/.config/wallpaper_py`, `~/.local/share/wallpaper_py`,
   `~/.local/state/wallpaper_py` and `~/.cache/wallpaper_py`. Keep
   `config.toml`, `favourites.txt` and `to-remove.txt` if you want them
   later.
3. Delete the wallpaper_py folder itself (with its `.venv`).

Your wallpapers stay as they were last set (the last rendered images are
in the cache folder you deleted, so Windows may fall back to a plain
colour after the next sign-in; pick a wallpaper in Windows' settings if
so).
