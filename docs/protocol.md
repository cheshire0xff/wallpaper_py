# Socket protocol

How to talk to the running daemon: the CLI, the tray's window process and any
other program use the same socket and the same messages. The models live in
[`wallpaper_py/protocol.py`](../wallpaper_py/protocol.py); the generated
[JSON Schema](protocol.schema.json) describes every message
(`uv run python scripts/protocol_schema.py` regenerates it, and a test fails
while it is out of date).

**Version 3.** It only ever adds commands, fields, error kinds and events.
Clients must ignore fields they don't know, and treat an unknown error kind
like `failed`. A breaking change bumps the version. (Until the first
release it may still change in place.)

## Connecting

- A `QLocalServer`: a named pipe on Windows, a Unix socket elsewhere, only
  for the current user. Its name is `wallpaper_py-` plus the first 16 hex
  digits of the SHA-1 of the per-user data directory (normalized, absolute
  path); see `ipc.server_name`.
- One JSON object per line (UTF-8, `\n`), one request per connection: send
  one line, read one line, and the daemon closes the connection. The exception
  is `subscribe` (below).
- No daemon listening means it isn't running. The CLI then fails
  (`wallpaper tray` starts it); the window process starts the daemon
  (`pythonw -m wallpaper_py tray`) and retries for up to 10 s.
- A command runs on the daemon's service thread between ticks, so the reply
  can take a while (a 4K render, or a first scan of a big directory): the
  CLI waits up to 120 s.

## Requests

```json
{"v": 3, "request": {"command": "next", "target": "konachan"}}
```

- `v`: the protocol version; another one is refused (`protocol`).
- `request.command`: `status`, `next`, `prev`, `hold`, `resume`, `fav`,
  `unfav`, `remove`, `unremove`, `pick`, `upcoming`, `stats`,
  `reset_cycle`, `settings`, `save_settings`; or the window's
  `show_window` and `subscribe` ([below](#the-window)).
- `request.target` (every command but `status`, `unremove`, `settings` and
  `save_settings`; those refuse it, and so do the window's two): a playlist's `id` (what the tray and
  the windows send: a rename can't misdirect it) or name (what people
  type), or a monitor the playlist drives (a name, a unique part of one,
  or the full id). It may be left out when there is only one playlist;
  for `stats`, leaving it out means every playlist.
- `request.image`, an absolute path (the daemon doesn't know the client's
  directory), in any directory:
  - `pick` (required): the image to show.
  - `fav`, `unfav`, `remove`, `unremove` (required for `unremove`): the
    image to put on the list or take off it, instead of the target
    playlist's current one. It need not exist (a list may name a file
    that is gone). Send an `image` or a `target`, not both.
- `request.limit` (`upcoming` only, 1 to 1000, default 100): how many
  images.

```json
{"v": 3, "request": {"command": "pick", "target": "fantasy", "image": "D:\\pictures\\other\\7.png"}}
```

- `request.rescan` (`stats` only, default `false`): scan the folders
  again first (the daemon's scan is up to 5 min old).
- `request.settings` and `request.revision` (`save_settings` only, and
  required there): the whole edited settings (below), and the `revision`
  of the ones the edit was made on (from `settings`).

Requests are checked strictly: unknown fields are refused.

## Replies

Data only, no text: one model per command, tagged on `command`, or an
error. What a `change` holds: `playlist_id`, `playlist` (its name), `image`
(path), `reason` (`scheduled`, `next`, `previous`, `picked`, `restored`),
`shown_on` (monitor names) and `missed` (members that detached during the
change; they get the image when they come back).

| Command | Reply fields |
|---|---|
| `status` | `playlists` (each: `id`, `name`, `monitors`, `current`, `favourite`, `held`, `manual`, `directory`, `order`, `interval` in seconds (null: manual), `next_in_seconds` of active time (null: manual; while held, what is left once resumed), `upcoming` paths), `paused` (why, or null), `warnings` (settings that are valid but probably a mistake) |
| `next` | `playlist`, `change` |
| `prev` | `playlist`, `change` (null: nothing earlier in the history) |
| `pick` | `playlist`, `change` (reason `picked`) |
| `fav` / `unfav` / `unremove` | `playlist` (the one whose current image it is; null when the request named an `image`), `image`, `list_file` (favourites.txt; to-remove.txt for `unremove`), `changed` (false: it already was / wasn't on the list) |
| `remove` | `playlist` (as for `fav`), `image` (listed for removal, never deleted), `list_file` (to-remove.txt), `changed` (false: it was listed already), `change` (the next image shown; null: no monitor attached, it moves on later, or the request named an `image`) |
| `upcoming` | `playlist`, `images` (paths, the next one first) |
| `hold` / `resume` | `playlist`, `held` (now: true after `hold`, false after `resume`), `changed` (false: it already was / wasn't held) |
| `stats` | `playlists` (each: `name`, `directory`, `order`, `scan_age` in seconds, `images`, `seen`, `unseen`, `skipped`, `favourites_here`, `to_remove_here`), `favourites` and `to_remove` (each: `file`, `listed`, `available`) |
| `reset_cycle` | `playlist`, `unseen` (now: every image but the skipped ones) |
| `settings` | `settings` (below), `revision` (an integer; send it back with the edit) |
| `save_settings` | `revision` (of the saved settings: the next edit goes on this one), `warnings` (settings that are valid but probably a mistake) |
| `show_window` | `shown` (true: the window process that has the window was asked to show the page; false: none has it) |

`pick` shows the image now, as if `next` had chosen it: it goes into the
history right after the current entry, so `prev` goes back to what was
shown before, and the interval starts again. A held playlist doesn't rotate
by itself, but `next`, `prev` and `pick` still work; a manual one
(no interval: `interval` null) never rotates, so `hold` / `resume`
on it are an error (`failed`).

`remove` of a playlist's current image shows the next one at once. With
an `image` it only lists it, as an edit by hand would: a playlist showing
it moves on at its next tick (within 5 s).

`upcoming` lists the images in the order they come: forward in the history
first (after `prev`), then the rest of the shuffle cycle (not the next
cycle: it isn't shuffled yet) or the sorted walk, which wraps around.
Images listed to remove, gone or unreadable are left out. While
to-remove.txt has never been read it answers `failed`.

`stats` counts what each playlist picks up, from the daemon's scan of its
folder, its shuffle deck and the lists; it changes nothing. `images` =
`seen` + `unseen` + `skipped`, always: `skipped` are the images listed to
remove or that failed to load; `seen`, of the rest, the ones drawn in this
shuffle cycle (picked ones count); `unseen` the others, new files too
(they are shuffled in when the next image is drawn). A sorted playlist has
no cycle: `seen` are the images up to and including where its walk is (the
newest image it showed from the folder), in name order, `unseen` the ones
after. Of the lists: `listed` entries in the file, `available` the ones
whose file exists (one `stat` each), and per playlist `favourites_here` /
`to_remove_here`, the entries among its images.

`reset_cycle` starts a shuffle playlist's cycle over from the current scan:
every image unseen again. The image on screen stays, and isn't first in
the new cycle. A sorted playlist has no cycle (`failed`); an empty folder
(maybe not mounted) keeps it (`no_images`). It can't be undone: the CLI
asks first.

## Settings

The settings live in the daemon's state database; only the daemon writes them.
`settings` reads them, with their `revision`; `save_settings` replaces
them as a whole and uses them at once: every monitor whose playlist or
look changed shows its image rendered again before the reply, and the new
overview goes to subscribers first. It is refused, and nothing is saved,
when the stored settings are no longer the `revision` the edit was made on
(`stale_settings`: read them again), or when a value can't be used
(`invalid_settings`, the message says which: a blank or repeated name, an
interval under 10 s, a folder that is new or changed and doesn't exist, a
monitor on a playlist that isn't there). Deleting a playlist deletes its
state (history, shuffle deck, hold) in the same save; a renamed one keeps
it (state goes by the playlist's `id`). A playlist given another folder
starts a new shuffle cycle and shows an image from it at once (reason
`scheduled`; also when held or manual); `prev` still goes back to the old
one.

```json
{"playlists": [{"id": "3f0c9d6e...", "name": "konachan", "directory": "D:\\pictures\\KONACHAN", "order": "shuffle", "interval": 1800.0}], "monitors": [{"id": "\\\\?\\DISPLAY#DELA246#...", "playlist": "3f0c9d6e...", "look": {"mode": "fit", "background": "blur", "blur": 0.005, "darken": 0.25}}], "pause_when_locked": true, "pause_when_idle": 300.0, "favourites_file": null, "to_remove_file": null}
```

- `playlists`, in the order they are listed: `id` (generated by the
  client for a new one, never reused), `name` (unique, case-insensitively),
  `directory` (absolute), `order` (`shuffle` or `sorted`), `interval` in
  seconds of active time (at least 10; null: manual).
- `monitors`, by `id` only (never by name: two monitors of one model share
  one): `playlist` (an `id` from `playlists`; null: none; several monitors
  on one playlist show the same image) and `look` (`mode`: `fill`, `fit`
  or `stretch`; `background`, which fills Fit's bars: a colour as
  `#rrggbb` in lower case, or `average` / `dominant` / `edges` / `blur`
  of the image; default `#000000`; for `blur`: `blur`, the radius as a
  part of the screen's longer side, 0.001 to 0.02, default 0.005, and
  `darken`, 0 (none) to 0.8, default 0.25; numbers, kept with any
  background). A monitor without an entry has no playlist and the default
  look; leaving one out forgets it.
- `pause_when_locked`, `pause_when_idle` (seconds without input; null:
  never), `favourites_file` and `to_remove_file` (absolute; null: in the
  app's data directory).

A list file (favourites, to-remove) that can't be used (e.g. saved with a
byte order mark) stops `PlaylistService`, whether at start, on a tick or in a
command: every command then answers `config_error`, and the overview's
`error` says why, until the file is fixed (the daemon tries again on every
tick).

While to-remove.txt has never been read (another program holds it open
since the daemon started), no image is chosen: ticks wait, and `next`,
`prev` and `pick` answer `failed` until it can be read. Once read, the last
list read stands in while the file is busy.

A playlist's monitor is `{"id", "name", "attached", "look"}`: its id (the
settings hold monitors by id; spelled as in the overview's `monitors`,
below, when listed, else as the settings spell it; ids compare
case-insensitively), the monitor's name (the id if it isn't listed),
whether it is attached (`null`: not listed right now), and its look.

```json
{"command": "next", "playlist": "konachan", "change": {"playlist_id": "3f0c9d6e...", "playlist": "konachan", "image": "D:\\pictures\\KONACHAN\\123.jpg", "reason": "next", "shown_on": ["AW3225QF", "PLX2783H"], "missed": []}}
```

## Errors

```json
{"error": {"kind": "not_attached", "message": "None of fantasy's monitors is attached (ZeroMOD)"}}
```

| Kind | Meaning |
|---|---|
| `protocol` | malformed request, or another protocol version |
| `config_error` | a list file can't be used; the daemon waits for a fix |
| `unknown_target` | no such playlist or monitor, the monitor is in no playlist, or several playlists and none named |
| `not_attached` | none of the playlist's monitors is attached |
| `no_images` | nothing to show in the playlist's directory |
| `nothing_shown` | the playlist has no current image yet (`fav`, `unfav`, `remove` without an `image`) |
| `bad_image` | `pick`: the image is missing, not a file, or not one Pillow can open |
| `invalid_settings` | `save_settings`: a value can't be used; nothing was saved |
| `stale_settings` | `save_settings`: the settings changed since they were read; nothing was saved |
| `failed` | any other expected failure; the message says what |
| `internal` | a bug; the daemon logged the details |
| `unreachable` | never sent by the daemon: a client's own "no daemon, or it did not answer" |

The message is meant for people; programs decide by `kind`.

## Subscribing

```json
{"v": 3, "request": {"command": "subscribe"}}
```

The connection stays open and the daemon writes one event per line, tagged on
`event`, until it quits (the connection then closes). The first event is
always the current `overview_changed`. A refused subscription gets one
line instead, then the connection closes: an error, or, for a second
window, `show_window`'s reply ([below](#the-window)).

| Event | Fields | When |
|---|---|---|
| `overview_changed` | `overview`: `playlists` (each: `id`, `name`, `directory`, `monitors`, `current`, `favourite` (null: favourites.txt can't be read yet, so it isn't known), `held`, `manual`), `monitors` (every monitor the desktop lists, also those in no playlist: `id`, `name`, `geometry`, `look`), `paused`, `error` (the daemon is up but `PlaylistService` isn't: e.g. a list file it can't use), `favourites_file` and `to_remove_file` (the lists' paths; null while `error`) | on subscribing, then whenever it changes (also after `hold` / `resume` and `save_settings`, and when a monitor is switched off or moves) |
| `changed` | `change` | a wallpaper changed, by a tick or a command |
| `marks_changed` | none | favourites.txt or to-remove.txt changed (their mtime or size), by a command, by hand (seen on the next tick, within 5 s), a settings save naming other files, or the service running again after a list it couldn't use was fixed: read them again |
| `show_page` | `page` (`home`, `library` or `settings`) | only to the window ([below](#the-window)): come to the front on that page |

```json
{"event": "overview_changed", "overview": {"playlists": [{"id": "9b1e4f2a...", "name": "fantasy", "directory": "D:\\pictures\\Fantasy", "monitors": [{"id": "\\\\?\\DISPLAY#AUS27B1#...", "name": "ZeroMOD", "attached": false}], "current": "D:\\pictures\\Fantasy\\7.png", "favourite": false, "held": false, "manual": true}], "monitors": [{"id": "\\\\?\\DISPLAY#DELA246#...", "name": "AW3225QF", "geometry": {"x": 0, "y": 0, "width": 3840, "height": 2160}}, {"id": "\\\\?\\DISPLAY#AUS27B1#...", "name": "ZeroMOD", "geometry": null}], "paused": null, "error": null, "favourites_file": "C:\\Users\\me\\AppData\\Local\\wallpaper_py\\favourites.txt", "to_remove_file": "C:\\Users\\me\\AppData\\Local\\wallpaper_py\\to-remove.txt"}}
{"event": "changed", "change": {"playlist_id": "3f0c9d6e...", "playlist": "konachan", "image": "D:\\pictures\\KONACHAN\\123.jpg", "reason": "next", "shown_on": ["AW3225QF", "PLX2783H"], "missed": []}}
```

A monitor in the overview's `monitors` has a `geometry` in desktop pixels
(`x`, `y`, `width`, `height`; `x` and `y` are negative for a monitor left
of or above the main one) or `null` while it is detached. Address monitors
by `id`, never by their order or position: both change when a monitor is
switched off. The overview is kept small on purpose (it goes to every
subscriber on every change), so it has no schedule (order, interval, time
left, which changes with every tick): ask `status` for that.

Read the events promptly: the daemon drops a subscriber that falls more than
4 MB behind. Send commands on separate connections.

## The window

At most one process has the window (`wallpaper ui`), and the daemon knows
which: it subscribes as the window. The socket answers these two itself,
not `PlaylistService`, so they work while it is stopped (the window must
open to show why).

```json
{"v": 3, "request": {"command": "subscribe", "window": "library"}}
{"v": 3, "request": {"command": "show_window", "page": "settings"}}
```

- `subscribe` with `window` (a page: `home`, `library` or `settings`): "I
  am the window, on this page". The first such subscriber is the window,
  until its connection closes; it gets the events as any subscriber, and
  `show_page` too. A later one is refused: its page goes to the window as
  `show_page`, and it gets one line, `{"command": "show_window", "shown":
  true}`, instead of the events, and the connection closes. Two starting at
  once: the daemon takes their subscriptions one at a time, and the first
  wins.
- `show_window` with `page`: `show_page` to the window. `shown: false` if
  there is none; starting one is then the asker's job (the tray starts
  `wallpaper ui <page>`).
- The window closes: it ends its subscription at once, so from then on
  there is none. Commands it sent finish on their own connections. The
  daemon sees the close on its next pass through its event loop: a
  `show_window` (or window subscription) that arrives in between, while a
  tick or command runs, goes to the closed window and shows nothing. A
  second click works. Accepted: it takes a click within a few hundred
  milliseconds of closing, and catching it would mean polling the socket on
  the service thread.

Not on the socket (by design): thumbnails and image data (read the files by
path), the lists (read favourites.txt / to-remove.txt, again on
`marks_changed`), and history or folder listings (read the state database
read-only, as `core.store.read_history` does; scan the folder, as
`core.library.scan_files` does). Counts are (`stats`), and so is the order
to come (`upcoming`): it depends on the shuffle deck and the history.
