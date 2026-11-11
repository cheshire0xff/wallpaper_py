"""
The layout window: the monitor arrangement to scale, the playlist cards'
text, and the buttons, all through a fake Client (never a real app).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QSize
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from wallpaper_py.app.ui.arrangement import (
    LABEL_HEIGHT,
    MARGIN,
    Box,
    MonitorsView,
    arrange,
    caption,
    playlists_by_monitor,
)
from wallpaper_py.app.ui.client import Client, ReplyCallback
from wallpaper_py.app.ui.layout import (
    LayoutWindow,
    chip_text,
    details_text,
    format_duration,
    format_size,
    schedule_text,
)
from wallpaper_py.app.ui.thumbnails import (
    Decoded,
    ImageInfo,
    Thumbnail,
    ThumbnailLoader,
)
from wallpaper_py.core.config import Order
from wallpaper_py.core.service import Change
from wallpaper_py.protocol import (
    CommandRequest,
    DesktopMonitor,
    ErrorReply,
    Geometry,
    MarkResult,
    MonitorView,
    NextResult,
    Overview,
    PlaylistDetails,
    PlaylistView,
    PrevResult,
    Request,
    StatusResult,
)

AW = DesktopMonitor(
    id="aw", name="AW3225QF", geometry=Geometry(x=0, y=0, width=3840, height=2160)
)
PLX = DesktopMonitor(
    id="plx", name="PLX2783H", geometry=Geometry(x=-1920, y=0, width=1920, height=1080)
)
ZERO = DesktopMonitor(id="zero", name="ZeroMOD", geometry=None)  # switched off
TV = DesktopMonitor(
    id="tv", name="TV", geometry=Geometry(x=3840, y=0, width=1920, height=1080)
)

KONACHAN = PlaylistView(
    name="konachan",
    monitors=(
        MonitorView(selector="AW3225QF", name="AW3225QF", attached=True, id="aw"),
        MonitorView(selector="PLX", name="PLX2783H", attached=True, id="plx"),
    ),
    current=Path("/p/konachan/123.jpg"),
    favourite=False,
)
FANTASY = PlaylistView(
    name="fantasy",
    monitors=(
        MonitorView(selector="ZeroMOD", name="ZeroMOD", attached=False, id="zero"),
    ),
    current=Path("/p/fantasy/7.png"),
    favourite=False,
    manual=True,
)
OVERVIEW = Overview(playlists=(KONACHAN, FANTASY), monitors=(AW, PLX, ZERO, TV))

INFO = ImageInfo(3840, 2160, 4_200_000)
THUMBNAIL = Thumbnail(INFO, None)


def details(view: PlaylistView, next_in: float | None = 754.0) -> PlaylistDetails:
    return PlaylistDetails(
        **dict(view),
        directory=Path("/p") / view.name,
        order=Order.SHUFFLE,
        interval=None if view.manual else 1800.0,
        mode=None,
        next_in_seconds=None if view.manual else next_in,
        upcoming=(),
    )


# -- the arrangement -----------------------------------------------------------


def aspect(box: Box) -> float:
    return box.width / box.height


def test_negative_positions_are_scaled_to_fit_keeping_aspect() -> None:
    placed = {p.monitor.id: p.box for p in arrange([AW, PLX], 1000, 400)}
    aw, plx = placed["aw"], placed["plx"]
    # The PLX2783H at -1920,0 is left of the AW3225QF at 0,0, touching it.
    assert plx.x < aw.x and plx.right == pytest.approx(aw.x)
    assert plx.y == pytest.approx(aw.y)
    # One scale for both, aspect ratios kept.
    assert aw.width == pytest.approx(2 * plx.width)
    assert aspect(aw) == pytest.approx(16 / 9) and aspect(plx) == pytest.approx(16 / 9)
    # Inside the area (labels below), and as large as it fits.
    room_right, room_bottom = 1000 - MARGIN, 400 - MARGIN - LABEL_HEIGHT
    for box in (aw, plx):
        assert box.x >= MARGIN and box.y >= MARGIN
        assert box.right <= room_right + 1e-6 and box.bottom <= room_bottom + 1e-6
    # As large as it fits: here the height limits it.
    assert aw.bottom == pytest.approx(room_bottom)


def test_scaling_follows_the_area() -> None:
    small = arrange([AW, PLX], 500, 300)
    large = arrange([AW, PLX], 1500, 700)
    assert large[0].box.width > small[0].box.width
    assert aspect(large[0].box) == pytest.approx(aspect(small[0].box))


def test_stacked_monitors() -> None:
    top = DesktopMonitor(
        id="t", name="T", geometry=Geometry(x=0, y=-1080, width=1920, height=1080)
    )
    bottom = DesktopMonitor(
        id="b", name="B", geometry=Geometry(x=0, y=0, width=1920, height=1080)
    )
    placed = {p.monitor.id: p.box for p in arrange([top, bottom], 800, 600)}
    assert placed["t"].bottom == pytest.approx(placed["b"].y)
    assert placed["t"].x == pytest.approx(placed["b"].x)


def test_detached_monitors_go_right_of_the_arrangement() -> None:
    placed = arrange([AW, ZERO, PLX], 1200, 400)
    by_id = {p.monitor.id: p for p in placed}
    assert not by_id["zero"].attached and by_id["aw"].attached
    zero, aw = by_id["zero"].box, by_id["aw"].box
    assert zero.x > aw.right  # with a gap
    assert aspect(zero) == pytest.approx(16 / 9)  # never seen: 1920 x 1080
    # Its last known size, if it was seen attached.
    strip = arrange([AW, ZERO], 1200, 400, {"zero": (1920, 480)})
    assert aspect(strip[1].box) == pytest.approx(4)


def test_only_detached_monitors() -> None:
    placed = arrange([ZERO], 400, 300)
    assert len(placed) == 1 and placed[0].box.width > 0
    assert not arrange([], 400, 300)


def test_captions_and_monitors_in_no_playlist() -> None:
    by_monitor = playlists_by_monitor(OVERVIEW)
    assert by_monitor == {"aw": "konachan", "plx": "konachan", "zero": "fantasy"}
    assert caption(AW, "konachan") == ("AW3225QF", "3840×2160 · konachan")
    assert caption(TV, by_monitor.get("tv")) == ("TV", "1920×1080 · no playlist")
    assert caption(ZERO, "fantasy") == ("ZeroMOD", "off · fantasy")


def test_monitors_view_draws_every_monitor(qtbot: QtBot) -> None:
    view = MonitorsView()
    qtbot.addWidget(view)
    view.resize(900, 300)
    view.show_overview(OVERVIEW)
    assert {p.monitor.id for p in view.placed()} == {"aw", "plx", "zero", "tv"}
    assert "TV: 1920×1080 · no playlist" in view.accessibleDescription()
    assert "ZeroMOD: off · fantasy" in view.accessibleDescription()
    view.grab()  # paints without error
    before = view.placed()[0].box
    view.resize(500, 300)  # redrawn to the new size
    assert view.placed()[0].box.width < before.width
    # A monitor seen attached keeps its size once switched off.
    off = Overview(monitors=(AW.model_copy(update={"geometry": None}),))
    view.show_overview(off)
    assert aspect(view.placed()[0].box) == pytest.approx(16 / 9)
    view.grab()


# -- card text -----------------------------------------------------------------


def test_formats() -> None:
    assert format_size(980) == "980 B"
    assert format_size(12_300) == "12 KB"
    assert format_size(4_200_000) == "4.2 MB"
    assert format_size(1_100_000_000) == "1.1 GB"
    assert format_duration(30) == "< 1 min"
    assert format_duration(754) == "13 min"
    assert format_duration(3600) == "1 h"
    assert format_duration(3900) == "1 h 05 min"
    assert format_duration(2 * 86400 + 3 * 3600) == "2 d 3 h"


def test_card_text_normal() -> None:
    assert chip_text(KONACHAN) is None
    assert details_text(KONACHAN, details(KONACHAN), THUMBNAIL, 754.0) == (
        "3840×2160 · 4.2 MB · shuffle, 30 min · next in 13 min"
    )
    # Before the image and the schedule have arrived.
    assert details_text(KONACHAN, None, None, None) == ""
    unreadable = Thumbnail(None, None, "Unsupported image format")
    assert details_text(KONACHAN, None, unreadable, None) == (
        "can't read: Unsupported image format"
    )


def test_card_text_held() -> None:
    held = KONACHAN.model_copy(update={"held": True})
    assert chip_text(held) == "Held"
    assert schedule_text(held, details(held), 754.0) == (
        "shuffle, 30 min · 13 min left once resumed"
    )


def test_card_text_manual() -> None:
    assert chip_text(FANTASY) == "Manual"
    assert details_text(FANTASY, details(FANTASY), THUMBNAIL, None) == (
        "3840×2160 · 4.2 MB · shuffle · manual"
    )


def test_card_text_without_a_monitor() -> None:
    off = FANTASY.model_copy(update={"manual": False})
    assert schedule_text(off, details(off), 60.0) == (
        "shuffle, 30 min · 1 min left once a monitor is on"
    )


# -- the window ----------------------------------------------------------------


class FakeClient(Client):
    """Records requests; a test answers them with `reply`."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[CommandRequest, ReplyCallback]] = []

    def run(self, request: CommandRequest, on_reply: ReplyCallback) -> None:
        self.calls.append((request, on_reply))

    def publish(self, overview: Overview) -> None:
        """As if the app sent a new overview."""
        self._set_overview(overview)

    def commands(self) -> list[CommandRequest]:
        return [r for r, _ in self.calls if r.command != "status"]

    def answer_status(self, overview: Overview) -> None:
        """Answer the status requests so far (the window may ask again)."""
        asked = [c for c in self.calls if c[0].command == "status"]
        self.calls = [c for c in self.calls if c[0].command != "status"]
        for _, on_reply in asked:
            on_reply(
                StatusResult(
                    playlists=tuple(details(p) for p in overview.playlists),
                    paused=None,
                )
            )


def fake_decode(path: Path, box: QSize) -> Decoded:
    del path
    image = QImage(box.width() // 4, box.height() // 4, QImage.Format.Format_RGB32)
    image.fill(0x336699)
    return Decoded(INFO, image)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(name="client")
def fixture_client() -> FakeClient:
    return FakeClient()


@pytest.fixture(name="clock")
def fixture_clock() -> Clock:
    return Clock()


@pytest.fixture(name="window")
def fixture_window(
    qtbot: QtBot, client: FakeClient, clock: Clock
) -> Iterator[LayoutWindow]:
    loader = ThumbnailLoader(QSize(480, 270), fake_decode)
    window = LayoutWindow(client, loader, clock)
    qtbot.addWidget(window)
    window.show()
    yield window
    loader.close()


def test_window_draws_the_overview(
    qtbot: QtBot, window: LayoutWindow, client: FakeClient
) -> None:
    assert window.banner.text() == "Connecting to wallpaper_py…"
    client.publish(OVERVIEW)
    assert window.banner.isHidden()
    assert list(window.cards) == ["konachan", "fantasy"]
    konachan, fantasy = window.cards["konachan"], window.cards["fantasy"]
    assert konachan.title.text() == "konachan"
    assert konachan.monitors.text() == "AW3225QF + PLX2783H"
    assert not konachan.link.isHidden()  # a mirrored group
    assert konachan.chip.isHidden()
    assert konachan.file.text() == "123.jpg"
    assert fantasy.monitors.text() == "ZeroMOD (off)"
    assert fantasy.link.isHidden()
    assert fantasy.chip.text() == "Manual" and not fantasy.chip.isHidden()
    assert fantasy.hold_button.isHidden()  # hold doesn't apply to manual
    assert not konachan.hold_button.isHidden()
    assert {p.monitor.id for p in window.monitors_view.placed()} == {
        "aw",
        "plx",
        "zero",
        "tv",
    }
    # Thumbnails arrive off the UI thread; details follow.
    qtbot.waitUntil(lambda: konachan.thumbnail() is not None)
    assert konachan.image.pixmap() is not None
    assert konachan.details.text() == "3840×2160 · 4.2 MB"
    # Then the schedule, from one status request.
    statuses = [r for r, _ in client.calls if r.command == "status"]
    assert statuses == [Request(command="status")]
    client.answer_status(OVERVIEW)
    assert konachan.details.text().endswith("shuffle, 30 min · next in 13 min")
    assert fantasy.details.text().endswith("shuffle · manual")


def test_the_countdown_runs_only_while_counting(
    window: LayoutWindow, client: FakeClient, clock: Clock
) -> None:
    client.publish(OVERVIEW)
    client.answer_status(OVERVIEW)
    card = window.cards["konachan"]
    assert card.details.text().endswith("next in 13 min")
    clock.now += 600
    window.refresh_schedule()
    assert card.details.text().endswith("next in 3 min")
    # Paused (locked / idle): it stands still, and the window says why.
    client.publish(OVERVIEW.model_copy(update={"paused": "idle"}))
    client.answer_status(OVERVIEW)
    assert window.banner.text() == "Rotation paused: idle"
    clock.now += 600
    window.refresh_schedule()
    assert card.details.text().endswith("next in 13 min")


def test_status_is_asked_once_at_a_time(
    window: LayoutWindow, client: FakeClient
) -> None:
    client.publish(OVERVIEW)
    client.publish(OVERVIEW)
    assert len([r for r, _ in client.calls if r.command == "status"]) == 1
    client.answer_status(OVERVIEW)  # then once more, for the newer overview
    assert len([r for r, _ in client.calls if r.command == "status"]) == 1
    assert window.cards["konachan"].details.text()


def test_hover_highlights_a_playlist_in_the_drawing_and_its_card(
    qtbot: QtBot, window: LayoutWindow, client: FakeClient
) -> None:
    client.publish(OVERVIEW)
    view = window.monitors_view
    konachan, fantasy = window.cards["konachan"], window.cards["fantasy"]
    assert view.highlight() is None and not konachan.highlighted()
    # Pointing at a card outlines its monitors.
    konachan.hovered.emit("konachan")
    assert view.highlight() == "konachan"
    assert konachan.highlighted() and not fantasy.highlighted()
    view.grab()  # paints the outline without error
    konachan.hovered.emit(None)
    assert view.highlight() is None and not konachan.highlighted()
    # Pointing at a monitor highlights its card; one in no playlist, nothing.
    boxes = {p.monitor.id: p.box for p in view.placed()}

    def centre(monitor_id: str) -> QPoint:
        box = boxes[monitor_id]
        return QPoint(round(box.x + box.width / 2), round(box.y + box.height / 2))

    QTest.mouseMove(view, centre("plx"))
    qtbot.waitUntil(konachan.highlighted)
    assert view.highlight() == "konachan" and not fantasy.highlighted()
    QTest.mouseMove(view, centre("tv"))
    qtbot.waitUntil(lambda: view.highlight() is None)
    assert not konachan.highlighted()


def test_buttons_send_commands(window: LayoutWindow, client: FakeClient) -> None:
    held_fav = KONACHAN.model_copy(update={"favourite": True, "held": True})
    client.publish(
        Overview(playlists=(KONACHAN, held_fav.model_copy(update={"name": "other"})))
    )
    card, other = window.cards["konachan"], window.cards["other"]
    for button in (
        card.prev_button,
        card.next_button,
        card.fav_button,
        card.remove_button,
        card.hold_button,
        other.fav_button,
        other.hold_button,
    ):
        button.click()
    assert client.commands() == [
        Request(command="prev", target="konachan"),
        Request(command="next", target="konachan"),
        Request(command="fav", target="konachan"),
        Request(command="remove", target="konachan"),
        Request(command="hold", target="konachan"),
        Request(command="unfav", target="other"),  # it is a favourite
        Request(command="resume", target="other"),  # it is held
    ]
    assert card.prev_button.toolTip() == "Previous"
    assert card.prev_button.accessibleName() == "Previous"
    assert other.fav_button.accessibleName() == "Remove from favourites"


def test_toggles_follow_the_overview_not_the_click(
    window: LayoutWindow, client: FakeClient
) -> None:
    client.publish(Overview(playlists=(KONACHAN,)))
    card = window.cards["konachan"]
    card.fav_button.click()
    card.hold_button.click()
    request, on_reply = client.calls[-2]
    assert request == Request(command="fav", target="konachan")
    on_reply(
        MarkResult(
            command="fav",
            playlist="konachan",
            image=Path("/p/konachan/123.jpg"),
            list_file=Path("/p/favourites.txt"),
            changed=True,
        )
    )
    assert not card.fav_button.is_on() and not card.hold_button.is_on()
    on = KONACHAN.model_copy(update={"favourite": True, "held": True})
    client.publish(Overview(playlists=(on,)))
    assert card.fav_button.is_on() and card.hold_button.is_on()
    assert card.chip.text() == "Held"
    client.publish(Overview(playlists=(KONACHAN,)))
    assert not card.fav_button.is_on() and not card.hold_button.is_on()


def test_nothing_shown_yet(window: LayoutWindow, client: FakeClient) -> None:
    empty = KONACHAN.model_copy(update={"current": None})
    client.publish(Overview(playlists=(empty,)))
    card = window.cards["konachan"]
    assert card.file.text() == "Nothing shown yet"
    assert not card.fav_button.isEnabled() and not card.remove_button.isEnabled()
    assert card.next_button.isEnabled()


def test_errors_show_in_the_card(window: LayoutWindow, client: FakeClient) -> None:
    client.publish(OVERVIEW)
    card = window.cards["fantasy"]
    card.next_button.click()
    _, on_reply = client.calls[-1]
    on_reply(ErrorReply.make("not_attached", "None of fantasy's monitors is attached"))
    assert card.message.text() == "None of fantasy's monitors is attached"
    assert not card.message.isHidden() and not card.message_icon.isHidden()
    card.prev_button.click()  # a new command clears it
    assert card.message.isHidden()
    _, on_reply = client.calls[-1]
    on_reply(PrevResult(playlist="fantasy", change=None))
    assert card.message.text() == "Nothing earlier in the history"
    assert card.message_icon.isHidden()  # a note, not an error
    card.next_button.click()
    _, on_reply = client.calls[-1]
    change = Change("fantasy", Path("/p/fantasy/8.png"), "next", ("ZeroMOD",))
    on_reply(NextResult(playlist="fantasy", change=change))
    assert card.message.isHidden()


def test_config_error_and_unreachable_app(
    window: LayoutWindow, client: FakeClient
) -> None:
    client.publish(Overview(error="No config file"))
    assert window.banner.text() == "Error: No config file"
    assert not window.cards and window.empty.isHidden()
    assert not client.calls  # no status while the service is down
    client.failed.emit("could not start wallpaper_py")
    assert window.banner.text() == "Error: could not start wallpaper_py"


def test_a_reply_after_closing_is_ignored(
    window: LayoutWindow, client: FakeClient
) -> None:
    client.publish(OVERVIEW)
    window.cards["konachan"].next_button.click()
    window.close()
    for _, on_reply in client.calls:
        on_reply(ErrorReply.make("unreachable", "gone"))  # no error


def test_cards_reflow_with_the_width(
    qtbot: QtBot, window: LayoutWindow, client: FakeClient
) -> None:
    three = OVERVIEW.model_copy(
        update={
            "playlists": (
                *OVERVIEW.playlists,
                KONACHAN.model_copy(update={"name": "third"}),
            )
        }
    )
    client.publish(three)
    window.resize(1400, 800)
    qtbot.waitUntil(lambda: window.columns() == 3)
    window.resize(500, 800)
    qtbot.waitUntil(lambda: window.columns() == 1)
    # A playlist gone from the config: its card goes too.
    client.publish(OVERVIEW)
    assert list(window.cards) == ["konachan", "fantasy"]
