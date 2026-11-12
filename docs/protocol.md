# Socket protocol

How to talk to the running app: the CLI, the tray's UI process and any
other program use the same socket and the same messages. The models live in
[`wallpaper_py/protocol.py`](../wallpaper_py/protocol.py); the generated
[JSON Schema](protocol.schema.json) describes every message
(`uv run python scripts/protocol_schema.py` regenerates it, and a test fails
while it is out of date).

**Version 2.** It only ever adds commands, fields, error kinds and events.
Clients must ignore fields they don't know, and treat an unknown error kind
like `failed`. A breaking change bumps the version. (Until the first
release it may still change in place.)

## Connecting

- A `QLocalServer`: a named pipe on Windows, a Unix socket elsewhere, only
  for the current user. Its name is `wallpaper_py-` plus the first 16 hex
  digits of the SHA-1 of the per-user data directory (normalized, absolute
  path); see `ipc.server_name`.
- One JSON object per line (UTF-8, `\n`), one request per connection: send
  one line, read one line, and the app closes the connection. The exception
  is `subscribe` (below).
- No app listening means it isn't running. The CLI then runs the command
  itself (holding the app's lock) instead; the UI process starts the app
  (`pythonw -m wallpaper_py tray`) and retries for up to 10 s.
- A command runs on the app's service thread between ticks, so the reply
  can take a while (a 4K render, or a first scan of a big directory): the
  CLI waits up to 120 s.

## Requests

```json
{"v": 2, "request": {"command": "next", "target": "konachan"}}
```

- `v`: the protocol version; another one is refused (`protocol`).
- `config` (optional): the config file the client means, if the user named
  one (`--config`). The app runs one config and refuses another
  (`wrong_config`). Left out, the client means whatever config the app
  runs; the overview names it (`config`, below).
- `request.command`: `status`, `next`, `prev`, `fav`, `unfav`, `remove`,
  `hold`, `resume`, `pick`, `reload`, or `subscribe`.
- `request.target` (all but `status`, `reload` and `subscribe`): a playlist name, or a
  monitor the playlist drives (a name, a unique part of one, or the full
  id). It may be left out when there is only one playlist.
- `request.image` (`pick` only, and required there): the image to show, an
  absolute path (the app doesn't know the client's directory). It may be in
  any directory.

```json
{"v": 2, "request": {"command": "pick", "target": "fantasy", "image": "D:\\pictures\\other\\7.png"}, "config": "C:\\Users\\me\\AppData\\Local\\wallpaper_py\\config.toml"}
```

Requests are checked strictly: unknown fields are refused.

## Replies

Data only, no text: one model per command, tagged on `command`, or an
error. What a `change` holds: `playlist`, `image` (path), `reason`
(`scheduled`, `next`, `previous`, `picked`, `restored`), `shown_on`
(monitor names) and `missed` (members that detached during the change; they
get the image when they come back).

| Command | Reply fields |
|---|---|
| `status` | `playlists` (each: `name`, `monitors`, `current`, `favourite`, `held`, `manual`, `directory`, `order`, `interval` in seconds (null: manual), `mode`, `next_in_seconds` of active time (null: manual; while held, what is left once resumed), `upcoming` paths), `paused` (why, or null), `warnings` (config that is valid but probably a mistake) |
| `next` | `playlist`, `change` |
| `prev` | `playlist`, `change` (null: nothing earlier in the history) |
| `pick` | `playlist`, `change` (reason `picked`) |
| `fav` / `unfav` | `playlist`, `image`, `list_file` (favourites.txt), `changed` (false: it already was / wasn't a favourite) |
| `remove` | `playlist`, `image` (listed for removal, never deleted), `list_file` (to-remove.txt), `change` (null: no monitor attached; it moves on later) |
| `hold` / `resume` | `playlist`, `held` (now), `changed` (false: it already was / wasn't held) |
| `reload` | `warnings` (config that is valid but probably a mistake); a config the app can't use is the error `config_error` instead |

`pick` shows the image now, as if `next` had chosen it: it goes into the
history right after the current entry, so `prev` goes back to what was
shown before, and the interval starts again. A held playlist doesn't rotate
by itself, but `next`, `prev` and `pick` still work; a manual one
(`interval = "manual"` in the config) never rotates, so `hold` / `resume`
on it are an error (`failed`).

`reload` reads the config file again and restarts the playlists from it
(history, shuffle decks and holds are kept: they live in the state
database). The new overview goes to subscribers before the reply. Send it
after editing the file: the tray's *Reload config*, `wallpaper reload` and
the settings window all do. While the config is broken, the other commands
answer `config_error` until a `reload` succeeds.

A playlist's monitor is `{"selector", "name", "attached", "id"}`: the
selector as written in the config, the monitor's name (the selector if it
isn't listed), whether it is attached (`null`: not listed right now), and
the listed monitor's `id` (`null` if not listed), which matches an entry of
the overview's `monitors` (below).

```json
{"command": "next", "playlist": "konachan", "change": {"playlist": "konachan", "image": "D:\\pictures\\KONACHAN\\123.jpg", "reason": "next", "shown_on": ["AW3225QF", "PLX2783H"], "missed": []}}
```

## Errors

```json
{"error": {"kind": "not_attached", "message": "None of fantasy's monitors is attached (ZeroMOD)"}}
```

| Kind | Meaning |
|---|---|
| `protocol` | malformed request, or another protocol version |
| `wrong_config` | the app runs another config file |
| `config_error` | the config is missing or invalid; the app waits for a fix (*Reload config*) |
| `unknown_target` | no such playlist or monitor, the monitor is in no playlist, or several playlists and none named |
| `not_attached` | none of the playlist's monitors is attached |
| `no_images` | nothing to show in the playlist's directory |
| `nothing_shown` | the playlist has no current image yet (`fav`, `unfav`, `remove`) |
| `bad_image` | `pick`: the image is missing, not a file, or not one Pillow can open |
| `failed` | any other expected failure; the message says what |
| `internal` | a bug; the app logged the details |
| `unreachable` | never sent by the app: a client's own "no app, or it did not answer" |

The message is meant for people; programs decide by `kind`.

## Subscribing

```json
{"v": 2, "request": {"command": "subscribe"}}
```

The connection stays open and the app writes one event per line, tagged on
`event`, until it quits (the connection then closes). The first event is
always the current `overview_changed`. A refused subscription gets one
error line instead, then the connection closes.

| Event | Fields | When |
|---|---|---|
| `overview_changed` | `overview`: `playlists` (each: `name`, `monitors`, `current`, `favourite`, `held`, `manual`), `monitors` (every monitor the desktop lists, also those in no playlist: `id`, `name`, `geometry`), `paused`, `error` (the app is up but its service isn't: e.g. a broken config), `config` (the config file the app runs) | on subscribing, then whenever it changes (also after `hold` / `resume` and `reload`, and when a monitor is switched off or moves) |
| `changed` | `change` | a wallpaper changed, by a tick or a command |

```json
{"event": "overview_changed", "overview": {"playlists": [{"name": "fantasy", "monitors": [{"selector": "ZeroMOD", "name": "ZeroMOD", "attached": false, "id": "\\\\?\\DISPLAY#AUS27B1#..."}], "current": "D:\\pictures\\Fantasy\\7.png", "favourite": false, "held": false, "manual": true}], "monitors": [{"id": "\\\\?\\DISPLAY#DELA246#...", "name": "AW3225QF", "geometry": {"x": 0, "y": 0, "width": 3840, "height": 2160}}, {"id": "\\\\?\\DISPLAY#AUS27B1#...", "name": "ZeroMOD", "geometry": null}], "paused": null, "error": null, "config": "C:\\Users\\me\\AppData\\Local\\wallpaper_py\\config.toml"}}
{"event": "changed", "change": {"playlist": "konachan", "image": "D:\\pictures\\KONACHAN\\123.jpg", "reason": "next", "shown_on": ["AW3225QF", "PLX2783H"], "missed": []}}
```

A monitor in the overview's `monitors` has a `geometry` in desktop pixels
(`x`, `y`, `width`, `height`; `x` and `y` are negative for a monitor left
of or above the main one) or `null` while it is detached. Address monitors
by `id`, never by their order or position: both change when a monitor is
switched off. The overview is kept small on purpose (it goes to every
subscriber on every change), so it has no schedule (order, interval, time
left, which changes with every tick): ask `status` for that.

Read the events promptly: the app drops a subscriber that falls more than
4 MB behind. Send commands on separate connections.

Not on the socket (by design): thumbnails and image data (read the files by
path), and history or library listings (read the state database read-only;
see [plan.md](plan.md), Phase 5).
