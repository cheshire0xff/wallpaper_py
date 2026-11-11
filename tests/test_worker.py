"""The service on its worker thread, driven through ServiceHost like the app does."""

import threading
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from wallpaper_py.app.worker import ServiceHost, ServiceSettings
from wallpaper_py.commands import Overview, Reply, Request

from .fakes import FakeBackend, FakePresence, make_monitor, write_config


@pytest.fixture(name="backend")
def fixture_backend() -> FakeBackend:
    return FakeBackend([make_monitor("Main")])


@pytest.fixture(name="config")
def fixture_config(tmp_path: Path) -> Path:
    return write_config(tmp_path)


@pytest.fixture(name="host")
def fixture_host(config: Path, backend: FakeBackend) -> Iterator[ServiceHost]:
    host = ServiceHost(
        ServiceSettings(
            config_path=config,
            backend_factory=lambda: backend,
            presence_factory=FakePresence,
            thread_scope=nullcontext,
            tick_period=60.0,  # only the first tick and explicit ones
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
