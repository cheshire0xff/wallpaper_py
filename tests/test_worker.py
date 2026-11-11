"""
The service on its worker thread, driven like the app does: through
ServiceHost (the tray) and through its command socket.
"""

import threading
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtNetwork import QLocalSocket
from pytestqt.qtbot import QtBot

from wallpaper_py.app import ipc
from wallpaper_py.app.worker import ServiceHost, ServiceSettings
from wallpaper_py.core.service import Change
from wallpaper_py.protocol import (
    ChangedEvent,
    CommandRequest,
    Envelope,
    ErrorReply,
    HoldResult,
    MarkResult,
    NextResult,
    Overview,
    OverviewChangedEvent,
    PickRequest,
    PickResult,
    PrevResult,
    ReloadRequest,
    ReloadResult,
    Reply,
    Request,
    Subscribe,
)

from .fakes import FakeBackend, FakePresence, make_monitor, write_config
from .hosts import connect, make_host, read_lines


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

    host = make_host(config, backend_factory, name)
    yield host
    assert host.shutdown(5_000)


def start(qtbot: QtBot, host: ServiceHost) -> Overview:
    with qtbot.waitSignal(host.overview_changed) as blocker:
        host.start()
    overview: Overview = blocker.args[0]
    return overview


def submit(qtbot: QtBot, host: ServiceHost, request: CommandRequest) -> Reply:
    replies: list[Reply] = []
    host.submit(request, replies.append)
    qtbot.waitUntil(lambda: bool(replies))
    return replies[0]


def ask(
    qtbot: QtBot, name: str, config: Path, request: CommandRequest | Subscribe
) -> QLocalSocket:
    """Send a request over the socket without waiting for the reply."""
    return connect(qtbot, name, ipc.encode(Envelope(request=request, config=config)))


def answer(qtbot: QtBot, socket: QLocalSocket) -> Reply:
    return ipc.decode_reply(read_lines(qtbot, socket, 1)[0])


def shown(reply: Reply) -> str:
    """The image a next / prev reply shows."""
    assert isinstance(reply, NextResult | PrevResult), reply
    assert reply.change is not None
    return reply.change.image.name


def error(reply: Reply) -> str:
    assert isinstance(reply, ErrorReply), reply
    return reply.error.message


def send(qtbot: QtBot, name: str, config: Path, request: CommandRequest) -> Reply:
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
        host.submit(Request.model_validate({"command": command}), replies.append)
    qtbot.waitUntil(lambda: len(replies) == 4)
    assert [shown(r) for r in replies[:3]] == ["b.jpg", "c.jpg", "b.jpg"]
    assert isinstance(replies[3], MarkResult) and replies[3].changed
    qtbot.waitUntil(
        lambda: host.overview is not None and host.overview.playlists[0].favourite
    )


def test_tray_commands_announce_changes(qtbot: QtBot, host: ServiceHost) -> None:
    start(qtbot, host)
    with qtbot.waitSignal(host.changed) as blocker:
        host.submit(Request(command="next"), lambda _: None)
    change: Change = blocker.args[0]
    assert (change.image.name, change.reason) == ("b.jpg", "next")


def test_socket_commands_run_on_the_service_thread(
    qtbot: QtBot, host: ServiceHost, backend: FakeBackend, name: str, config: Path
) -> None:
    start(qtbot, host)
    threads: set[int] = set()
    backend.before_monitors = lambda _: threads.add(threading.get_ident())
    with qtbot.waitSignal(host.changed) as blocker:
        reply = send(qtbot, name, config, Request(command="next"))
    assert shown(reply) == "b.jpg"
    assert blocker.args[0].image.name == "b.jpg"  # announced like a tick's
    assert threads and threading.get_ident() not in threads


def test_socket_refuses_another_config(
    qtbot: QtBot, host: ServiceHost, name: str, tmp_path: Path
) -> None:
    start(qtbot, host)
    reply = send(qtbot, name, tmp_path / "other.toml", Request(command="status"))
    assert "The running app uses" in error(reply)


def test_socket_answers_during_and_after_reloads(
    qtbot: QtBot, host: ServiceHost, name: str, config: Path, hold: Hold
) -> None:
    start(qtbot, host)
    assert not isinstance(
        send(qtbot, name, config, Request(command="status")), ErrorReply
    )

    # A request while the reload is still opening the service: answered
    # once it is done.
    hold.active = True
    host.reload()
    qtbot.waitUntil(hold.entered.is_set)
    pending = ask(qtbot, name, config, Request(command="next"))
    hold.release.set()
    assert shown(answer(qtbot, pending)) == "b.jpg"
    hold.active = False

    # A broken config: the socket answers with the error.
    text = config.read_text(encoding="utf-8")
    config.unlink()
    with qtbot.waitSignal(host.overview_changed):
        host.reload()
    reply = send(qtbot, name, config, Request(command="next"))
    assert isinstance(reply, ErrorReply) and reply.error.kind == "config_error"
    assert "No config file" in reply.error.message

    config.write_text(text, encoding="utf-8")
    with qtbot.waitSignal(host.overview_changed):
        host.reload()
    assert shown(send(qtbot, name, config, Request(command="next"))) == "c.jpg"


def test_subscribers_follow_events(
    qtbot: QtBot, host: ServiceHost, name: str, config: Path
) -> None:
    overview = start(qtbot, host)
    subscriber = ask(qtbot, name, config, Subscribe())
    # First the current overview, then events as they happen.
    [welcome] = read_lines(qtbot, subscriber, 1)
    assert ipc.decode_event(welcome) == OverviewChangedEvent(overview=overview)

    assert shown(send(qtbot, name, config, Request(command="next"))) == "b.jpg"
    changed, refreshed = (ipc.decode_event(l) for l in read_lines(qtbot, subscriber, 2))
    assert isinstance(changed, ChangedEvent)
    assert (changed.change.image.name, changed.change.reason) == ("b.jpg", "next")
    assert isinstance(refreshed, OverviewChangedEvent)
    current = refreshed.overview.playlists[0].current
    assert current is not None and current.name == "b.jpg"

    # The app stopping ends the subscription.
    with qtbot.waitSignal(subscriber.disconnected):
        assert host.shutdown(5_000)


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
    assert "No config file" in error(submit(qtbot, host, Request(command="next")))

    config.write_text(text, encoding="utf-8")
    with qtbot.waitSignal(host.overview_changed) as blocker:
        host.reload()
    assert blocker.args[0].error is None
    assert shown(submit(qtbot, host, Request(command="next"))) == "b.jpg"


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
    assert shown(submit(qtbot, host, Request(command="next"))) == "b.jpg"


def test_shutdown_before_start_is_harmless(config: Path) -> None:
    host = ServiceHost(
        ServiceSettings(config, lambda: FakeBackend([]), FakePresence, nullcontext)
    )
    assert host.shutdown()


def test_hold_reaches_the_tray_and_subscribers(
    qtbot: QtBot, host: ServiceHost, name: str, config: Path
) -> None:
    start(qtbot, host)
    subscriber = ask(qtbot, name, config, Subscribe())
    read_lines(qtbot, subscriber, 1)  # the current overview
    with qtbot.waitSignal(host.overview_changed) as blocker:
        reply = submit(qtbot, host, Request(command="hold"))
    assert reply == HoldResult(playlist="walls", held=True, changed=True)
    assert blocker.args[0].playlists[0].held
    [line] = read_lines(qtbot, subscriber, 1)
    event = ipc.decode_event(line)
    assert isinstance(event, OverviewChangedEvent) and event.overview.playlists[0].held


def test_hold_survives_an_app_restart(
    qtbot: QtBot, host: ServiceHost, backend: FakeBackend, name: str, config: Path
) -> None:
    start(qtbot, host)
    submit(qtbot, host, Request(command="hold"))
    assert host.shutdown(5_000)
    again = make_host(config, lambda: backend, name)
    try:
        assert start(qtbot, again).playlists[0].held
    finally:
        assert again.shutdown(5_000)


def test_pick_over_the_socket(
    qtbot: QtBot, host: ServiceHost, name: str, config: Path, tmp_path: Path
) -> None:
    start(qtbot, host)
    image = tmp_path / "elsewhere" / "x.png"
    image.parent.mkdir()
    Image.new("RGB", (8, 8)).save(image)
    with qtbot.waitSignal(host.changed) as blocker:
        reply = send(qtbot, name, config, PickRequest(image=image))
    assert isinstance(reply, PickResult) and reply.change.image == image
    assert blocker.args[0].reason == "picked"
    assert shown(send(qtbot, name, config, Request(command="prev"))) == "a.jpg"


def test_reload_answers_with_its_result(
    qtbot: QtBot, host: ServiceHost, name: str, config: Path
) -> None:
    overview = start(qtbot, host)
    assert overview.config == config  # the overview says which file the app runs
    assert send(qtbot, name, config, ReloadRequest()) == ReloadResult()

    # Valid but probably a mistake: the reply carries the warning.
    text = config.read_text(encoding="utf-8")
    config.write_text(
        text.replace("pause_when_idle = false", 'pause_when_idle = "20m"'),
        encoding="utf-8",
    )
    reply = send(qtbot, name, config, ReloadRequest())
    assert isinstance(reply, ReloadResult)
    assert [w.split(" (")[0] for w in reply.warnings] == ["[playlists.walls] interval"]

    # Broken: the error, and the overview says so (with the file still named).
    config.write_text("[playlists.walls\n", encoding="utf-8")
    with qtbot.waitSignal(host.overview_changed) as blocker:
        reply = send(qtbot, name, config, ReloadRequest())
    assert isinstance(reply, ErrorReply) and reply.error.kind == "config_error"
    assert blocker.args[0].error == reply.error.message
    assert blocker.args[0].config == config

    # The tray's Reload config takes the same path.
    config.write_text(text, encoding="utf-8")
    assert submit(qtbot, host, ReloadRequest()) == ReloadResult()
    replies: list[Reply] = []
    host.reload(replies.append)
    qtbot.waitUntil(lambda: bool(replies))
    assert replies == [ReloadResult()]


def test_a_request_without_a_config_means_the_apps(
    qtbot: QtBot, host: ServiceHost, name: str
) -> None:
    start(qtbot, host)
    envelope = Envelope(request=Request(command="next"))
    assert envelope.config is None
    socket = connect(qtbot, name, ipc.encode(envelope))
    assert shown(answer(qtbot, socket)) == "b.jpg"
