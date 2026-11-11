"""Opening a window: the UI process's own socket, and starting one."""

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
    WindowKind,
    WindowOpener,
    ui_server_name,
)

from .clients import run_python

# argv: server name, window, config. Prints SHOWN, NONE or ERROR <message>.
SHOW = """
import sys
from pathlib import Path
from wallpaper_py.app.ui.launcher import ShowRequest, show_in_running_ui
from wallpaper_py.core.model import WallpaperError
name, window, config = sys.argv[1:4]
try:
    shown = show_in_running_ui(name, ShowRequest(window=window, config=Path(config)))
except WallpaperError as err:
    print("ERROR", err)
else:
    print("SHOWN" if shown else "NONE")
"""


@pytest.fixture(name="name")
def fixture_name(tmp_path: Path) -> str:
    return ui_server_name(tmp_path)


@pytest.fixture(name="config")
def fixture_config(tmp_path: Path) -> Path:
    return tmp_path / "config.toml"


@pytest.fixture(name="server")
def fixture_server(name: str, config: Path) -> Iterator[UiServer]:
    server = UiServer(name, config)
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


def ui_command(window: WindowKind) -> list[str]:
    return ["wallpaper", "ui", window]


def test_name_is_next_to_the_apps(tmp_path: Path) -> None:
    assert ui_server_name(tmp_path) == ipc.server_name(tmp_path) + "-ui"


def test_opener_asks_the_running_ui(
    qtbot: QtBot,
    server: UiServer,
    name: str,
    config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = Started(monkeypatch)
    owner = QObject()
    opener = WindowOpener(name, config, ui_command, parent=owner)
    with qtbot.waitSignal(server.show_requested) as shown:
        opener.open("library")
    assert shown.args == ["library"]
    qtbot.wait(50)  # the reply arrives; nothing is started
    assert not started.commands


def test_opener_starts_a_ui_if_none_answers(
    qtbot: QtBot, name: str, config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = Started(monkeypatch)
    owner = QObject()
    WindowOpener(name, config, ui_command, parent=owner).open("layout")
    qtbot.waitUntil(lambda: bool(started.commands))
    assert started.commands == [["wallpaper", "ui", "layout"]]


def test_ui_refuses_another_config(
    qtbot: QtBot,
    server: UiServer,
    name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pylint: disable=unused-argument
    started = Started(monkeypatch)
    owner = QObject()
    opener = WindowOpener(name, tmp_path / "other.toml", ui_command, parent=owner)
    with qtbot.waitSignal(opener.failed) as failed:
        opener.open("layout")
    assert "The open windows use" in failed.args[0]
    assert not started.commands


def test_malformed_requests_get_an_error(
    qtbot: QtBot, server: UiServer, name: str
) -> None:
    # pylint: disable=unused-argument
    answers: list[bytes] = []
    owner = QObject()
    line = b'{"v": 2, "window": "settings", "config": "c"}\n'
    ipc.LineRequest(
        name, line, answers.append, lambda *_: pytest.fail("failed"), parent=owner
    )
    qtbot.waitUntil(lambda: bool(answers))
    error = ShowReply.model_validate_json(answers[0]).error
    assert error is not None and error.startswith("Malformed request: window")


def test_blocking_show_from_a_starting_ui(
    qtbot: QtBot, server: UiServer, name: str, config: Path, tmp_path: Path
) -> None:
    def show(window: str, config_path: Path) -> str:
        result = run_python(qtbot, SHOW, name, window, str(config_path))
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    with qtbot.waitSignal(server.show_requested) as shown:
        assert show("layout", config) == "SHOWN"
    assert shown.args == ["layout"]
    assert show("layout", tmp_path / "other.toml").startswith(
        "ERROR The open windows use"
    )
    server.close()
    assert show("layout", config) == "NONE"


def test_show_request_round_trip(config: Path) -> None:
    request = ShowRequest(window="library", config=config)
    assert ShowRequest.model_validate_json(ipc.encode(request)) == request
