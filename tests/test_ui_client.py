"""
The window process's client: a subscription and commands over the daemon's
socket, against the real service thread (fake backend) in this process.
Everything is event driven, so client and server can share the process.
"""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from wallpaper_py.app import ipc
from wallpaper_py.app.ui.client import SocketClient
from wallpaper_py.app.worker import ServiceThread
from wallpaper_py.core.service import Change
from wallpaper_py.protocol import (
    CommandRequest,
    ErrorReply,
    MarkRequest,
    NextResult,
    Overview,
    Reply,
    Request,
)

from wallpaper_py.core.settings import Settings

from .fakes import FakeBackend, make_monitor, write_settings
from .hosts import make_service_thread


@pytest.fixture(name="settings")
def fixture_settings(tmp_path: Path) -> Settings:
    return write_settings(tmp_path)


@pytest.fixture(name="name")
def fixture_name(tmp_path: Path) -> str:
    return ipc.server_name(tmp_path)


@pytest.fixture(name="service_thread")
def fixture_service_thread(settings: Settings, name: str) -> Iterator[ServiceThread]:
    """The daemon's service and socket; started by the test (or the client)."""
    del settings  # saved before the daemon starts
    backend = FakeBackend([make_monitor("Main")])
    service_thread = make_service_thread(lambda: backend, name)
    yield service_thread
    assert service_thread.shutdown(5_000)


@pytest.fixture(name="clients")
def fixture_clients() -> Iterator[list[SocketClient]]:
    """Clients to close after the test."""
    made: list[SocketClient] = []
    yield made
    for client in made:
        client.close()


def make_client(
    clients: list[SocketClient],
    name: str,
    start_daemon: Callable[[], None] | None = None,
    start_timeout_s: float = 10.0,
) -> SocketClient:
    client = SocketClient(name, start_daemon, start_timeout_s=start_timeout_s)
    clients.append(client)
    return client


def started(qtbot: QtBot, service_thread: ServiceThread) -> None:
    with qtbot.waitSignal(service_thread.overview_changed):
        service_thread.start()


def run(qtbot: QtBot, client: SocketClient, request: CommandRequest) -> Reply:
    replies: list[Reply] = []
    with qtbot.waitSignal(client.idle):
        client.run(request, replies.append)
        assert client.busy() == (not replies)  # unsent: quitting would drop it
    assert len(replies) == 1 and not client.busy()
    return replies[0]


def test_follows_the_daemon_and_runs_commands(
    qtbot: QtBot,
    service_thread: ServiceThread,
    clients: list[SocketClient],
    name: str,
) -> None:
    started(qtbot, service_thread)
    client = make_client(clients, name)
    with qtbot.waitSignal(client.overview_changed) as first:
        client.start()
    overview: Overview = first.args[0]
    assert client.overview == overview
    current = overview.playlists[0].current
    assert current is not None and current.name == "a.jpg"

    changes: list[Change] = []
    client.changed.connect(changes.append)
    with qtbot.waitSignal(client.overview_changed) as updated:
        reply = run(qtbot, client, Request(command="next"))
    assert isinstance(reply, NextResult) and reply.change.image.name == "b.jpg"
    assert [c.image.name for c in changes] == ["b.jpg"]
    current = updated.args[0].playlists[0].current
    assert current is not None and current.name == "b.jpg"

    with qtbot.waitSignal(client.marks_changed):
        run(qtbot, client, MarkRequest(command="fav", target="walls"))

    # The daemon quitting ends the subscription.
    with qtbot.waitSignal(client.ended):
        assert service_thread.shutdown(5_000)


def test_starts_the_daemon_if_none_runs(
    qtbot: QtBot,
    service_thread: ServiceThread,
    clients: list[SocketClient],
    name: str,
) -> None:
    starts: list[bool] = []

    def start_daemon() -> None:
        starts.append(True)
        service_thread.start()  # comes up a little later, like a real start

    client = make_client(clients, name, start_daemon)
    with qtbot.waitSignal(client.overview_changed, timeout=10_000):
        client.start()
    assert starts == [True]


def test_gives_up_if_the_daemon_does_not_start(
    qtbot: QtBot, clients: list[SocketClient], name: str
) -> None:
    starts: list[bool] = []
    client = make_client(
        clients, name, lambda: starts.append(True), start_timeout_s=0.5
    )
    with qtbot.waitSignal(client.failed) as failed:
        client.start()
    assert starts == [True]
    assert failed.args[0].startswith("could not start wallpaper_py; see ")


def test_a_start_that_cannot_run_fails(
    qtbot: QtBot, clients: list[SocketClient], name: str
) -> None:
    def start_daemon() -> None:
        raise FileNotFoundError("no such interpreter")

    client = make_client(clients, name, start_daemon)
    with qtbot.waitSignal(client.failed) as failed:
        client.start()
    assert failed.args == ["Cannot start wallpaper_py: no such interpreter"]


def test_without_a_starter_it_fails_at_once(
    qtbot: QtBot, clients: list[SocketClient], name: str
) -> None:
    client = make_client(clients, name)
    with qtbot.waitSignal(client.failed) as failed:
        client.start()
    assert failed.args == ["wallpaper_py is not running"]


def test_commands_without_a_daemon_are_unreachable(
    qtbot: QtBot, clients: list[SocketClient], name: str
) -> None:
    client = make_client(clients, name)
    reply = run(qtbot, client, Request(command="status"))
    assert reply == ErrorReply.make("unreachable", "wallpaper_py is not running")
