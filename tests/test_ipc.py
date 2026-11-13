"""The sockets: message format, the clients and the servers."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot

from wallpaper_py.app import ipc
from wallpaper_py.app.ipc import CommandServer, LineRequest, ProtocolError
from wallpaper_py.core.service import Change
from wallpaper_py.protocol import (
    ChangedEvent,
    Envelope,
    ErrorReply,
    NextResult,
    Overview,
    OverviewChangedEvent,
    PrevResult,
    Request,
    Subscribe,
)

from .clients import RAW, SEND, run_python
from .hosts import connect, read_lines

CHANGE = Change("walls", Path("/p/a.jpg"), "next", ("Main",))


@pytest.fixture(name="name")
def fixture_name(tmp_path: Path) -> str:
    return ipc.server_name(tmp_path)


@pytest.fixture(name="config")
def fixture_config(tmp_path: Path) -> Path:
    return tmp_path / "config.toml"


@pytest.fixture(name="owner")
def fixture_owner() -> QObject:
    """The parent of LineRequests, which Qt (not Python) must own."""
    return QObject()


@pytest.fixture(name="server")
def fixture_server(name: str, config: Path) -> Iterator[CommandServer]:
    server = CommandServer(name, config)

    def answer(connection_id: int, request: Request) -> None:
        change = CHANGE if request.command == "next" else None
        server.reply(connection_id, PrevResult(playlist="walls", change=change))

    server.received.connect(answer)
    server.listen()
    yield server
    server.close()


def send(
    qtbot: QtBot, name: str, command: str, config: Path, timeout: float = 30
) -> str:
    """Send from a child process; its printed result."""
    result = run_python(qtbot, SEND, name, command, str(config), str(timeout))
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_server_name_is_per_directory_and_role(tmp_path: Path) -> None:
    name = ipc.server_name(tmp_path)
    assert name == ipc.server_name(tmp_path / "x" / "..")
    assert name != ipc.server_name(tmp_path / "other")
    assert name.startswith("wallpaper_py-") and "/" not in name and "\\" not in name
    assert ipc.server_name(tmp_path, "ui") == f"{name}-ui"


def test_messages_round_trip(config: Path) -> None:
    envelope = Envelope(
        request=Request(command="next", target="konachan"), config=config
    )
    assert ipc.decode_request(ipc.encode(envelope).strip()) == envelope
    subscribe = Envelope(request=Subscribe(), config=config)
    assert ipc.decode_request(ipc.encode(subscribe)) == subscribe
    for reply in (
        NextResult(playlist="walls", change=CHANGE),
        ErrorReply.make("not_attached", "None attached"),
    ):
        assert ipc.decode_reply(ipc.encode(reply)) == reply
    for event in (
        ChangedEvent(change=CHANGE),
        OverviewChangedEvent(overview=Overview(paused="idle")),
    ):
        assert ipc.decode_event(ipc.encode(event)) == event


def test_replies_ignore_unknown_fields() -> None:
    line = b'{"command": "prev", "playlist": "walls", "change": null, "new": 1}' b"\n"
    assert ipc.decode_reply(line) == PrevResult(playlist="walls", change=None)


@pytest.mark.parametrize(
    "line, problem",
    [
        (b"not json", "Invalid JSON"),
        (b"[]", "should be an object"),
        (b'{"v": 1, "request": {"command": "next"}, "config": "c"}', "protocol 2"),
        (b'{"request": {"command": "delete"}, "config": "c"}', "'delete'"),
        (b'{"request": {"command": "next", "target": 3}, "config": "c"}', "target"),
        (b'{"request": {"command": "next"}, "config": 3}', "config"),
        (b'{"request": {"command": "reload", "target": "a"}}', "target"),
        (b'{"request": {"command": "next"}, "config": "c", "x": 1}', "x"),
        (b'{"request": {"command": "next", "x": 1}, "config": "c"}', "x"),
        (b"\xff", "Invalid JSON"),
    ],
)
def test_bad_requests_are_rejected(line: bytes, problem: str) -> None:
    with pytest.raises(ProtocolError, match="Malformed request") as error:
        ipc.decode_request(line)
    assert problem in str(error.value)


def test_bad_reply_is_rejected() -> None:
    with pytest.raises(ProtocolError, match="Malformed reply"):
        ipc.decode_reply(b'{"code": 0, "out": "text"}')
    with pytest.raises(ProtocolError, match="Malformed event"):
        ipc.decode_event(b'{"event": "later_added"}')


def test_no_app_running(name: str, config: Path) -> None:
    envelope = Envelope(request=Request(command="status"), config=config)
    assert ipc.send(name, envelope) is None


def test_request_and_reply(
    qtbot: QtBot, server: CommandServer, name: str, config: Path
) -> None:
    # pylint: disable=unused-argument
    reply = ipc.decode_reply(send(qtbot, name, "next", config).encode())
    assert reply == PrevResult(playlist="walls", change=CHANGE)


def test_another_config_is_refused(
    qtbot: QtBot, server: CommandServer, name: str, tmp_path: Path
) -> None:
    # pylint: disable=unused-argument
    reply = ipc.decode_reply(
        send(qtbot, name, "next", tmp_path / "other.toml").encode()
    )
    assert isinstance(reply, ErrorReply) and reply.error.kind == "wrong_config"
    assert "The running app uses" in reply.error.message


def test_malformed_request_gets_an_error_reply(
    qtbot: QtBot, server: CommandServer, name: str
) -> None:
    # pylint: disable=unused-argument
    result = run_python(qtbot, RAW, name, '{"v": 2, "command": "rm -rf"}')
    reply = ipc.decode_reply(result.stdout.strip().encode())
    assert isinstance(reply, ErrorReply) and reply.error.kind == "protocol"
    assert "Malformed request" in reply.error.message


def test_too_long_request_gets_an_error_reply(
    qtbot: QtBot, server: CommandServer, name: str
) -> None:
    # pylint: disable=unused-argument
    socket = connect(qtbot, name, b"x" * (ipc.MAX_MESSAGE + 1))
    [line] = read_lines(qtbot, socket, 1)
    assert ipc.decode_reply(line) == ErrorReply.make("protocol", "Request too long")


def test_unanswered_request_times_out(qtbot: QtBot, name: str, config: Path) -> None:
    server = CommandServer(name, config)  # nobody answers
    server.listen()
    try:
        output = send(qtbot, name, "status", config, timeout=0.5)
    finally:
        server.close()
    assert output == "ERROR The running app did not answer in time"


def test_subscribers_get_events_until_the_server_closes(
    qtbot: QtBot, server: CommandServer, name: str, config: Path
) -> None:
    welcome = OverviewChangedEvent(overview=Overview(paused="idle"))
    server.subscribed.connect(lambda c: server.send_event(c, welcome))
    line = ipc.encode(Envelope(request=Subscribe(), config=config))
    first = connect(qtbot, name, line)
    second = connect(qtbot, name, line)
    assert read_lines(qtbot, first, 1) == [ipc.encode(welcome).strip()]
    assert read_lines(qtbot, second, 1) == [ipc.encode(welcome).strip()]

    server.broadcast(ChangedEvent(change=CHANGE))
    for socket in (first, second):
        [event] = read_lines(qtbot, socket, 1)
        assert ipc.decode_event(event) == ChangedEvent(change=CHANGE)

    # A command on another connection still gets its one reply.
    reply = ipc.decode_reply(send(qtbot, name, "next", config).encode())
    assert isinstance(reply, PrevResult)

    with qtbot.waitSignal(first.disconnected):
        server.close()


def test_subscription_to_another_config_is_refused(
    qtbot: QtBot, server: CommandServer, name: str, tmp_path: Path
) -> None:
    subscribed: list[int] = []
    server.subscribed.connect(subscribed.append)
    line = ipc.encode(Envelope(request=Subscribe(), config=tmp_path / "other.toml"))
    [reply] = read_lines(qtbot, connect(qtbot, name, line), 1)
    decoded = ipc.decode_event(reply)
    assert isinstance(decoded, ErrorReply) and decoded.error.kind == "wrong_config"
    assert not subscribed


def test_a_subscriber_that_stops_reading_is_dropped(
    qtbot: QtBot,
    server: CommandServer,
    name: str,
    config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subscribed: list[int] = []
    server.subscribed.connect(subscribed.append)
    socket = connect(
        qtbot, name, ipc.encode(Envelope(request=Subscribe(), config=config))
    )
    qtbot.waitUntil(lambda: bool(subscribed))
    monkeypatch.setattr(ipc, "MAX_BACKLOG", -1)  # anything unsent is too much
    with qtbot.waitSignal(socket.disconnected):
        server.broadcast(ChangedEvent(change=CHANGE))


def test_line_request_answers_without_blocking(
    qtbot: QtBot, server: CommandServer, name: str, config: Path, owner: QObject
) -> None:
    # pylint: disable=unused-argument
    answers: list[bytes] = []
    line = ipc.encode(Envelope(request=Request(command="next"), config=config))
    LineRequest(
        name, line, answers.append, lambda *_: pytest.fail("failed"), parent=owner
    )
    qtbot.waitUntil(lambda: bool(answers))
    assert ipc.decode_reply(answers[0]) == PrevResult(playlist="walls", change=CHANGE)


def test_line_request_reports_no_server(
    qtbot: QtBot, name: str, owner: QObject
) -> None:
    failures: list[tuple[bool, str]] = []
    LineRequest(
        name,
        b"{}\n",
        lambda _: pytest.fail("answered"),
        lambda *failure: failures.append(failure),
        parent=owner,
    )
    qtbot.waitUntil(lambda: bool(failures))
    assert failures[0][0] is True  # not running


def test_line_request_times_out(
    qtbot: QtBot, name: str, config: Path, owner: QObject
) -> None:
    server = CommandServer(name, config)  # nobody answers
    server.listen()
    failures: list[tuple[bool, str]] = []
    try:
        line = ipc.encode(Envelope(request=Request(command="status"), config=config))
        LineRequest(
            name,
            line,
            lambda _: pytest.fail("answered"),
            lambda *failure: failures.append(failure),
            parent=owner,
            timeout_s=0.2,
        )
        qtbot.waitUntil(lambda: bool(failures))
    finally:
        server.close()
    assert failures == [(False, "The running app did not answer in time")]
