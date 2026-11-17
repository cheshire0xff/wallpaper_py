# App wiring

Which process and thread each object lives on, and diagrams of how they
talk. What an object does is in its module's docstring, why it is built
that way in [architecture.md](architecture.md), the socket's messages in
[protocol.md](protocol.md); the connections themselves are the
`connect()` calls in the code. `tests/test_docs.py` checks that every
`Class.member` named here still exists.

Qt basics used below: `signal.emit(...)` calls every slot connected to the
signal. Within one thread that is a direct call. A **queued** connection
(used for every connection that crosses threads) instead posts the call to
the receiving thread's event loop, so the sender never waits.

## Processes, threads and who lives where

| Process / thread | Objects |
|---|---|
| **daemon**, main thread (Qt event loop) | `QApplication`, `Tray`, `ScreenWatcher`, `ServiceThread` (the handle on the service thread) and its `LocalClient`, the tray's `Client` |
| **daemon**, service thread (`QThread` "playlist-service") | `ServiceWorker` and everything it creates on that thread: the `CommandServer` (socket), the tick `QTimer`, COM (`backends.thread_scope`), the backend, the SQLite `Store`, the `PlaylistService` |
| **window process** (`wallpaper ui`, on demand), main thread | `QApplication`, `SocketClient` (the daemon's socket, subscribed as the window), and the one `MainWindow`: its `ThumbnailLoader` (shared by Home and Settings), its `Sidebar` and its pages, `HomePage` (with its `PlaylistRow`s and `MonitorsView`), `LibraryPage` (with its `GridTab`s, each an `ImageGrid` and a `FastScroll`, its `StatsTab`, its `EnlargedView` with an `ImageCanvas`, the grids' `TileLoader` and `Pregenerator`, and a `LatestRead` each for the folder, the lists, the history and the enlarged image), `SettingsPage` (with its `PlaylistEditor`s, `LookEditor`, a `MonitorsView` and the About tab) |
| **window process**, pool threads (a `Jobs` each: `ThumbnailLoader` 2, `TileLoader` 4, `Pregenerator` 1 at low priority, each `LatestRead` 1) | nothing lives there: jobs read files (images, thumbnails, folders, the lists, the history, an image at full size) and hand their results to `Jobs.done` on the main thread |
| **CLI** (a separate process) | `ipc.send`: blocking client, no event loop |

## Overview

Solid arrows are direct calls within one thread; dashed arrows are queued
signals that cross from one thread to another; thick arrows are socket
lines between processes.

```mermaid
flowchart LR
    subgraph CLI["CLI process"]
        send["ipc.send<br/>blocking client"]
    end
    subgraph UIP["window process"]
        client["SocketClient"]
        windows["MainWindow<br/>HomePage, LibraryPage,<br/>SettingsPage"]
        thumbs["ThumbnailLoader<br/>thread pool"]
        tiles["TileLoader, Pregenerator,<br/>LatestRead: thread pools"]
    end
    subgraph UI["daemon: main thread"]
        tray["Tray"]
        screens["ScreenWatcher"]
        local["LocalClient"]
        host["ServiceThread"]
    end
    subgraph SVC["daemon: service thread"]
        server["CommandServer<br/>socket"]
        timer["tick QTimer<br/>every 5 s"]
        worker["ServiceWorker"]
        service["PlaylistService<br/>backend, Store"]
    end

    send ==>|"request / reply"| server
    client ==>|"subscribe as the window: events, show_page<br/>commands: request / reply"| server
    client -->|"overview_changed, changed,<br/>marks_changed, show_requested"| windows
    windows -->|"run (also stats, upcoming, reset_cycle,<br/>settings, save_settings)"| client
    windows -->|"want"| thumbs
    thumbs -.->|"loaded"| windows
    windows -->|"request, set_wanted,<br/>start (pre-generation), read"| tiles
    tiles -.->|"loaded, done"| windows
    server -->|"received, subscribed"| worker
    worker -->|"reply, broadcast,<br/>show_window"| server
    tray -->|"run (also show_window)"| local
    local -->|"overview_changed,<br/>reply callback"| tray
    local -.->|"_submit"| worker
    worker -.->|"replied, sent (events)"| local
    screens -->|"settled: request_tick"| host
    host -.->|"_tick, _stop"| worker
    timer -->|"timeout"| worker
    worker -->|"calls"| service
    worker -.->|"ticked, failed"| host
```

## Flows

A command from the CLI (`wallpaper next konachan`; if no daemon answers,
the CLI says so: `cli.send_to_daemon`):

```mermaid
sequenceDiagram
    participant CLI as CLI process<br/>ipc.send
    participant S as CommandServer<br/>service thread
    participant W as ServiceWorker<br/>service thread
    participant L as LocalClient<br/>main thread

    CLI->>S: connect, write one JSON line, then block
    S->>S: CommandServer._received: validate
    S->>W: CommandServer.received(connection_id, request)
    W->>W: ServiceWorker.execute: commands.execute(service, request)
    Note over W: ServiceWorker._send, per event: a changed if the command<br/>changed a wallpaper, an overview_changed only if it changed
    W--)L: ServiceWorker.sent(event): Client._dispatch, the tray's signals
    W->>S: CommandServer.broadcast(event), to subscribers
    W->>S: CommandServer.reply(connection_id, reply)
    S->>CLI: one JSON line (data, e.g. NextResult), then close
    Note over CLI: ipc.send returns, then cli.format_reply makes the text
```

A tray click goes through the tray's `Client`, the `LocalClient` that
`ServiceThread` hands out, so the reply comes back to the main thread. The
worker never sees who asked: the socket's `connection_id` stays in
`CommandServer`, the tray's callback (by `request_id`) in `LocalClient`.

```mermaid
sequenceDiagram
    participant Tr as Tray<br/>main thread
    participant L as LocalClient<br/>main thread
    participant W as ServiceWorker<br/>service thread

    Tr->>L: Tray.run: Client.run(request, Tray._replied)
    Note over L: callbacks[request_id] = Tray._replied
    L--)W: LocalClient._submit(request_id, request)
    W->>W: ServiceWorker.submit: ServiceWorker.execute(request)
    W--)L: ServiceWorker.sent(event), as above
    W--)L: ServiceWorker.replied(request_id, reply)
    L->>Tr: LocalClient._replied: pop the callback, call it
    Note over Tr: Tray._replied shows a notification on an ErrorReply
```

A tick (every 5 s, or extra after a screen change: `ScreenWatcher.settled`
-> `ServiceThread.request_tick`). Each event also goes to subscribers, as
in the CLI flow.

```mermaid
sequenceDiagram
    participant T as tick QTimer<br/>service thread
    participant W as ServiceWorker<br/>service thread
    participant L as LocalClient<br/>main thread
    participant H as ServiceThread<br/>main thread
    participant Tr as Tray<br/>main thread

    T->>W: timeout, every 5 s
    W->>W: ServiceWorker.tick: service.tick()
    W--)L: ServiceWorker.sent(changed), once per wallpaper change
    W--)L: ServiceWorker.sent(overview_changed), only if it changed
    L->>Tr: Client._dispatch: Client.overview_changed, Tray.show_overview
    Note over Tr: redraw the menu and the icon
    W--)H: ServiceWorker.ticked
```

Opening the window on a page from the tray (*Open wallpaper_py*, *Library*
or *Settings…*). The daemon's socket knows which window process has the
window, so the tray asks it, and starts one only if none has; the service
thread never starts processes.

```mermaid
sequenceDiagram
    participant Tr as Tray<br/>daemon, main thread
    participant W as ServiceWorker<br/>daemon, service thread
    participant S as CommandServer<br/>daemon, service thread
    participant U as window process<br/>SocketClient, MainWindow

    Tr->>Tr: Tray.open_window("home"): allow_foreground, Client.run(show_window)
    Tr--)W: LocalClient._submit: ServiceWorker.submit
    W->>S: CommandServer.show_window("home")
    alt a window process has the window
        S->>U: show_page {"page": "home"}
        U->>U: Client.show_requested: MainWindow.open_page("home")
        W--)Tr: ServiceWorker.replied: shown true
    else none has it
        W--)Tr: ServiceWorker.replied: shown false
        Tr->>U: Tray._window_replied: processes.start_detached(processes.ui_command("home"))
        U->>S: SocketClient.start: subscribe {"window": "home"}
        S->>U: the current overview, then events
        U->>U: SocketClient.accepted: MainWindow.open_page("home")
    end
```

`wallpaper ui <page>` (from the tray as above, or typed) subscribes as the
window at once. If it found no daemon, it starts one and shows the window
right away, "Connecting…" (`SocketClient.daemon_started`). Two at once, or
one while a window is open: the service thread takes their subscriptions
one at a time, and the first is the window.

```mermaid
sequenceDiagram
    participant A as window process A
    participant B as window process B
    participant S as CommandServer<br/>daemon, service thread

    A->>S: subscribe {"window": "home"}
    B->>S: subscribe {"window": "library"}
    Note over S: A's came first: A is the window
    S->>A: the current overview, then events
    Note over A: SocketClient.accepted: its window shows
    S->>A: show_page {"page": "library"}
    S->>B: {"command": "show_window", "shown": true}, then close
    Note over B: SocketClient.shown_elsewhere: it quits (closing its<br/>"Connecting…" window if it had started the daemon)
```

Home, from an overview to a click (all on the window process's main thread
except the thumbnail jobs):

```mermaid
sequenceDiagram
    participant C as SocketClient
    participant L as HomePage
    participant T as ThumbnailLoader<br/>(pool threads)
    participant P as PlaylistRow

    C->>L: overview_changed: HomePage.show_overview
    L->>P: PlaylistRow.show_view (text, chips, toggles: the overview's state)
    L->>T: ThumbnailLoader.want(current images)
    L->>C: Client.run(status): the schedule, one request at a time
    T--)L: ThumbnailLoader.loaded(path): set on the rows still showing it, and given to MonitorsView.set_image
    C->>L: the StatusResult: order, interval, time left
    Note over L: the countdown QTimer redraws the time left every second
    P->>L: PlaylistRow.requested(playlist, "fav")
    L->>C: Client.run(fav), reply to HomePage.replied
    C->>L: the reply: an error shows in the row
    C->>L: overview_changed: the star is now on
```

The Settings page (the daemon keeps the settings; the page reads and writes
them only through its commands):

```mermaid
sequenceDiagram
    participant C as SocketClient
    participant S as SettingsPage
    participant W as ServiceWorker<br/>daemon, service thread

    C->>S: overview_changed: SettingsPage.show_overview (the monitors)
    S->>C: SettingsPage.load: Client.run(settings), reply to SettingsPage.loaded
    C->>S: SettingsResult: the settings and their revision
    Note over S: edits: a settings_draft.Draft, which SettingsPage._refresh<br/>checks (settings_draft.problems) after every change
    S->>C: SettingsPage.save: Client.run(save_settings: the whole edit, the revision)
    C->>W: save_settings: commands.execute stores and uses them,<br/>then ServiceWorker.tick renders what changed
    W->>C: overview_changed (every page redraws), then the SaveSettingsResult
    C->>S: SettingsPage.saved: the new revision, and the warnings if any
```

The Library page's Stats tab and Upcoming (from the daemon), one request
of each kind at a time:

```mermaid
sequenceDiagram
    participant C as SocketClient
    participant L as LibraryPage
    participant W as ServiceWorker<br/>daemon, service thread

    Note over L: shown (LibraryPage.showEvent), or its playlist chosen: stats (the header's seen count on every tab)
    Note over L: the Stats or Upcoming tab chosen: that one
    L->>C: Client.run(stats: its playlist), reply to LibraryPage.stats_replied
    C->>W: stats: commands.execute counts from the scan, the deck and the lists
    W->>C: StatsResult
    C->>L: StatsTab.show_stats, and the header's count
    L->>C: Client.run(upcoming: 500), reply to LibraryPage.upcoming_replied
    C->>L: the UpcomingResult: GridTab.set_entries, in its order
    C->>L: changed of its playlist, marks_changed: asked again (and on overview_changed: stats always, upcoming after a save)
```

The grid tabs that the page reads itself, off the main thread (nothing
goes through the daemon):

```mermaid
sequenceDiagram
    participant L as LibraryPage
    participant R as LatestRead<br/>(a thread each)
    participant G as GridTab, ImageGrid
    participant T as TileLoader<br/>(tile threads)
    participant P as Pregenerator<br/>(its thread)

    Note over L,P: an overview names the playlists: LibraryPage._pregenerate
    L->>P: Pregenerator.start(the folders, the chosen one first, and the lists)
    Note over L: shown, marks_changed: the lists (badges on every grid)
    Note over L: All images shown, or its playlist chosen: the folder
    Note over L: History shown, changed of its playlist: the history
    L->>R: LatestRead.read(key, entries.read_lists / read_folder / read_history)
    R--)L: LatestRead.done (the latest read only): LibraryPage._fill narrows by the chip
    L->>G: GridTab.set_entries: narrowed by name, sorted, ImageGrid.set_entries
    G->>T: TileLoader.set_wanted(rows on screen), TileLoader.request (painted tiles)
    T--)G: TileLoader.loaded: the tile fades in
    P->>P: waits on TileLoader.idle, then ThumbnailCache.ensure per image, and at the end prunes
```

Shutdown. The daemon (tray *Quit* or Ctrl+C) stops its service thread, and
the closed subscription ends the window process too; the window process alone
ends when its window closes, and the daemon runs on:

```mermaid
sequenceDiagram
    participant H as ServiceThread<br/>daemon, main thread
    participant W as ServiceWorker<br/>daemon, service thread
    participant C as SocketClient<br/>window process
    participant M as MainWindow<br/>window process

    Note over H: app.quit() ends the UI event loop
    H--)W: ServiceThread.shutdown: _stop, then wait for the thread
    W->>W: ServiceWorker.stop: timer, socket (every subscription ends), service, COM
    W--)C: the subscription closes: SocketClient.ended, QApplication.quit
    Note over M: QApplication.aboutToQuit: MainWindow.shutdown
    Note over M: or the window closes: MainWindow.closeEvent
    M->>M: MainWindow.shutdown: HomePage, LibraryPage.shutdown, ThumbnailLoader.close (each waits for its threads)
    M->>C: MainWindow.closed: SocketClient.close (the daemon knows of no window from now on), quit now or on SocketClient.idle
```
