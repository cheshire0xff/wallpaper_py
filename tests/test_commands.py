"""Commands run on the service: data in, data out; and the overview."""

from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import pytest
from PIL import Image

from wallpaper_py.commands import (
    Outcome,
    error_reply,
    execute,
    open_service,
    overview,
)
from wallpaper_py.core import paths
from wallpaper_py.core.model import ImageMode, Look, MonitorId
from wallpaper_py.core.service import (
    Change,
    ChangeReason,
    NoImagesError,
    PlaylistService,
)
from wallpaper_py.core.settings import MonitorSettings, Order, Settings
from wallpaper_py.protocol import (
    Command,
    DesktopMonitor,
    Envelope,
    ErrorReply,
    Geometry,
    HoldResult,
    MarkResult,
    MonitorView,
    NextResult,
    Overview,
    PickRequest,
    PickResult,
    PlaylistView,
    PrevResult,
    PruneRequest,
    PruneResult,
    RemoveResult,
    Reply,
    Request,
    ResumeResult,
    SaveSettingsRequest,
    SaveSettingsResult,
    SettingsRequest,
    SettingsResult,
    StatusResult,
)

from .fakes import (
    BusyFile,
    FakeBackend,
    FakePresence,
    make_images,
    make_monitor,
    fake_id,
    toml_ids,
)

CONFIG = f"""
pause_when_idle = false

[playlists.walls]
id = "walls-id"
directory = 'walls'
monitors = {toml_ids("Main", "Side")}
order = "sorted"
interval = "10m"

[playlists.strip]
id = "strip-id"
directory = 'strip'
monitors = {toml_ids("Strip")}
interval = "10m"
mode = "fit"
"""
STRIP_ID = MonitorId(fake_id("Strip"))  # not listed

MAIN = make_monitor("Main")
SIDE = make_monitor("Side", attached=False)
LEFT = make_monitor("Left", x=-2560, size=(2560, 1440))  # in no playlist


@pytest.fixture(name="backend")
def fixture_backend() -> FakeBackend:
    return FakeBackend([MAIN, SIDE, LEFT])  # "Strip" is not listed at all


@pytest.fixture(name="service")
def fixture_service(tmp_path: Path, backend: FakeBackend) -> Iterator[PlaylistService]:
    make_images(tmp_path / "walls", "a.jpg", "b.jpg")
    make_images(tmp_path / "strip", "s.jpg")
    config = tmp_path / "config.toml"
    config.write_text(CONFIG, encoding="utf-8")
    with open_service(config, backend, FakePresence()) as service:
        yield service


def run(service: PlaylistService, command: Command, target: str | None = None) -> Reply:
    return execute(service, Request(command=command, target=target)).reply


def walls_change(tmp_path: Path, name: str, reason: ChangeReason = "next") -> Change:
    # Side is detached already: only members detaching mid-change are "missed".
    return Change("walls", tmp_path / "walls" / name, reason, ("Main",))


def test_unknown_command_is_rejected() -> None:
    with pytest.raises(ValueError):
        Request(command="delete")  # type: ignore[arg-type]


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
        change=walls_change(tmp_path, "b.jpg"),
    )
    assert b.is_file()  # listed, never deleted


def test_commands_that_change_wallpapers_return_the_change(
    service: PlaylistService,
) -> None:
    def change(command: Command) -> tuple[str, str] | None:
        outcome = execute(service, Request(command=command, target="walls"))
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
    assert execute(service, Request(command="status")).change is None


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
    service: PlaylistService, command: Command, target: str, kind: str, message: str
) -> None:
    outcome = execute(service, Request(command=command, target=target))
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
    change = Change("walls", image, "picked", ("Main",))
    assert outcome == Outcome(PickResult(playlist="walls", change=change), change)

    assert run(service, "hold", "walls") == HoldResult(
        playlist="walls", held=True, changed=True
    )
    assert run(service, "hold", "main") == HoldResult(
        playlist="walls", held=True, changed=False
    )
    assert overview(service).playlists[0].held
    assert run(service, "resume", "walls") == ResumeResult(
        playlist="walls", held=False, changed=True
    )
    assert run(service, "resume", "walls") == ResumeResult(
        playlist="walls", held=False, changed=False
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
        Envelope(request=PickRequest(image=tmp_path), config=tmp_path).model_dump_json()
    )
    assert envelope.request == PickRequest(image=tmp_path)


def test_manual_playlist(tmp_path: Path, backend: FakeBackend) -> None:
    make_images(tmp_path / "walls", "a.jpg")
    make_images(tmp_path / "strip", "s.jpg")
    config = tmp_path / "config.toml"
    manual = CONFIG.replace('interval = "10m"', 'interval = "manual"')
    config.write_text(manual, encoding="utf-8")
    with open_service(config, backend, FakePresence()) as service:
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
                name="walls",
                monitors=(
                    MonitorView(id=MAIN.id, name="Main", attached=True),
                    MonitorView(id=SIDE.id, name="Side", attached=False),
                ),
                current=None,
                favourite=False,
            ),
            PlaylistView(
                name="strip",
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
                geometry=Geometry(x=-2560, y=0, width=2560, height=1440),
            ),
        ),
    )
    run(service, "next", "walls")
    run(service, "fav", "walls")
    walls = overview(service).playlists[0]
    assert walls.current == tmp_path / "walls" / "a.jpg"
    assert walls.favourite and walls.attached
    assert not overview(service).playlists[1].attached


WALLS_ONLY, STRIP, STRIP_TABLE = CONFIG.partition("[playlists.strip]")


def walls_current(config: Path, backend: FakeBackend) -> Path | None:
    with open_service(config, backend, FakePresence()) as service:
        reply = run(service, "status")
        assert isinstance(reply, StatusResult)
        return next((p.current for p in reply.playlists if p.name == "walls"), None)


def test_the_config_file_is_imported_into_the_settings(
    service: PlaylistService, backend: FakeBackend, tmp_path: Path
) -> None:
    """
    The bridge of Phase 4b step 3a: the file replaces the stored settings
    when the service opens; without a file, they stand.
    """
    run(service, "next", "walls")
    run(service, "hold", "strip")  # strip's monitor isn't listed: hold only
    shown = service.current(service.settings.playlists[0])
    assert shown is not None
    config = tmp_path / "config.toml"
    config.unlink()  # briefly gone (an editor replacing it)
    assert walls_current(config, backend) == shown
    config.write_text(WALLS_ONLY, encoding="utf-8")  # strip deleted
    with open_service(config, backend, FakePresence()) as again:
        assert [p.name for p in again.settings.playlists] == ["walls"]
        assert execute(again, PruneRequest()).reply == PruneResult(dropped=())
        assert again.current(again.settings.playlists[0]) == shown
    config.write_text(CONFIG, encoding="utf-8")
    with open_service(config, backend, FakePresence()) as again:
        assert not again.held(again.settings.playlists[1])  # strip starts afresh


def test_a_folder_changed_in_the_file_shows_on_the_next_tick(
    service: PlaylistService, backend: FakeBackend, tmp_path: Path
) -> None:
    """The bridge: a reload imports the file; the new folder shows at once."""
    run(service, "next", "walls")
    make_images(tmp_path / "art", "z.jpg")
    config = tmp_path / "config.toml"
    config.write_text(CONFIG.replace("'walls'", "'art'", 1), encoding="utf-8")
    with open_service(config, backend, FakePresence()) as again:
        [change] = [c for c in again.tick() if c.playlist == "walls"]
        assert change.image == tmp_path / "art" / "z.jpg"


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
    assert outcome.render  # the app ticks before replying
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
    config = tmp_path / "config.toml"
    config.write_text(CONFIG, encoding="utf-8")
    with open_service(config, backend, FakePresence()) as service:
        run(service, "next", "walls")
        run(service, "fav", "walls")
    busy = BusyFile(paths.data_dir() / "favourites.txt", monkeypatch)
    with open_service(config, backend, FakePresence()) as service:
        assert overview(service).playlists[0].favourite is None
        reply = run(service, "unfav", "walls")
        assert isinstance(reply, ErrorReply) and "Cannot read" in reply.error.message
        busy.busy = False
        assert overview(service).playlists[0].favourite is True
