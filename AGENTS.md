# Notes for coding agents

Per-monitor wallpaper playlists for Windows and KDE Plasma: a PySide6 tray
app, a CLI, and an on-demand UI process. Read these first:

- [docs/plan.md](docs/plan.md): phases, decisions, what is next (the source
  of truth for scope)
- [docs/architecture.md](docs/architecture.md): layers and their rules
- [docs/app-wiring.md](docs/app-wiring.md): processes, threads, every
  signal connection
- [docs/protocol.md](docs/protocol.md): the socket protocol

## Commands

Python is managed with [uv](https://docs.astral.sh/uv/); never `pip`.

```
uv sync                                      # environment
uv run python scripts/check.py               # black --check, mypy, pylint, pytest (as CI)
uv run python scripts/check.py black         # format
uv run python scripts/protocol_schema.py     # after changing protocol.py
```

CI runs the checks on Windows and Ubuntu; keep both green. Tests marked
`windows` read the real desktop; tests never change it, and the app is never
started for real from a test (fakes in `tests/fakes.py`). What only a real
desktop shows is in [docs/desktop-checks.md](docs/desktop-checks.md): add
steps there for new desktop-facing behaviour, for the user to try.

## Layering

- `core/` is pure Python: no Qt, no platform APIs. `protocol.py` and
  `commands.py` are Qt-free too. Qt lives only in `app/`.
- Platform code goes in `backends/`, guarded by
  `assert sys.platform == ...`.
- Monitors are addressed by id, never by index or position (they change
  when a monitor is switched off).
- Only the service thread touches `PlaylistService`; everything goes
  through `ServiceWorker.execute`. Nothing on that thread may block on a
  client or spin a nested event loop.
- mypy is strict; pydantic models for anything (de)serialized.
- When you rename something named in docs/app-wiring.md, update the doc:
  `tests/test_docs.py` checks it.

## Qt for Python rules (easy to miss, costly when missed)

Checked by `tests/test_qt_rules.py`; reasons in
[docs/architecture.md](docs/architecture.md#layers).

1. **Never connect a lambda or `partial` that holds `self`** (also not in
   `QTimer.singleShot`). Qt keeps the lambda, and with it the object,
   alive; freeing it later from inside the deletion of its own child
   deletes that child twice: a random access violation, often in an
   unrelated test. Connect bound methods; find the context with `sender()`
   or with data on the sender (`setProperty`, `QAction.setData`).
2. **Every method used as a slot gets `@Slot(...)`** with the signal's
   argument types. Only real slots run in the receiver's thread on a queued
   connection; any other callable runs in the sender's thread.
3. **Don't name a method like an inherited Qt method** (`update`, `show`
   on a widget, ...): PySide then connects it as a plain callable without
   thread affinity (PYSIDE-2990). Overriding `...Event` handlers is fine.
4. **Load pydantic before Qt.** `app/__init__.py` imports `protocol.py`
   first; PySide6's import hook breaks pydantic if it was only partly
   loaded before Qt.
5. A QObject lives in its parent's thread; create the worker's objects in
   `ServiceWorker.start` (on its thread). `sender()` only for same-thread
   connections. Disconnect only your own connections
   (`signal.disconnect(slot)`, not `signal.disconnect()`).
6. A blocking `QLocalSocket` client (`waitFor*`) keeps the GIL: never run
   it in the process that serves the socket (tests use child processes,
   `tests/clients.py`).
