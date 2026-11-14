"""Opening the window on a page: the UI process's own socket, starting one."""

from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from PySide6.QtCore import QObject
from pytestqt.qtbot import QtBot

from wallpaper_py.app import ipc
from wallpaper_py.app.ui import launcher
from wallpaper_py.app.ui.launcher import (
    ShowReply,
    ShowRequest,
    UiServer,
    PageName,
    WindowOpener,
    ui_server_name,
)

from .clients import run_python

# argv: server name, page. Prints SHOWN, NONE or ERROR <message>.
SHOW = """
import sys
from wallpaper_py.app.ui.launcher import ShowRequest, show_in_running_ui
from wallpaper_py.core.model import WallpaperError
name, page = sys.argv[1:3]
try:
    shown = show_in_running_ui(name, ShowRequest(page=page))
except WallpaperError as err:
    print("ERROR", err)
else:
    print("SHOWN" if shown else "NONE")
"""


@pytest.fixture(name="name")
def fixture_name(tmp_path: Path) -> str:
    return ui_server_name(tmp_path)


@pytest.fixture(name="server")
def fixture_server(name: str) -> Iterator[UiServer]:
    server = UiServer(name)
    server.listen()
    yield server
    server.close()


class Started:
    """Records what `start_detached` would have started."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.commands: list[list[str]] = []
        monkeypatch.setattr(launcher, "start_detached", self.record)

    def record(self, command: Sequence[str]) -> None:
        self.commands.append(list(command))


def ui_command(page: PageName) -> list[str]:
    return ["wallpaper", "ui", page]


def test_name_is_next_to_the_apps(tmp_path: Path) -> None:
    assert ui_server_name(tmp_path) == ipc.server_name(tmp_path) + "-ui"


def test_opener_asks_the_running_ui(
    qtbot: QtBot,
    server: UiServer,
    name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = Started(monkeypatch)
    owner = QObject()
    opener = WindowOpener(name, ui_command, parent=owner)
    with qtbot.waitSignal(server.show_requested) as shown:
        opener.open("library")
    assert shown.args == ["library"]
    qtbot.wait(50)  # the reply arrives; nothing is started
    assert not started.commands


def test_opener_starts_a_ui_if_none_answers(
    qtbot: QtBot, name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = Started(monkeypatch)
    owner = QObject()
    WindowOpener(name, ui_command, parent=owner).open("home")
    qtbot.waitUntil(lambda: bool(started.commands))
    assert started.commands == [["wallpaper", "ui", "home"]]


def test_malformed_requests_get_an_error(
    qtbot: QtBot, server: UiServer, name: str
) -> None:
    # pylint: disable=unused-argument
    answers: list[bytes] = []
    owner = QObject()
    line = b'{"v": 3, "page": "kitchen"}\n'
    ipc.LineRequest(
        name, line, answers.append, lambda *_: pytest.fail("failed"), parent=owner
    )
    qtbot.waitUntil(lambda: bool(answers))
    error = ShowReply.model_validate_json(answers[0]).error
    assert error is not None and error.startswith("Malformed request: page")


def test_another_protocol_version_is_refused(
    qtbot: QtBot, server: UiServer, name: str
) -> None:
    # pylint: disable=unused-argument
    answers: list[bytes] = []
    owner = QObject()
    line = b'{"v": 1, "page": "home"}\n'
    ipc.LineRequest(
        name, line, answers.append, lambda *_: pytest.fail("failed"), parent=owner
    )
    qtbot.waitUntil(lambda: bool(answers))
    error = ShowReply.model_validate_json(answers[0]).error
    assert error is not None and "speaks protocol" in error


def test_blocking_show_from_a_starting_ui(
    qtbot: QtBot, server: UiServer, name: str
) -> None:
    def show(page: str) -> str:
        result = run_python(qtbot, SHOW, name, page)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    with qtbot.waitSignal(server.show_requested) as shown:
        assert show("settings") == "SHOWN"
    assert shown.args == ["settings"]
    server.close()
    assert show("home") == "NONE"


def test_show_request_round_trip() -> None:
    request = ShowRequest(page="library")
    assert ShowRequest.model_validate_json(ipc.encode(request)) == request
