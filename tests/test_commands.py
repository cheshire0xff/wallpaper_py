"""Commands as data (shared by the CLI and the app) and the tray's overview."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from wallpaper_py.commands import (
    Command,
    MonitorView,
    Overview,
    PlaylistView,
    Reply,
    Request,
    execute,
    open_service,
    overview,
)
from wallpaper_py.core.service import PlaylistService

from .fakes import FakeBackend, FakePresence, make_images, make_monitor

CONFIG = """
pause_when_idle = false

[playlists.walls]
directory = 'walls'
monitors = ["Main", "Side"]
order = "sorted"
interval = "10m"

[playlists.strip]
directory = 'strip'
monitors = ["Strip"]
interval = "10m"
"""


@pytest.fixture(name="backend")
def fixture_backend() -> FakeBackend:
    return FakeBackend(
        [
            make_monitor("Main"),
            make_monitor("Side", attached=False),
            # "Strip" is not listed at all
        ]
    )


@pytest.fixture(name="service")
def fixture_service(tmp_path: Path, backend: FakeBackend) -> Iterator[PlaylistService]:
    make_images(tmp_path / "walls", "a.jpg", "b.jpg")
    make_images(tmp_path / "strip", "s.jpg")
    config = tmp_path / "config.toml"
    config.write_text(CONFIG, encoding="utf-8")
    with open_service(config, backend, FakePresence()) as service:
        yield service


def run(service: PlaylistService, command: Command, target: str | None = None) -> Reply:
    return execute(service, Request(command, target)).reply


def test_unknown_command_is_rejected() -> None:
    with pytest.raises(ValueError):
        Request("delete")  # type: ignore[arg-type]


def test_commands(service: PlaylistService) -> None:
    assert run(service, "next", "walls") == Reply(0, out="walls: a.jpg on Main")
    assert run(service, "next", "main").out == "walls: b.jpg on Main"
    assert run(service, "prev", "walls").out == "walls: a.jpg on Main"
    assert run(service, "fav", "walls").out.startswith("Added ")
    assert run(service, "fav", "walls").out == "a.jpg is already a favourite"
    assert run(service, "unfav", "walls").out.startswith("Removed ")
    assert run(service, "unfav", "walls").out == "a.jpg is not a favourite"
    remove = run(service, "remove", "walls").out.splitlines()
    assert "(not deleted)" in remove[0] and remove[1] == "walls: b.jpg on Main"


def test_commands_that_change_wallpapers_return_the_change(
    service: PlaylistService,
) -> None:
    def change(command: Command) -> tuple[str, str] | None:
        outcome = execute(service, Request(command, "walls"))
        assert outcome.reply.code == 0, outcome.reply.err
        if outcome.change is None:
            return None
        assert outcome.change.playlist == "walls"
        return outcome.change.image.name, outcome.change.reason

    assert change("next") == ("a.jpg", "next")
    assert change("next") == ("b.jpg", "next")
    assert change("prev") == ("a.jpg", "previous")
    assert change("prev") is None  # nothing earlier
    assert change("fav") is None and change("unfav") is None
    assert change("remove") == ("b.jpg", "next")
    assert execute(service, Request("status")).change is None


def test_errors_become_a_reply(service: PlaylistService) -> None:
    outcome = execute(service, Request("next"))  # two playlists: name one
    assert outcome.change is None
    reply = outcome.reply
    assert reply.code == 1 and reply.err.startswith("error: Name a playlist")
    assert run(service, "next", "strip").err.startswith("error: None of strip's")


def test_status(service: PlaylistService) -> None:
    reply = run(service, "status")
    assert reply.code == 0
    assert "Main (attached), Side (detached)" in reply.out
    assert "Strip (not found)" in reply.out
    assert reply.out.endswith("Rotation active")


def test_overview(service: PlaylistService, tmp_path: Path) -> None:
    assert overview(service) == Overview(
        playlists=(
            PlaylistView(
                "walls",
                (MonitorView("Main", True), MonitorView("Side", False)),
                current=None,
                favourite=False,
            ),
            PlaylistView(
                "strip", (MonitorView("Strip", None),), current=None, favourite=False
            ),
        )
    )
    run(service, "next", "walls")
    run(service, "fav", "walls")
    walls = overview(service).playlists[0]
    assert walls.current == tmp_path / "walls" / "a.jpg"
    assert walls.favourite and walls.attached
    assert not overview(service).playlists[1].attached
