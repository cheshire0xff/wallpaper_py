"""The command socket: message format, the blocking client and the server."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from wallpaper_py.app import ipc
from wallpaper_py.app.ipc import CommandServer, Envelope, ProtocolError
from wallpaper_py.commands import Reply, Request

from .clients import RAW, SEND, run_python


@pytest.fixture(name="name")
def fixture_name(tmp_path: Path) -> str:
    return ipc.server_name(tmp_path)


@pytest.fixture(name="config")
def fixture_config(tmp_path: Path) -> Path:
    return tmp_path / "config.toml"


@pytest.fixture(name="server")
def fixture_server(name: str, config: Path) -> Iterator[CommandServer]:
    server = CommandServer(name, config)

    def answer(connection_id: int, request: Request) -> None:
        server.reply(connection_id, Reply(0, out=f"did {request.command}"))

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


def test_server_name_is_per_directory(tmp_path: Path) -> None:
    name = ipc.server_name(tmp_path)
    assert name == ipc.server_name(tmp_path / "x" / "..")
    assert name != ipc.server_name(tmp_path / "other")
    assert name.startswith("wallpaper_py-") and "/" not in name and "\\" not in name


def test_messages_round_trip(config: Path) -> None:
    envelope = Envelope(request=Request("next", "konachan"), config=config)
    assert ipc.decode_request(ipc.encode_request(envelope).strip()) == envelope
    reply = Reply(1, out="a\nb", err="error: x")
    assert ipc.decode_reply(ipc.encode_reply(reply)) == reply


@pytest.mark.parametrize(
    "line, problem",
    [
        (b"not json", "Invalid JSON"),
        (b"[]", "should be an object"),
        (b'{"v": 9, "request": {"command": "next"}, "config": "c"}', "protocol 1"),
        (b'{"request": {"command": "delete"}, "config": "c"}', "request.command"),
        (b'{"request": {"command": "next", "target": 3}, "config": "c"}', "target"),
        (b'{"request": {"command": "next"}}', "config"),
        (b'{"request": {"command": "next"}, "config": "c", "x": 1}', "x"),
        (b"\xff", "Invalid JSON"),
    ],
)
def test_bad_requests_are_rejected(line: bytes, problem: str) -> None:
    with pytest.raises(ProtocolError, match="Malformed request") as error:
        ipc.decode_request(line)
    assert problem in str(error.value)


def test_bad_reply_is_rejected() -> None:
    with pytest.raises(ProtocolError, match="Malformed reply"):
        ipc.decode_reply(b'{"code": "zero"}')


def test_no_app_running(name: str, config: Path) -> None:
    assert ipc.send(name, Envelope(request=Request("status"), config=config)) is None


def test_request_and_reply(
    qtbot: QtBot, server: CommandServer, name: str, config: Path
) -> None:
    # pylint: disable=unused-argument
    assert (
        send(qtbot, name, "next", config)
        == ipc.encode_reply(Reply(0, out="did next")).decode().strip()
    )


def test_another_config_is_refused(
    qtbot: QtBot, server: CommandServer, name: str, tmp_path: Path
) -> None:
    # pylint: disable=unused-argument
    reply = ipc.decode_reply(
        send(qtbot, name, "next", tmp_path / "other.toml").encode()
    )
    assert reply.code == 1 and "The running app uses" in reply.err


def test_malformed_request_gets_an_error_reply(
    qtbot: QtBot, server: CommandServer, name: str
) -> None:
    # pylint: disable=unused-argument
    result = run_python(qtbot, RAW, name, '{"v": 1, "command": "rm -rf"}')
    reply = ipc.decode_reply(result.stdout.strip().encode())
    assert reply.code == 1 and "Malformed request" in reply.err


def test_unanswered_request_times_out(qtbot: QtBot, name: str, config: Path) -> None:
    server = CommandServer(name, config)  # nobody answers
    server.listen()
    try:
        output = send(qtbot, name, "status", config, timeout=0.5)
    finally:
        server.close()
    assert output == "ERROR The running app did not answer in time"
