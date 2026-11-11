"""
The service on its worker thread, driven like the app does: through
ServiceHost (the tray) and through its command socket.
"""

import threading
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path

import pytest
from PySide6.QtNetwork import QLocalSocket
from pytestqt.qtbot import QtBot

from wallpaper_py.app import ipc
from wallpaper_py.app.worker import ServiceHost, ServiceSettings
from wallpaper_py.commands import Overview, Reply, Request
from wallpaper_py.core.service import Change

from .fakes import FakeBackend, FakePresence, make_monitor, write_config


@pytest.fixture(name="backend")
def fixture_backend() -> FakeBackend:
    return FakeBackend([make_monitor("Main")])


@pytest.fixture(name="config")
def fixture_config(tmp_path: Path) -> Path:
    return write_config(tmp_path)


class Hold:
    """While `active`, opening the service waits until `release` is set."""

    def __init__(self) -> None:
        self.active = False
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self) -> None:
        if self.active:
            self.entered.set()
            assert self.release.wait(10)


@pytest.fixture(name="hold")
def fixture_hold() -> Hold:
    return Hold()


@pytest.fixture(name="name")
def fixture_name(tmp_path: Path) -> str:
    return ipc.server_name(tmp_path)


@pytest.fixture(name="host")
def fixture_host(
    config: Path, backend: FakeBackend, hold: Hold, name: str
) -> Iterator[ServiceHost]:
    def backend_factory() -> FakeBackend:
        hold()
        return backend

    host = ServiceHost(
        ServiceSettings(
            config_path=config,
            backend_factory=backend_factory,
            presence_factory=FakePresence,
            thread_scope=nullcontext,
            tick_period=60.0,  # only the first tick and explicit ones
            server_name=name,
        )
    )
    yield host
    assert host.shutdown(5_000)


def start(qtbot: QtBot, host: ServiceHost) -> Overview:
    with qtbot.waitSignal(host.overview_changed) as blocker:
        host.start()
    overview: Overview = blocker.args[0]
    return overview


def submit(qtbot: QtBot, host: ServiceHost, request: Request) -> Reply:
    replies: list[Reply] = []
    host.submit(request, replies.append)
    qtbot.waitUntil(lambda: bool(replies))
    return replies[0]


def ask(qtbot: QtBot, name: str, config: Path, request: Request) -> QLocalSocket:
    """
    Send a request over the socket without waiting for the reply. Event
    driven (no waitFor*), so it can run on this thread, next to the server.
    """
    socket = QLocalSocket()
    with qtbot.waitSignal(socket.connected):
        socket.connectToServer(name)
    socket.write(ipc.encode_request(ipc.Envelope(request=request, config=config)))
    return socket


def answer(qtbot: QtBot, socket: QLocalSocket) -> Reply:
    data = bytearray()

    def complete() -> bool:
        data.extend(socket.readAll().data())
        return b"\n" in data

    qtbot.waitUntil(complete)
    return ipc.decode_reply(bytes(data).split(b"\n", 1)[0])


def send(qtbot: QtBot, name: str, config: Path, request: Request) -> Reply:
    return answer(qtbot, ask(qtbot, name, config, request))


def test_first_tick_runs_on_the_worker_thread(
    qtbot: QtBot, host: ServiceHost, backend: FakeBackend, tmp_path: Path
) -> None:
    threads: set[int] = set()
    backend.before_monitors = lambda _: threads.add(threading.get_ident())
    overview = start(qtbot, host)
    assert overview.playlists[0].current == tmp_path / "walls" / "a.jpg"
    assert threads and threading.get_ident() not in threads
    assert host.overview == overview


def test_commands_are_answered_in_order(qtbot: QtBot, host: ServiceHost) -> None:
    start(qtbot, host)
    replies: list[Reply] = []
    for command in ("next", "next", "prev", "fav"):
        host.submit(Request(command), replies.append)
    qtbot.waitUntil(lambda: len(replies) == 4)
    assert [r.out for r in replies[:3]] == [
        "walls: b.jpg on Main",
        "walls: c.jpg on Main",
        "walls: b.jpg on Main",
    ]
    assert replies[3].out.startswith("Added ")
    qtbot.waitUntil(
        lambda: host.overview is not None and host.overview.playlists[0].favourite
    )


def test_tray_commands_announce_changes(qtbot: QtBot, host: ServiceHost) -> None:
    start(qtbot, host)
    with qtbot.waitSignal(host.changed) as blocker:
        host.submit(Request("next"), lambda _: None)
    change: Change = blocker.args[0]
    assert (change.image.name, change.reason) == ("b.jpg", "next")


def test_socket_commands_run_on_the_service_thread(
    qtbot: QtBot, host: ServiceHost, backend: FakeBackend, name: str, config: Path
) -> None:
    start(qtbot, host)
    threads: set[int] = set()
    backend.before_monitors = lambda _: threads.add(threading.get_ident())
    with qtbot.waitSignal(host.changed) as blocker:
        reply = send(qtbot, name, config, Request("next"))
    assert reply.out == "walls: b.jpg on Main"
    assert blocker.args[0].image.name == "b.jpg"  # announced like a tick's
    assert threads and threading.get_ident() not in threads


def test_socket_refuses_another_config(
    qtbot: QtBot, host: ServiceHost, name: str, tmp_path: Path
) -> None:
    start(qtbot, host)
    reply = send(qtbot, name, tmp_path / "other.toml", Request("status"))
    assert reply.code == 1 and "The running app uses" in reply.err


def test_socket_answers_during_and_after_reloads(
    qtbot: QtBot, host: ServiceHost, name: str, config: Path, hold: Hold
) -> None:
    start(qtbot, host)
    assert send(qtbot, name, config, Request("status")).code == 0

    # A request while the reload is still opening the service: answered
    # once it is done.
    hold.active = True
    host.reload()
    qtbot.waitUntil(hold.entered.is_set)
    pending = ask(qtbot, name, config, Request("next"))
    hold.release.set()
    assert answer(qtbot, pending).out == "walls: b.jpg on Main"
    hold.active = False

    # A broken config: the socket answers with the error.
    text = config.read_text(encoding="utf-8")
    config.unlink()
    with qtbot.waitSignal(host.overview_changed):
        host.reload()
    reply = send(qtbot, name, config, Request("next"))
    assert reply.code == 1 and "No config file" in reply.err

    config.write_text(text, encoding="utf-8")
    with qtbot.waitSignal(host.overview_changed):
        host.reload()
    assert send(qtbot, name, config, Request("next")).out == "walls: c.jpg on Main"


def test_extra_tick_on_request(qtbot: QtBot, host: ServiceHost) -> None:
    start(qtbot, host)
    with qtbot.waitSignal(host.ticked):
        host.request_tick()


def test_config_error_then_reload(
    qtbot: QtBot, host: ServiceHost, config: Path
) -> None:
    text = config.read_text(encoding="utf-8")
    config.unlink()
    overview = start(qtbot, host)
    assert overview.error is not None and "No config file" in overview.error
    reply = submit(qtbot, host, Request("next"))
    assert reply.code == 1 and "No config file" in reply.err

    config.write_text(text, encoding="utf-8")
    with qtbot.waitSignal(host.overview_changed) as blocker:
        host.reload()
    assert blocker.args[0].error is None
    assert submit(qtbot, host, Request("next")).code == 0


def test_unexpected_tick_errors_are_survived(
    qtbot: QtBot, host: ServiceHost, backend: FakeBackend
) -> None:
    start(qtbot, host)

    def boom(_: int) -> None:
        raise OSError("COM hiccup")

    backend.before_monitors = boom
    with qtbot.waitSignal(host.ticked):
        host.request_tick()
    backend.before_monitors = None
    assert submit(qtbot, host, Request("next")).code == 0


def test_shutdown_before_start_is_harmless(config: Path) -> None:
    host = ServiceHost(
        ServiceSettings(config, lambda: FakeBackend([]), FakePresence, nullcontext)
    )
    assert host.shutdown()
