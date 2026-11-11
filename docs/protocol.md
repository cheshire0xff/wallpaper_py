# Socket protocol

How to talk to the running app: the CLI, the tray's UI process and any
other program use the same socket and the same messages. The models live in
[`wallpaper_py/protocol.py`](../wallpaper_py/protocol.py); the generated
[JSON Schema](protocol.schema.json) describes every message
(`uv run python scripts/protocol_schema.py` regenerates it, and a test fails
while it is out of date).

**Version 2.** It only ever adds commands, fields, error kinds and events.
Clients must ignore fields they don't know, and treat an unknown error kind
like `failed`. A breaking change bumps the version.

## Connecting

- A `QLocalServer`: a named pipe on Windows, a Unix socket elsewhere, only
  for the current user. Its name is `wallpaper_py-` plus the first 16 hex
  digits of the SHA-1 of the per-user data directory (normalized, absolute
  path); see `ipc.server_name`.
- One JSON object per line (UTF-8, `\n`), one request per connection: send
  one line, read one line, and the app closes the connection. The exception
  is `subscribe` (below).
- No app listening means it isn't running. The CLI then starts it
  (`pythonw -m wallpaper_py tray`) and retries for up to 10 s.
- A command runs on the app's service thread between ticks, so the reply
  can take a while (a 4K render, or a first scan of a big directory): the
  CLI waits up to 120 s.

## Requests

```json
{"v": 2, "request": {"command": "next", "target": "konachan"}, "config": "C:\\Users\\me\\AppData\\Local\\wallpaper_py\\config.toml"}
```

- `v`: the protocol version; another one is refused (`protocol`).
- `config`: the config file the client means. The app runs one config and
  refuses others (`wrong_config`).
- `request.command`: `status`, `next`, `prev`, `fav`, `unfav`, `remove`, or
  `subscribe`.
- `request.target` (all but `status` and `subscribe`): a playlist name, or a
  monitor the playlist drives (a name, a unique part of one, or the full
  id). It may be left out when there is only one playlist.

Requests are checked strictly: unknown fields are refused.

## Replies

Data only, no text: one model per command, tagged on `command`, or an
error. What a `change` holds: `playlist`, `image` (path), `reason`
(`scheduled`, `next`, `previous`, `restored`), `shown_on` (monitor names)
and `missed` (members that detached during the change; they get the image
when they come back).

| Command | Reply fields |
|---|---|
| `status` | `playlists` (each: `name`, `monitors`, `current`, `favourite`, `directory`, `order`, `interval` in seconds, `mode`, `next_in_seconds` of active time, `upcoming` paths), `paused` (why, or null), `warnings` (config that is valid but probably a mistake) |
| `next` | `playlist`, `change` |
| `prev` | `playlist`, `change` (null: nothing earlier in the history) |
| `fav` / `unfav` | `playlist`, `image`, `list_file` (favourites.txt), `changed` (false: it already was / wasn't a favourite) |
| `remove` | `playlist`, `image` (listed for removal, never deleted), `list_file` (to-remove.txt), `change` (null: no monitor attached; it moves on later) |

A monitor is `{"selector", "name", "attached"}`: the selector as written in
the config, the monitor's name (the selector if it isn't listed), and
whether it is attached (`null`: not listed right now).

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
| `failed` | any other expected failure; the message says what |
| `internal` | a bug; the app logged the details |
| `unreachable` | never sent by the app: a client's own "no app, or it did not answer" |

The message is meant for people; programs decide by `kind`.

## Subscribing

```json
{"v": 2, "request": {"command": "subscribe"}, "config": "C:\\Users\\me\\AppData\\Local\\wallpaper_py\\config.toml"}
```

The connection stays open and the app writes one event per line, tagged on
`event`, until it quits (the connection then closes). The first event is
always the current `overview_changed`. A refused subscription gets one
error line instead, then the connection closes.

| Event | Fields | When |
|---|---|---|
| `overview_changed` | `overview`: `playlists` (each: `name`, `monitors`, `current`, `favourite`), `paused`, `error` (the app is up but its service isn't: e.g. a broken config) | on subscribing, then whenever it changes |
| `changed` | `change` | a wallpaper changed, by a tick or a command |

```json
{"event": "overview_changed", "overview": {"playlists": [{"name": "fantasy", "monitors": [{"selector": "ZeroMOD", "name": "ZeroMOD", "attached": false}], "current": "D:\\pictures\\Fantasy\\7.png", "favourite": false}], "paused": null, "error": null}}
{"event": "changed", "change": {"playlist": "konachan", "image": "D:\\pictures\\KONACHAN\\123.jpg", "reason": "next", "shown_on": ["AW3225QF", "PLX2783H"], "missed": []}}
```

Read the events promptly: the app drops a subscriber that falls more than
4 MB behind. Send commands on separate connections.

Not on the socket (by design): thumbnails and image data (read the files by
path), and history or library listings (read the state database read-only;
see [plan.md](plan.md), Phase 5).
