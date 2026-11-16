# Notes for coding agents

Per-monitor wallpaper playlists for Windows and KDE Plasma: a PySide6 tray
daemon, a CLI, and an on-demand window process. Read these first:

- [docs/plan.md](docs/plan.md): phases, what is next, what is decided
  against (the source of truth for scope)
- [docs/architecture.md](docs/architecture.md): layers, and the
  decisions with their reasons
- [docs/app-wiring.md](docs/app-wiring.md): processes, threads, and
  diagrams of the flows
- [docs/protocol.md](docs/protocol.md): the socket protocol

Each fact is written in one place: what a module does in its docstring,
why it is built that way in architecture.md, which thread an object lives
on and how a flow goes in app-wiring.md (a table and diagrams, not every
`connect()`: the code has those). Don't restate one in another; point to
it.

## Commands

Python is managed with [uv](https://docs.astral.sh/uv/); never `pip`.

```
uv sync                                      # environment
uv run python scripts/check.py               # black --check, mypy, pylint, pytest (as CI)
uv run python scripts/check.py black         # format
uv run python scripts/protocol_schema.py     # after changing protocol.py
```

CI runs the checks on Windows and Ubuntu; keep both green. Tests marked
`windows` read the real desktop; tests never change it, and the daemon is never
started for real from a test (fakes in `tests/fakes.py`). What only a real
desktop shows is in [docs/desktop-checks.md](docs/desktop-checks.md), a
short list the user runs before a release and on a new platform. Don't
grow it: add a step only for something no test can see and no step there
shows yet (else extend that step). For a new feature, end the session's
report with a few steps to try once instead.

## Layering

- `core/` is pure Python: no Qt, no platform APIs. `protocol.py` and
  `commands.py` are Qt-free too. Qt lives only in `app/`.
- Platform code goes in `backends/`, guarded by
  `assert sys.platform == ...`.
- Monitors are addressed by id, never by index or position (they change
  when a monitor is switched off). Playlists too, by id, never by name, in
  the tray and the windows (keys, targets, remembered choices): a rename
  then breaks none of them. The name is for showing, and for the CLI.
- Only the service thread touches `PlaylistService`; everything goes
  through `ServiceWorker.execute`. Nothing on that thread may block on a
  client or spin a nested event loop.
- Every method that overrides a base method (ours, Qt's, the standard
  library's) is marked `@override` (`typing`); mypy enforces it
  (`explicit-override`). A misspelt Qt event handler is then an error,
  not a method Qt never calls.
- mypy is strict; pydantic models for anything (de)serialized: the
  settings are `Settings` in `core/settings.py` (stored in the state
  database, sent on the socket; a new setting goes there, with its
  meaning checked in `check_settings`), not `isinstance` checks.
  Hand-written code checks only what values mean (durations, paths,
  uniqueness). Use `StrictInt` / `StrictStr` / `StrictBool` and
  `Field(strict=True)` for values a client sends: lax pydantic takes
  `true` as an int and `0` as `Literal[False]`.
- The settings are written only by the daemon, through `save_settings`
  (the Settings page); there is no settings file. They hold monitors by
  id only (never names: two monitors of one model share one) and
  generated playlist ids. No playlists is valid settings that rotate
  nothing.
- Pre-release: no migrations or backward compatibility for stored state or
  the protocol; change it in place.
- Files people write by hand (the favourites and to-remove lists) are UTF-8
  without a byte order mark, nothing else; a BOM is a `ConfigError` that
  says so (`marks.decode_text`). This is on purpose: don't add BOM
  tolerance (`utf-8-sig`), and don't report it in reviews.
- When you rename something named in docs/app-wiring.md or AGENTS.md,
  update it: `tests/test_docs.py` checks them.

## Code reviews

Findings are what goes wrong for the user in normal use: a crash, lost or
wrong data, an error nobody sees, a wallpaper that doesn't change when it
should. Only these count toward the number of findings.

After them, list the rest under one heading, "Low severity", grouped by
kind, one line each, nothing to fix by default:

- clock skew, timestamps in the future, and other environment faults the
  daemon can't see;
- a repeated cheap check or query, a log line twice, a local variable or
  other style-only changes.

Don't report at all:

- state or files from before a change (no migrations);
- a decision already written down in this file or in docs/.

## Qt for Python rules (easy to miss, costly when missed)

Checked by `tests/test_qt_rules.py`; each with its reason (this is
their one place).

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
   thread affinity (PYSIDE-2990). Overriding `...Event` handlers is fine,
   and so are an item model's `rowCount` / `data` and a delegate's
   `paint` / `sizeHint` (what they exist for); background work goes
   through `Jobs` (app/ui/jobs.py), never a `QThread.run` override.
4. **Load pydantic before Qt.** `app/__init__.py` imports `protocol.py`
   first; PySide6's import hook breaks pydantic if it was only partly
   loaded before Qt.
5. A QObject lives in its parent's thread; create the worker's objects in
   `ServiceWorker.start` (on its thread), which only sets up and returns,
   so the thread's event loop runs the timer and the socket. `sender()`
   only for same-thread connections (invalid for a direct call from
   another thread). Disconnect only your own connections
   (`signal.disconnect(slot)`, not `signal.disconnect()`).
6. A blocking `QLocalSocket` client (`waitFor*`) keeps the GIL: never run
   it in the process that serves the socket (tests use child processes,
   `tests/clients.py`).
