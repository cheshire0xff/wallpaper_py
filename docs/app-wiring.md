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
| **app**, UI thread (main thread, Qt event loop) | `QApplication`, `Tray`, `WindowOpener`, `ScreenWatcher`, and `ServiceHost`, the thin proxy to the service thread |
| **app**, service thread (`QThread` "playlist-service") | `ServiceWorker` and everything it creates on that thread: the `CommandServer` (socket), the tick `QTimer`, COM (`backends.thread_scope`), the backend, the SQLite `Store`, the `PlaylistService` |
| **UI process** (`wallpaper ui`, on demand), UI thread | `QApplication`, `UiServer` (its own socket), `SocketClient` (the app's socket), and the one `MainWindow`: its `ThumbnailLoader` (shared by Home and Settings), its `Sidebar` and its pages, `HomePage` (with its `PlaylistRow`s and `MonitorsView`), `LibraryPage` (with its `OverviewTab`, the All images `ImageGrid`, and the grids' `TileLoader`, `Pregenerator` and `FolderScan`), `SettingsPage` (with its `PlaylistEditor`s, `LookEditor`, a `MonitorsView` and the About tab) |
| **UI process**, pool threads (a `Jobs` each: `ThumbnailLoader` 2, `TileLoader` 4, `Pregenerator` 1 at low priority, `FolderScan` 1) | nothing lives there: jobs read files (images, thumbnails, folders) and hand their results to `Jobs.done` on the UI thread |
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
    subgraph UIP["UI process"]
        uiserver["UiServer<br/>its own socket"]
        client["SocketClient"]
        windows["MainWindow<br/>HomePage, LibraryPage,<br/>SettingsPage"]
        thumbs["ThumbnailLoader<br/>thread pool"]
        tiles["TileLoader, Pregenerator,<br/>FolderScan: thread pools"]
    end
    subgraph UI["app: UI thread"]
        tray["Tray"]
        opener["WindowOpener"]
        screens["ScreenWatcher"]
        host["ServiceHost"]
    end
    subgraph SVC["app: service thread"]
        server["CommandServer<br/>socket"]
        timer["tick QTimer<br/>every 5 s"]
        worker["ServiceWorker"]
        service["PlaylistService<br/>backend, Store"]
    end

    send ==>|"request / reply"| server
    client ==>|"subscribe: events<br/>commands: request / reply"| server
    opener ==>|"show page / ok"| uiserver
    uiserver -->|"show_requested"| windows
    client -->|"overview_changed, changed,<br/>marks_changed"| windows
    windows -->|"run (also stats, reset_cycle,<br/>settings, save_settings)"| client
    windows -->|"want"| thumbs
    thumbs -.->|"loaded"| windows
    windows -->|"request, set_wanted,<br/>start (pre-generation), scan"| tiles
    tiles -.->|"loaded, scanned"| windows
    server -->|"received, subscribed"| worker
    worker -->|"reply, broadcast"| server
    tray -->|"open_window"| opener
    tray -->|"run: submit"| host
    screens -->|"settled: request_tick"| host
    host -.->|"_submit, _tick, _stop"| worker
    timer -->|"timeout"| worker
    worker -->|"calls"| service
    worker -.->|"replied, overview_changed, changed, ticked, failed"| host
    host -->|"overview_changed"| tray
    host -->|"reply callback"| tray
```

## Flows

A command from the CLI (`wallpaper next konachan` while the app runs; if
none answers, the CLI runs it itself: `cli.send_to_app`):

```mermaid
sequenceDiagram
    participant CLI as CLI process<br/>ipc.send
    participant S as CommandServer<br/>service thread
    participant W as ServiceWorker<br/>service thread
    participant H as ServiceHost<br/>UI thread

    CLI->>S: connect, write one JSON line, then block
    S->>S: CommandServer._received: validate
    S->>W: CommandServer.received(connection_id, request)
    W->>W: ServiceWorker.execute: commands.execute(service, request)
    W--)H: ServiceWorker.changed(change), if the command changed a wallpaper
    W->>S: CommandServer.broadcast(changed), to subscribers
    W--)H: ServiceWorker.overview_changed(overview), only if it changed
    W->>S: CommandServer.broadcast(overview_changed), to subscribers
    W->>S: CommandServer.reply(connection_id, reply)
    S->>CLI: one JSON line (data, e.g. NextResult), then close
    Note over CLI: ipc.send returns, then cli.format_reply makes the text
```

A tray click goes through `ServiceHost`, so the reply comes back to the UI
thread. The worker never sees who asked: the socket's `connection_id`
stays in `CommandServer`, the tray's callback (by `request_id`) in
`ServiceHost`.

```mermaid
sequenceDiagram
    participant Tr as Tray<br/>UI thread
    participant H as ServiceHost<br/>UI thread
    participant W as ServiceWorker<br/>service thread

    Tr->>H: Tray.run: ServiceHost.submit(request, Tray._replied)
    Note over H: callbacks[request_id] = Tray._replied
    H--)W: ServiceHost._submit(request_id, request)
    W->>W: ServiceWorker.submit: ServiceWorker.execute(request)
    W--)H: ServiceWorker.changed / overview_changed, as above
    W--)H: ServiceWorker.replied(request_id, reply)
    H->>Tr: ServiceHost._on_replied: pop the callback, call it
    Note over Tr: Tray._replied shows a notification on an ErrorReply
```

A tick (every 5 s, or extra after a screen change: `ScreenWatcher.settled`
-> `ServiceHost.request_tick`). Each change and new overview also goes to
subscribers, as in the CLI flow.

```mermaid
sequenceDiagram
    participant T as tick QTimer<br/>service thread
    participant W as ServiceWorker<br/>service thread
    participant H as ServiceHost<br/>UI thread
    participant Tr as Tray<br/>UI thread

    T->>W: timeout, every 5 s
    W->>W: ServiceWorker.tick: service.tick()
    W--)H: ServiceWorker.changed(change), once per wallpaper change
    W--)H: ServiceWorker.overview_changed(overview), only if it changed
    H->>Tr: ServiceHost.overview_changed: Tray.show_overview
    Note over Tr: redraw the menu and the icon
    W--)H: ServiceWorker.ticked
```

Opening the window on a page (tray *Open wallpaper_py*, *Library* or
*Settings…*, or `wallpaper ui [home|library|settings]`):

```mermaid
sequenceDiagram
    participant O as WindowOpener<br/>app, UI thread
    participant U as UI process<br/>UiServer, MainWindow
    participant C as SocketClient<br/>UI process
    participant S as CommandServer<br/>app, service thread

    O->>U: connect to the UI socket, write {"page": "home"}
    alt a UI process runs
        U->>U: UiServer.show_requested: MainWindow.open_page("home")
        U->>O: {"error": null}, then close
    else nothing listens
        O->>U: processes.start_detached(processes.ui_command): wallpaper ui home
        U->>U: launcher.show_in_running_ui: none, so take the UI lock, listen
        U->>U: MainWindow.open_page("home")
        C->>S: SocketClient.start: subscribe
        S->>C: the current overview, then events
        C->>U: SocketClient.overview_changed: the pages redraw
    end
```

Home, from an overview to a click (all on the UI process's UI thread
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

The Settings page (the app keeps the settings; the page reads and writes
them only through its commands):

```mermaid
sequenceDiagram
    participant C as SocketClient
    participant S as SettingsPage
    participant W as ServiceWorker<br/>app, service thread

    C->>S: overview_changed: SettingsPage.show_overview (the monitors)
    S->>C: SettingsPage.load: Client.run(settings), reply to SettingsPage.loaded
    C->>S: SettingsResult: the settings and their revision
    Note over S: edits: a settings_draft.Draft, which SettingsPage._refresh<br/>checks (settings_draft.problems) after every change
    S->>C: SettingsPage.save: Client.run(save_settings: the whole edit, the revision)
    C->>W: save_settings: commands.execute stores and uses them,<br/>then ServiceWorker.tick renders what changed
    W->>C: overview_changed (every page redraws), then the SaveSettingsResult
    C->>S: SettingsPage.saved: the new revision, and the warnings if any
```

The Library page's Overview tab (the numbers come from the app):

```mermaid
sequenceDiagram
    participant C as SocketClient
    participant L as LibraryPage
    participant W as ServiceWorker<br/>app, service thread

    Note over L: shown (LibraryPage.showEvent), or its tab or playlist chosen
    L->>C: Client.run(stats: its playlist), reply to LibraryPage.stats_replied
    C->>W: stats: commands.execute counts from the scan, the deck and the lists
    W->>C: StatsResult
    C->>L: OverviewTab.show_stats
    C->>L: overview_changed, marks_changed, or changed of its playlist: asked again
```

The Library page's All images tab (the page reads the folder and the
images itself, off the UI thread; nothing goes through the app):

```mermaid
sequenceDiagram
    participant L as LibraryPage
    participant F as FolderScan<br/>(its thread)
    participant G as ImageGrid
    participant T as TileLoader<br/>(tile threads)
    participant P as Pregenerator<br/>(its thread)

    Note over L,P: an overview names the playlists: LibraryPage._pregenerate
    L->>P: Pregenerator.start(the folders, the chosen one first, and the lists)
    Note over L: All images shown, or its playlist chosen
    L->>F: FolderScan.scan(the folder)
    F--)L: FolderScan.scanned: ImageGrid.set_images
    G->>T: TileLoader.set_wanted(rows on screen), TileLoader.request (painted tiles)
    T--)G: TileLoader.loaded: the tile fades in
    P->>P: waits on TileLoader.idle, then ThumbnailCache.ensure per image, and at the end prunes
```

Shutdown. The app (tray *Quit* or Ctrl+C) stops its service thread, and
the closed subscription ends the UI process too; the UI process alone
ends when its window closes, and the app runs on:

```mermaid
sequenceDiagram
    participant H as ServiceHost<br/>app, UI thread
    participant W as ServiceWorker<br/>app, service thread
    participant C as SocketClient<br/>UI process
    participant M as MainWindow<br/>UI process

    Note over H: app.quit() ends the UI event loop
    H--)W: ServiceHost.shutdown: _stop, then wait for the thread
    W->>W: ServiceWorker.stop: timer, socket (every subscription ends), service, COM
    W--)C: the subscription closes: SocketClient.ended, QApplication.quit
    Note over M: QApplication.aboutToQuit: MainWindow.shutdown
    Note over M: or the window closes: MainWindow.closeEvent
    M->>M: MainWindow.shutdown: HomePage, LibraryPage.shutdown, ThumbnailLoader.close (each waits for its threads)
    M->>C: MainWindow.closed: UiServer closes, quit now or on SocketClient.idle
```
