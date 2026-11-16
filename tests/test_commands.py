"""Commands run on the service: data in, data out; and the overview."""

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from PIL import Image
from pydantic import TypeAdapter, ValidationError

from wallpaper_py.commands import (
    Outcome,
    error_reply,
    execute,
    open_service,
    overview,
)
from wallpaper_py.core import paths
from wallpaper_py.core.model import ImageMode, Look, MonitorId, Rectangle
from wallpaper_py.core.service import (
    Change,
    ChangeReason,
    NoImagesError,
    PlaylistService,
)
from wallpaper_py.core.settings import (
    MonitorSettings,
    Order,
    PlaylistSettings,
    Settings,
)
from wallpaper_py.protocol import (
    CommandRequest,
    DesktopMonitor,
    Envelope,
    ErrorReply,
    Geometry,
    HoldResult,
    MarkRequest,
    MarkResult,
    MonitorView,
    NextResult,
    Overview,
    PickRequest,
    PickResult,
    PlaylistView,
    PrevResult,
    RemoveResult,
    Reply,
    Request,
    StatusRequest,
    ResetCycleRequest,
    ResetCycleResult,
    SaveSettingsRequest,
    SaveSettingsResult,
    SettingsRequest,
    SettingsResult,
    StatsRequest,
    StatsResult,
    StatusResult,
    TargetCommand,
    UpcomingRequest,
    UpcomingResult,
    target_request,
)

from .fakes import (
    BusyFile,
    FakeBackend,
    FakePresence,
    make_images,
    make_monitor,
    fake_id,
    save_settings,
)

STRIP_ID = MonitorId(fake_id("Strip"))  # not listed

MAIN = make_monitor("Main")
SIDE = make_monitor("Side", attached=False)
# In no playlist; a 4K at 150 % on Wayland: 2560x1440 of the desktop.
LEFT = replace(
    make_monitor("Left"), rect=Rectangle(-2560, 0, 0, 1440, pixels=(3840, 2160))
)


def example(tmp_path: Path, interval: float | None = 600.0) -> Settings:
    """walls (sorted) on Main and Side, strip on Strip (fit), every `interval`."""
    walls = PlaylistSettings(
        id="walls-id",
        name="walls",
        directory=tmp_path / "walls",
        order=Order.SORTED,
        interval=interval,
    )
    strip = PlaylistSettings(
        id="strip-id", name="strip", directory=tmp_path / "strip", interval=interval
    )
    return Settings(
        playlists=(walls, strip),
        monitors=(
            MonitorSettings(id=MAIN.id, playlist="walls-id"),
            MonitorSettings(id=SIDE.id, playlist="walls-id"),
            MonitorSettings(id=STRIP_ID, playlist="strip-id", look=Look(ImageMode.FIT)),
        ),
        pause_when_idle=None,
    )


@pytest.fixture(name="backend")
def fixture_backend() -> FakeBackend:
    return FakeBackend([MAIN, SIDE, LEFT])  # "Strip" is not listed at all


@pytest.fixture(name="service")
def fixture_service(tmp_path: Path, backend: FakeBackend) -> Iterator[PlaylistService]:
    make_images(tmp_path / "walls", "a.jpg", "b.jpg")
    make_images(tmp_path / "strip", "s.jpg")
    save_settings(example(tmp_path))
    with open_service(backend, FakePresence()) as service:
        yield service


def request(
    command: Literal["status"] | TargetCommand, target: str | None = None
) -> CommandRequest:
    if command == "status":
        return StatusRequest()
    return target_request(command, target)


def run(
    service: PlaylistService,
    command: Literal["status"] | TargetCommand,
    target: str | None = None,
) -> Reply:
    return execute(service, request(command, target)).reply


def walls_change(tmp_path: Path, name: str, reason: ChangeReason = "next") -> Change:
    # Side is detached already: only members detaching mid-change are "missed".
    return Change("walls-id", "walls", tmp_path / "walls" / name, reason, ("Main",))


def test_unknown_command_is_rejected() -> None:
    with pytest.raises(ValueError):
        Request(command="delete")  # type: ignore[arg-type]


def test_status_takes_no_target() -> None:
    with pytest.raises(ValueError):
        StatusRequest(target="walls")  # type: ignore[call-arg]


def test_a_playlist_is_found_by_its_id_or_its_name(service: PlaylistService) -> None:
    by_id = execute(service, Request(command="hold", target="walls-id")).reply
    assert by_id == HoldResult(
        command="hold", playlist="walls", held=True, changed=True
    )
    by_name = execute(service, Request(command="resume", target="WALLS")).reply
    assert by_name == HoldResult(
        command="resume", playlist="walls", held=False, changed=True
    )


def test_an_id_wins_over_a_name_that_looks_like_it(service: PlaylistService) -> None:
    walls = service.settings.playlists[0]
    other = service.settings.playlists[1].model_copy(update={"name": walls.id})
    settings = service.settings.model_copy(update={"playlists": (other, walls)})
    service.save_settings(settings, service.revision())
    assert service.find_playlist(walls.id).id == walls.id


def test_commands_reply_with_data(service: PlaylistService, tmp_path: Path) -> None:
    favourites = paths.data_dir() / "favourites.txt"
    a, b = tmp_path / "walls" / "a.jpg", tmp_path / "walls" / "b.jpg"

    def mark(
        command: Literal["fav", "unfav"], image: Path, changed: bool
    ) -> MarkResult:
        return MarkResult(
            command=command,
            playlist="walls",
            image=image,
            list_file=favourites,
            changed=changed,
        )

    assert run(service, "next", "walls") == NextResult(
        playlist="walls", change=walls_change(tmp_path, "a.jpg")
    )
    assert run(service, "next", "main") == NextResult(  # by monitor
        playlist="walls", change=walls_change(tmp_path, "b.jpg")
    )
    assert run(service, "prev", "walls") == PrevResult(
        playlist="walls", change=walls_change(tmp_path, "a.jpg", "previous")
    )
    assert run(service, "prev", "walls") == PrevResult(playlist="walls", change=None)
    assert run(service, "fav", "walls") == mark("fav", a, True)
    assert run(service, "fav", "walls") == mark("fav", a, False)
    assert run(service, "unfav", "walls") == mark("unfav", a, True)
    assert run(service, "unfav", "walls") == mark("unfav", a, False)
    assert run(service, "remove", "walls") == RemoveResult(
        playlist="walls",
        image=a,
        list_file=paths.data_dir() / "to-remove.txt",
        changed=True,
        change=walls_change(tmp_path, "b.jpg"),
    )
    assert b.is_file()  # listed, never deleted


def test_marks_by_image(service: PlaylistService, tmp_path: Path) -> None:
    """Any image, by path: no playlist, nothing shown needed, no change."""
    favourites = paths.data_dir() / "favourites.txt"
    to_remove = paths.data_dir() / "to-remove.txt"
    gone = tmp_path / "elsewhere" / "gone.jpg"  # need not exist

    def mark(command: Literal["fav", "unfav", "remove", "unremove"]) -> Outcome:
        return execute(service, MarkRequest(command=command, image=gone))

    def result(
        command: Literal["fav", "unfav", "unremove"], file: Path, changed: bool
    ) -> Outcome:
        return Outcome(
            MarkResult(
                command=command,
                playlist=None,
                image=gone,
                list_file=file,
                changed=changed,
            )
        )

    assert mark("fav") == result("fav", favourites, True)
    assert mark("fav") == result("fav", favourites, False)
    assert favourites.read_text(encoding="utf-8") == f"{gone}\n"
    assert mark("unfav") == result("unfav", favourites, True)
    assert mark("unfav") == result("unfav", favourites, False)
    for changed in (True, False):
        assert mark("remove") == Outcome(
            RemoveResult(
                playlist=None,
                image=gone,
                list_file=to_remove,
                changed=changed,
                change=None,
            )
        )
    assert to_remove.read_text(encoding="utf-8") == f"{gone}\n"
    assert mark("unremove") == result("unremove", to_remove, True)
    assert mark("unremove") == result("unremove", to_remove, False)
    assert to_remove.read_text(encoding="utf-8") == ""


def test_remove_by_image_moves_on_at_the_next_tick(
    service: PlaylistService, tmp_path: Path
) -> None:
    """Only listed, as if edited by hand: the playlist showing it moves on."""
    a = tmp_path / "walls" / "a.jpg"
    run(service, "next", "walls")
    outcome = execute(service, MarkRequest(command="remove", image=a))
    assert outcome.change is None
    assert service.current(service.find_playlist("walls")) == a
    assert [c.image.name for c in service.tick()] == ["b.jpg"]


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"command": "fav", "target": "walls", "image": "/a.jpg"}, "not both"),
        ({"command": "unremove", "target": "walls"}, "unremove needs an image"),
        ({"command": "fav", "image": "a.jpg"}, "absolute"),
        ({"command": "upcoming", "limit": 0}, "greater than or equal to 1"),
        ({"command": "upcoming", "limit": 1001}, "less than or equal to 1000"),
        ({"command": "upcoming", "limit": True}, "valid integer"),
        ({"command": "upcoming", "limit": "5"}, "valid integer"),
    ],
)
def test_mark_and_upcoming_requests_are_checked(
    fields: dict[str, object], message: str
) -> None:
    if fields.get("image") == "/a.jpg":
        fields = {**fields, "image": str(Path.cwd() / "a.jpg")}  # absolute here
    with pytest.raises(ValidationError, match=message):
        Envelope.model_validate({"request": fields})


def test_upcoming(service: PlaylistService, tmp_path: Path) -> None:
    """In order: walls is sorted; history ahead first, after prev."""
    walls = tmp_path / "walls"
    make_images(walls, "c.jpg")

    def upcoming(limit: int = 100) -> Reply:
        return execute(service, UpcomingRequest(target="walls", limit=limit)).reply

    assert upcoming() == UpcomingResult(
        playlist="walls", images=(walls / "a.jpg", walls / "b.jpg", walls / "c.jpg")
    )
    run(service, "next", "walls")
    run(service, "next", "walls")
    run(service, "prev", "walls")  # on a.jpg, b.jpg ahead in the history
    assert upcoming(2) == UpcomingResult(
        playlist="walls", images=(walls / "b.jpg", walls / "c.jpg")
    )
    execute(service, MarkRequest(command="remove", image=walls / "c.jpg"))
    reply = upcoming()
    assert isinstance(reply, UpcomingResult)
    # c.jpg is left out; the walk goes on after b.jpg and wraps around.
    assert [p.name for p in reply.images] == ["b.jpg", "a.jpg", "b.jpg"]
    error = execute(service, UpcomingRequest(target="nowhere")).reply
    assert isinstance(error, ErrorReply) and error.error.kind == "unknown_target"


def test_commands_that_change_wallpapers_return_the_change(
    service: PlaylistService,
) -> None:
    def change(command: TargetCommand) -> tuple[str, str] | None:
        outcome = execute(service, request(command, "walls"))
        assert not isinstance(outcome.reply, ErrorReply), outcome.reply
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
    assert execute(service, StatusRequest()).change is None


@pytest.mark.parametrize(
    "command, target, kind, message",
    [
        ("next", None, "unknown_target", "Name a playlist or monitor: walls, strip"),
        ("next", "nowhere", "unknown_target", "No monitor matches 'nowhere'"),
        ("next", "strip", "not_attached", "None of strip's monitors is attached"),
        ("fav", "walls", "nothing_shown", "walls has not shown an image yet"),
    ],
)
def test_errors_are_structured(
    service: PlaylistService,
    command: TargetCommand,
    target: str,
    kind: str,
    message: str,
) -> None:
    outcome = execute(service, request(command, target))
    assert outcome.change is None
    reply = outcome.reply
    assert isinstance(reply, ErrorReply)
    assert reply.error.kind == kind and reply.error.message.startswith(message)


def test_pick_hold_resume_reply_with_data(
    service: PlaylistService, tmp_path: Path
) -> None:
    image = tmp_path / "elsewhere" / "x.png"
    image.parent.mkdir()
    Image.new("RGB", (8, 8)).save(image)
    outcome = execute(service, PickRequest(target="walls", image=image))
    change = Change("walls-id", "walls", image, "picked", ("Main",))
    assert outcome == Outcome(PickResult(playlist="walls", change=change), change)

    assert run(service, "hold", "walls") == HoldResult(
        command="hold", playlist="walls", held=True, changed=True
    )
    assert run(service, "hold", "main") == HoldResult(
        command="hold", playlist="walls", held=True, changed=False
    )
    assert overview(service).playlists[0].held
    assert run(service, "resume", "walls") == HoldResult(
        command="resume", playlist="walls", held=False, changed=True
    )
    assert run(service, "resume", "walls") == HoldResult(
        command="resume", playlist="walls", held=False, changed=False
    )
    assert execute(service, Request(command="hold", target="walls")).change is None


@pytest.mark.parametrize(
    "image, message",
    [("missing.png", "No such image file"), ("walls/empty.jpg", "Cannot open")],
)
def test_pick_errors(
    service: PlaylistService, tmp_path: Path, image: str, message: str
) -> None:
    (tmp_path / "walls" / "empty.jpg").write_bytes(b"")  # not an image
    outcome = execute(service, PickRequest(target="walls", image=tmp_path / image))
    assert outcome.change is None
    reply = outcome.reply
    assert isinstance(reply, ErrorReply) and reply.error.kind == "bad_image"
    assert reply.error.message.startswith(message)


def test_pick_request_is_strict(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        PickRequest(image=Path("relative.png"))
    with pytest.raises(ValueError):
        PickRequest.model_validate({"image": str(tmp_path / "a.png"), "x": 1})
    with pytest.raises(ValueError):
        Request.model_validate({"command": "pick", "target": "walls"})
    envelope = Envelope.model_validate_json(
        Envelope(request=PickRequest(image=tmp_path)).model_dump_json()
    )
    assert envelope.request == PickRequest(image=tmp_path)


def test_manual_playlist(tmp_path: Path, backend: FakeBackend) -> None:
    make_images(tmp_path / "walls", "a.jpg")
    make_images(tmp_path / "strip", "s.jpg")
    save_settings(example(tmp_path, interval=None))
    with open_service(backend, FakePresence()) as service:
        reply = run(service, "hold", "walls")
        assert isinstance(reply, ErrorReply) and reply.error.kind == "failed"
        assert "walls is manual" in reply.error.message
        status = run(service, "status")
        assert isinstance(status, StatusResult)
        walls = status.playlists[0]
        assert walls.manual and not walls.held
        assert walls.interval is None and walls.next_in_seconds is None
        assert overview(service).playlists[0].manual


def test_no_images_and_unexpected_errors() -> None:
    assert error_reply(NoImagesError("x")).error.kind == "no_images"
    unexpected = error_reply(KeyError("boom"))
    assert unexpected.error.kind == "internal"
    assert unexpected.error.message == "Unexpected error: 'boom'"


def test_status(service: PlaylistService, tmp_path: Path) -> None:
    run(service, "next", "walls")
    status = run(service, "status")
    assert isinstance(status, StatusResult)
    assert status.paused is None and status.warnings == ()
    walls, strip = status.playlists
    assert (walls.name, walls.order, walls.interval) == (
        "walls",
        Order.SORTED,
        600.0,
    )
    assert walls.directory == tmp_path / "walls"
    assert walls.monitors == (
        MonitorView(id=MAIN.id, name="Main", attached=True),
        MonitorView(id=SIDE.id, name="Side", attached=False),
    )
    assert walls.current == tmp_path / "walls" / "a.jpg" and not walls.favourite
    assert walls.next_in_seconds == 600.0
    assert [p.name for p in walls.upcoming] == ["b.jpg", "a.jpg"]
    assert strip.monitors == (
        MonitorView(
            id=STRIP_ID, name=str(STRIP_ID), attached=None, look=Look(ImageMode.FIT)
        ),
    )


def test_overview(service: PlaylistService, tmp_path: Path) -> None:
    assert overview(service) == Overview(
        playlists=(
            PlaylistView(
                id="walls-id",
                name="walls",
                directory=tmp_path / "walls",
                monitors=(
                    MonitorView(id=MAIN.id, name="Main", attached=True),
                    MonitorView(id=SIDE.id, name="Side", attached=False),
                ),
                current=None,
                favourite=False,
            ),
            PlaylistView(
                id="strip-id",
                name="strip",
                directory=tmp_path / "strip",
                monitors=(
                    MonitorView(
                        id=STRIP_ID,
                        name=str(STRIP_ID),
                        attached=None,
                        look=Look(ImageMode.FIT),
                    ),
                ),
                current=None,
                favourite=False,
            ),
        ),
        # Every listed monitor, in a playlist or not ("Left"), with its
        # geometry (None: detached); "Strip" isn't listed, so isn't here.
        monitors=(
            DesktopMonitor(
                id=MAIN.id,
                name="Main",
                geometry=Geometry(x=0, y=0, width=1920, height=1080),
            ),
            DesktopMonitor(id=SIDE.id, name="Side", geometry=None),
            DesktopMonitor(
                id=LEFT.id,
                name="Left",
                geometry=Geometry(
                    x=-2560, y=0, width=2560, height=1440, pixels=(3840, 2160)
                ),
            ),
        ),
        favourites_file=paths.data_dir() / "favourites.txt",
        to_remove_file=paths.data_dir() / "to-remove.txt",
    )
    run(service, "next", "walls")
    run(service, "fav", "walls")
    walls = overview(service).playlists[0]
    assert walls.current == tmp_path / "walls" / "a.jpg"
    assert walls.favourite and walls.attached
    assert not overview(service).playlists[1].attached


def test_settings_and_save_settings(service: PlaylistService) -> None:
    reply = execute(service, SettingsRequest()).reply
    assert isinstance(reply, SettingsResult)
    assert reply.settings == service.settings
    main, side, strip = reply.settings.monitors
    assert strip == MonitorSettings(
        id=STRIP_ID, playlist="strip-id", look=Look(ImageMode.FIT)
    )
    left = MonitorSettings(id=LEFT.id, look=Look(ImageMode.STRETCH))
    edit = reply.settings.model_copy(
        update={"monitors": (main, side, strip, left), "pause_when_idle": 900.0}
    )
    outcome = execute(
        service, SaveSettingsRequest(settings=edit, revision=reply.revision)
    )
    saved = outcome.reply
    assert isinstance(saved, SaveSettingsResult)
    assert outcome.render  # the daemon ticks before replying
    assert saved.revision == reply.revision + 1
    assert len(saved.warnings) == 2  # 10m is shorter than the idle pause
    assert service.settings == edit
    assert overview(service).monitors[2].look == Look(ImageMode.STRETCH)

    stale = execute(service, SaveSettingsRequest(settings=edit, revision=0)).reply
    assert isinstance(stale, ErrorReply) and stale.error.kind == "stale_settings"
    bad = edit.model_copy(update={"pause_when_idle": 0.0})
    invalid = execute(
        service, SaveSettingsRequest(settings=bad, revision=saved.revision)
    ).reply
    assert isinstance(invalid, ErrorReply)
    assert invalid.error.kind == "invalid_settings"
    assert "idle pause" in invalid.error.message


def test_save_settings_request_is_strict() -> None:
    """The settings come from the window: a typo is refused, not ignored."""
    good = {"command": "save_settings", "settings": {}, "revision": 1}
    assert Envelope.model_validate({"request": good}).request == (
        SaveSettingsRequest(settings=Settings(), revision=1)
    )
    for bad in (
        {**good, "settings": {"playlist": []}},
        {**good, "settings": {"monitors": [{"id": "m", "mode": "fit"}]}},
        {"command": "save_settings", "settings": {}},  # no revision
    ):
        with pytest.raises(ValueError):
            Envelope.model_validate({"request": bad})


def test_a_favourites_list_never_read_is_unknown(
    tmp_path: Path, backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The toggle can't say "not a favourite" before the list was read."""
    make_images(tmp_path / "walls", "a.jpg", "b.jpg")
    save_settings(example(tmp_path))
    with open_service(backend, FakePresence()) as service:
        run(service, "next", "walls")
        run(service, "fav", "walls")
    busy = BusyFile(paths.data_dir() / "favourites.txt", monkeypatch)
    with open_service(backend, FakePresence()) as service:
        assert overview(service).playlists[0].favourite is None
        reply = run(service, "unfav", "walls")
        assert isinstance(reply, ErrorReply) and "Cannot read" in reply.error.message
        busy.busy = False
        assert overview(service).playlists[0].favourite is True


def test_stats_of_every_playlist_or_one(service: PlaylistService) -> None:
    service.tick()  # walls shows a.jpg; strip has no monitor attached
    reply = execute(service, StatsRequest()).reply
    assert isinstance(reply, StatsResult)
    assert [p.name for p in reply.playlists] == ["walls", "strip"]
    walls, strip = reply.playlists
    assert (walls.images, walls.seen, walls.unseen, walls.skipped) == (2, 1, 1, 0)
    assert (strip.images, strip.seen, strip.unseen) == (1, 0, 1)
    assert reply.favourites.file == paths.data_dir() / "favourites.txt"
    assert reply.to_remove.file == paths.data_dir() / "to-remove.txt"
    # Over the wire and back, as the CLI and the window read it.
    assert TypeAdapter(Reply).validate_json(reply.model_dump_json()) == reply

    one = execute(service, StatsRequest(target="Main", rescan=True)).reply
    assert isinstance(one, StatsResult)
    assert [p.name for p in one.playlists] == ["walls"]  # the one Main shows
    unknown = execute(service, StatsRequest(target="nope")).reply
    assert isinstance(unknown, ErrorReply)
    assert unknown.error.kind == "unknown_target"


def test_reset_cycle(service: PlaylistService) -> None:
    outcome = execute(service, ResetCycleRequest(target="strip"))
    assert outcome == Outcome(ResetCycleResult(playlist="strip", unseen=1))
    sorted_one = execute(service, ResetCycleRequest(target="walls")).reply
    assert isinstance(sorted_one, ErrorReply)
    assert sorted_one.error.kind == "failed"
    assert "walls is sorted" in sorted_one.error.message


def test_stats_request_is_strict() -> None:
    """A client's `rescan` must be a boolean (lax pydantic takes 1)."""
    text = '{"v": 3, "request": {"command": "stats", "rescan": 1}}'
    with pytest.raises(ValidationError):
        Envelope.model_validate_json(text)
