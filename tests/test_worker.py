"""
The service on its worker thread, driven like the daemon does: through
ServiceThread (the tray) and through its command socket.
"""

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtNetwork import QLocalSocket
from pytestqt.qtbot import QtBot

from wallpaper_py.app import ipc
from wallpaper_py.app.worker import ServiceSettings, ServiceThread
from wallpaper_py.core import paths
from wallpaper_py.core.model import ImageMode, Look
from wallpaper_py.core.service import Change
from wallpaper_py.core.settings import Settings
from wallpaper_py.protocol import (
    ChangedEvent,
    CommandRequest,
    Envelope,
    ErrorReply,
    HoldResult,
    MarkRequest,
    MarkResult,
    MarksChangedEvent,
    NextResult,
    Overview,
    OverviewChangedEvent,
    PickRequest,
    PickResult,
    PrevResult,
    RemoveResult,
    Reply,
    Request,
    StatusRequest,
    SaveSettingsRequest,
    SaveSettingsResult,
    SettingsRequest,
    SettingsResult,
    Subscribe,
    target_request,
)

from .fakes import (
    FakeBackend,
    FakePresence,
    make_monitor,
    save_settings,
    settled,
    write_settings,
)
from .hosts import connect, make_service_thread, read_lines


@pytest.fixture(name="backend")
def fixture_backend() -> FakeBackend:
    return FakeBackend([make_monitor("Main")])


@pytest.fixture(name="settings")
def fixture_settings(tmp_path: Path) -> Settings:
    return write_settings(tmp_path)


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


@pytest.fixture(name="service_thread")
def fixture_service_thread(
    settings: Settings, backend: FakeBackend, hold: Hold, name: str
) -> Iterator[ServiceThread]:
    del settings  # saved before the daemon starts

    def backend_factory() -> FakeBackend:
        hold()
        return backend

    service_thread = make_service_thread(backend_factory, name)
    yield service_thread
    assert service_thread.shutdown(5_000)


def start(qtbot: QtBot, service_thread: ServiceThread) -> Overview:
    with qtbot.waitSignal(service_thread.overview_changed) as blocker:
        service_thread.start()
    overview: Overview = blocker.args[0]
    return overview


def submit(
    qtbot: QtBot, service_thread: ServiceThread, request: CommandRequest
) -> Reply:
    replies: list[Reply] = []
    service_thread.submit(request, replies.append)
    qtbot.waitUntil(lambda: bool(replies))
    return replies[0]


def ask(qtbot: QtBot, name: str, request: CommandRequest | Subscribe) -> QLocalSocket:
    """Send a request over the socket without waiting for the reply."""
    return connect(qtbot, name, ipc.encode(Envelope(request=request)))


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


def send(qtbot: QtBot, name: str, request: CommandRequest) -> Reply:
    return answer(qtbot, ask(qtbot, name, request))


def tick(qtbot: QtBot, service_thread: ServiceThread) -> None:
    with qtbot.waitSignal(service_thread.ticked):
        service_thread.request_tick()


def test_first_tick_runs_on_the_worker_thread(
    qtbot: QtBot, service_thread: ServiceThread, backend: FakeBackend, tmp_path: Path
) -> None:
    threads: set[int] = set()
    backend.before_monitors = lambda _: threads.add(threading.get_ident())
    overview = start(qtbot, service_thread)
    assert overview.playlists[0].current == tmp_path / "walls" / "a.jpg"
    assert threads and threading.get_ident() not in threads
    assert service_thread.overview == overview


def test_commands_are_answered_in_order(
    qtbot: QtBot, service_thread: ServiceThread
) -> None:
    start(qtbot, service_thread)
    replies: list[Reply] = []
    for command in ("next", "next", "prev", "fav"):
        service_thread.submit(target_request(command, None), replies.append)
    qtbot.waitUntil(lambda: len(replies) == 4)
    assert [shown(r) for r in replies[:3]] == ["b.jpg", "c.jpg", "b.jpg"]
    assert isinstance(replies[3], MarkResult) and replies[3].changed
    qtbot.waitUntil(
        lambda: service_thread.overview is not None
        and service_thread.overview.playlists[0].favourite
    )


def test_tray_commands_announce_changes(
    qtbot: QtBot, service_thread: ServiceThread
) -> None:
    start(qtbot, service_thread)
    with qtbot.waitSignal(service_thread.changed) as blocker:
        service_thread.submit(Request(command="next"), lambda _: None)
    change: Change = blocker.args[0]
    assert (change.image.name, change.reason) == ("b.jpg", "next")


def test_socket_commands_run_on_the_service_thread(
    qtbot: QtBot, service_thread: ServiceThread, backend: FakeBackend, name: str
) -> None:
    start(qtbot, service_thread)
    threads: set[int] = set()
    backend.before_monitors = lambda _: threads.add(threading.get_ident())
    with qtbot.waitSignal(service_thread.changed) as blocker:
        reply = send(qtbot, name, Request(command="next"))
    assert shown(reply) == "b.jpg"
    assert blocker.args[0].image.name == "b.jpg"  # announced like a tick's
    assert threads and threading.get_ident() not in threads


def test_socket_answers_once_the_service_is_open(
    qtbot: QtBot, service_thread: ServiceThread, name: str, hold: Hold
) -> None:
    """A request while the service is still opening: answered once it is."""
    hold.active = True
    service_thread.start()
    qtbot.waitUntil(hold.entered.is_set)
    pending = ask(qtbot, name, Request(command="next"))
    hold.release.set()
    assert shown(answer(qtbot, pending)) == "b.jpg"


def test_subscribers_follow_events(
    qtbot: QtBot, service_thread: ServiceThread, name: str
) -> None:
    overview = start(qtbot, service_thread)
    subscriber = ask(qtbot, name, Subscribe())
    # First the current overview, then events as they happen.
    [welcome] = read_lines(qtbot, subscriber, 1)
    assert ipc.decode_event(welcome) == OverviewChangedEvent(overview=overview)

    assert shown(send(qtbot, name, Request(command="next"))) == "b.jpg"
    changed, refreshed = (ipc.decode_event(l) for l in read_lines(qtbot, subscriber, 2))
    assert isinstance(changed, ChangedEvent)
    assert (changed.change.image.name, changed.change.reason) == ("b.jpg", "next")
    assert isinstance(refreshed, OverviewChangedEvent)
    current = refreshed.overview.playlists[0].current
    assert current is not None and current.name == "b.jpg"

    # The daemon stopping ends the subscription.
    with qtbot.waitSignal(subscriber.disconnected):
        assert service_thread.shutdown(5_000)


def test_list_changes_reach_subscribers(
    qtbot: QtBot, service_thread: ServiceThread, name: str
) -> None:
    """By a command, or by hand (seen on the next tick)."""
    start(qtbot, service_thread)
    subscriber = ask(qtbot, name, Subscribe())
    read_lines(qtbot, subscriber, 1)  # the current overview
    image = paths.data_dir() / "elsewhere" / "x.jpg"
    reply = send(qtbot, name, MarkRequest(command="remove", image=image))
    assert isinstance(reply, RemoveResult) and reply.changed
    [line] = read_lines(qtbot, subscriber, 1)
    assert ipc.decode_event(line) == MarksChangedEvent()

    (paths.data_dir() / "favourites.txt").write_text(f"{image}\n", encoding="utf-8")
    tick(qtbot, service_thread)
    [line] = read_lines(qtbot, subscriber, 1)
    assert ipc.decode_event(line) == MarksChangedEvent()


def test_extra_tick_on_request(qtbot: QtBot, service_thread: ServiceThread) -> None:
    start(qtbot, service_thread)
    tick(qtbot, service_thread)


def test_a_bad_list_file_at_start_then_fixed(
    qtbot: QtBot,
    service_thread: ServiceThread,
    caplog: pytest.LogCaptureFixture,
    name: str,
) -> None:
    """
    The service can't open; each tick tries again, and it runs once fixed.
    Subscribers then read the lists again: they couldn't either.
    """
    listed = paths.data_dir() / "favourites.txt"
    listed.parent.mkdir(parents=True, exist_ok=True)
    listed.write_text("a.jpg\n", encoding="utf-8-sig")
    settled(listed)
    overview = start(qtbot, service_thread)
    assert overview.error is not None and "byte order mark" in overview.error
    subscriber = ask(qtbot, name, Subscribe())
    read_lines(qtbot, subscriber, 1)  # the overview with the error
    assert "byte order mark" in error(
        submit(qtbot, service_thread, Request(command="next"))
    )
    tick(qtbot, service_thread)  # tries again: the same error, logged once
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len([r for r in errors if "byte order mark" in r.getMessage()]) == 1

    listed.write_text("a.jpg\n", encoding="utf-8")
    with qtbot.waitSignal(service_thread.overview_changed) as blocker:
        service_thread.request_tick()
    assert blocker.args[0].error is None
    events = [ipc.decode_event(line) for line in read_lines(qtbot, subscriber, 2)]
    assert MarksChangedEvent() in events
    assert shown(submit(qtbot, service_thread, Request(command="next"))) == "b.jpg"


def test_unexpected_tick_errors_are_survived(
    qtbot: QtBot, service_thread: ServiceThread, backend: FakeBackend
) -> None:
    start(qtbot, service_thread)

    def boom(_: int) -> None:
        raise OSError("COM hiccup")

    backend.before_monitors = boom
    tick(qtbot, service_thread)
    backend.before_monitors = None
    assert shown(submit(qtbot, service_thread, Request(command="next"))) == "b.jpg"


def test_slow_commands_and_ticks_are_logged_with_their_time(
    qtbot: QtBot,
    service_thread: ServiceThread,
    backend: FakeBackend,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """What a click could wait for shows in the daemon's log."""
    start(qtbot, service_thread)
    caplog.set_level(logging.INFO, logger="wallpaper_py.app.worker")
    submit(qtbot, service_thread, StatusRequest())
    assert "took" not in caplog.text  # a quick one: not logged
    backend.before_monitors = lambda _: time.sleep(0.15)
    submit(qtbot, service_thread, Request(command="next"))
    tick(qtbot, service_thread)
    backend.before_monitors = None
    slow = [r.getMessage() for r in caplog.records if "took" in r.getMessage()]
    assert [m.split(" took ")[0] for m in slow] == ["Command next", "Tick"]
    assert all(int(m.split(" took ")[1].removesuffix(" ms")) >= 150 for m in slow)


def test_shutdown_before_start_is_harmless() -> None:
    service_thread = ServiceThread(
        ServiceSettings(lambda: FakeBackend([]), FakePresence, nullcontext)
    )
    assert service_thread.shutdown()


def test_hold_reaches_the_tray_and_subscribers(
    qtbot: QtBot, service_thread: ServiceThread, name: str
) -> None:
    start(qtbot, service_thread)
    subscriber = ask(qtbot, name, Subscribe())
    read_lines(qtbot, subscriber, 1)  # the current overview
    with qtbot.waitSignal(service_thread.overview_changed) as blocker:
        reply = submit(qtbot, service_thread, Request(command="hold"))
    assert reply == HoldResult(
        command="hold", playlist="walls", held=True, changed=True
    )
    assert blocker.args[0].playlists[0].held
    [line] = read_lines(qtbot, subscriber, 1)
    event = ipc.decode_event(line)
    assert isinstance(event, OverviewChangedEvent) and event.overview.playlists[0].held


def test_hold_survives_a_daemon_restart(
    qtbot: QtBot, service_thread: ServiceThread, backend: FakeBackend, name: str
) -> None:
    start(qtbot, service_thread)
    submit(qtbot, service_thread, Request(command="hold"))
    assert service_thread.shutdown(5_000)
    again = make_service_thread(lambda: backend, name)
    try:
        assert start(qtbot, again).playlists[0].held
    finally:
        assert again.shutdown(5_000)


def test_pick_over_the_socket(
    qtbot: QtBot, service_thread: ServiceThread, name: str, tmp_path: Path
) -> None:
    start(qtbot, service_thread)
    image = tmp_path / "elsewhere" / "x.png"
    image.parent.mkdir()
    Image.new("RGB", (8, 8)).save(image)
    with qtbot.waitSignal(service_thread.changed) as blocker:
        reply = send(qtbot, name, PickRequest(image=image))
    assert isinstance(reply, PickResult) and reply.change.image == image
    assert blocker.args[0].reason == "picked"
    assert shown(send(qtbot, name, Request(command="prev"))) == "a.jpg"


def test_saving_settings_answers_with_warnings_or_why_not(
    qtbot: QtBot, service_thread: ServiceThread, name: str, settings: Settings
) -> None:
    start(qtbot, service_thread)
    assert send(qtbot, name, SettingsRequest()) == SettingsResult(
        settings=settings, revision=1
    )

    # Valid but probably a mistake: the reply carries the warning.
    idle = settings.model_copy(update={"pause_when_idle": 1200.0})
    reply = send(qtbot, name, SaveSettingsRequest(settings=idle, revision=1))
    assert isinstance(reply, SaveSettingsResult) and reply.revision == 2
    assert [w.split(" (")[0] for w in reply.warnings] == ["walls: the interval"]

    # Edited on an old revision: refused, nothing saved.
    reply = send(qtbot, name, SaveSettingsRequest(settings=settings, revision=1))
    assert isinstance(reply, ErrorReply) and reply.error.kind == "stale_settings"
    # A value that can't be used: refused, nothing saved.
    walls = settings.playlists[0].model_copy(update={"interval": 1.0})
    bad = settings.model_copy(update={"playlists": (walls,)})
    reply = submit(qtbot, service_thread, SaveSettingsRequest(settings=bad, revision=2))
    assert isinstance(reply, ErrorReply) and reply.error.kind == "invalid_settings"
    assert send(qtbot, name, SettingsRequest()) == SettingsResult(
        settings=idle, revision=2
    )


def test_a_list_file_that_is_not_utf8_stops_the_service(
    qtbot: QtBot, service_thread: ServiceThread, name: str, tmp_path: Path
) -> None:
    start(qtbot, service_thread)
    listed = tmp_path / "user" / "data" / "to-remove.txt"
    listed.parent.mkdir(parents=True, exist_ok=True)

    # It goes bad while the daemon runs: the next tick stops the service.
    listed.write_text("a.jpg\n", encoding="utf-16")
    settled(listed)
    with qtbot.waitSignal(service_thread.overview_changed) as blocker:
        service_thread.request_tick()
    assert "not UTF-8" in blocker.args[0].error
    reply = send(qtbot, name, Request(command="next"))
    assert isinstance(reply, ErrorReply) and reply.error.kind == "config_error"

    # Still bad at the next tick: the same error; fixed: running again.
    tick(qtbot, service_thread)
    reply = send(qtbot, name, Request(command="next"))
    assert isinstance(reply, ErrorReply) and reply.error.kind == "config_error"
    listed.write_text("", encoding="utf-8")
    tick(qtbot, service_thread)
    assert shown(send(qtbot, name, Request(command="next"))) == "b.jpg"


def test_a_renamed_playlist_keeps_its_state(
    qtbot: QtBot, service_thread: ServiceThread, name: str, settings: Settings
) -> None:
    """Its state goes by its id, which a rename keeps: nothing to move."""
    start(qtbot, service_thread)
    assert shown(send(qtbot, name, Request(command="next"))) == "b.jpg"
    art = settings.playlists[0].model_copy(update={"name": "art"})
    edit = settings.model_copy(update={"playlists": (art,)})
    with qtbot.waitSignal(service_thread.overview_changed) as blocker:
        reply = send(qtbot, name, SaveSettingsRequest(settings=edit, revision=1))
    assert isinstance(reply, SaveSettingsResult)
    [playlist] = blocker.args[0].playlists
    assert playlist.name == "art" and playlist.current.name == "b.jpg"
    # The history too: prev goes back, where a new playlist has none.
    assert shown(send(qtbot, name, Request(command="prev"))) == "a.jpg"


def test_no_settings_yet_runs_without_playlists(
    qtbot: QtBot, service_thread: ServiceThread
) -> None:
    """The first start: playlists are added in the settings window."""
    save_settings(Settings())
    overview = start(qtbot, service_thread)
    assert overview.error is None and overview.playlists == ()
    assert overview.monitors  # listed, for the settings window to assign
    assert "No playlists yet" in error(
        submit(qtbot, service_thread, Request(command="next"))
    )


def test_a_config_error_from_a_command_stops_the_service(
    qtbot: QtBot,
    service_thread: ServiceThread,
    name: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The same rule as for a tick: the service stops, the reply says why."""
    start(qtbot, service_thread)
    listed = paths.data_dir() / "to-remove.txt"
    listed.parent.mkdir(parents=True, exist_ok=True)
    listed.write_text("a.jpg\n", encoding="utf-8-sig")  # Notepad's BOM
    settled(listed)

    with qtbot.waitSignal(service_thread.overview_changed) as blocker:
        reply = send(qtbot, name, Request(command="next"))  # reads it
    assert isinstance(reply, ErrorReply) and reply.error.kind == "config_error"
    assert "byte order mark" in reply.error.message
    assert blocker.args[0].error == reply.error.message
    assert send(qtbot, name, StatusRequest()) == reply
    assert not [r for r in caplog.records if r.exc_info]  # no traceback: expected

    listed.write_text("", encoding="utf-8")
    tick(qtbot, service_thread)
    assert shown(send(qtbot, name, Request(command="next"))) == "b.jpg"


def test_saved_settings_show_before_the_reply(
    qtbot: QtBot, service_thread: ServiceThread, name: str, backend: FakeBackend
) -> None:
    """A monitor whose look changed is rendered again, then the reply goes."""
    start(qtbot, service_thread)
    current = send(qtbot, name, SettingsRequest())
    assert isinstance(current, SettingsResult)
    [main] = current.settings.monitors
    fit = main.model_copy(update={"look": Look(ImageMode.FIT)})
    edit = current.settings.model_copy(update={"monitors": (fit,)})
    calls = len(backend.set_calls)
    with qtbot.waitSignal(service_thread.changed) as blocker:
        reply = send(
            qtbot, name, SaveSettingsRequest(settings=edit, revision=current.revision)
        )
    assert reply == SaveSettingsResult(revision=current.revision + 1)
    assert len(backend.set_calls) == calls + 1  # before the reply
    assert blocker.args[0].reason == "restored"

    def fit_in_overview() -> bool:
        overview = service_thread.overview
        return overview is not None and overview.monitors[0].look == fit.look

    qtbot.waitUntil(fit_in_overview)
    again = send(qtbot, name, SettingsRequest())
    assert isinstance(again, SettingsResult) and again.settings == edit
