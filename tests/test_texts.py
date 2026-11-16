"""What the tray, Home and the CLI say about a playlist or a monitor: texts.py."""

from pathlib import Path

from wallpaper_py.core.model import MonitorId
from wallpaper_py.core.settings import Order
from wallpaper_py.protocol import MonitorView, PlaylistDetails, PlaylistView
from wallpaper_py.texts import (
    members,
    monitor_label,
    not_rotating,
    playlist_label,
    rounded_duration,
    traits,
)

AW = MonitorView(id=MonitorId("aw"), name="AW3225QF", attached=True)
ZERO = MonitorView(id=MonitorId("zero"), name="ZeroMOD", attached=False)
GONE = MonitorView(id=MonitorId("DELA1B2"), name="DELA1B2", attached=None)
WALLS = PlaylistView(
    id="walls-id",
    name="walls",
    directory=Path("/p/walls"),
    monitors=(AW, ZERO),
    current=None,
    favourite=None,
)


def details(view: PlaylistView, interval: float | None) -> PlaylistDetails:
    return PlaylistDetails(
        **dict(view),
        order=Order.SHUFFLE,
        interval=interval,
        next_in_seconds=interval,
        upcoming=(),
    )


def test_monitors() -> None:
    assert [monitor_label(m) for m in (AW, ZERO, GONE)] == [
        "AW3225QF",
        "ZeroMOD (off)",
        "DELA1B2 (not found)",
    ]
    assert members(WALLS) == "AW3225QF + ZeroMOD (off)"


def test_why_a_playlist_doesnt_change_by_itself() -> None:
    held = WALLS.model_copy(update={"held": True})
    manual = WALLS.model_copy(update={"manual": True})
    assert [not_rotating(p) for p in (WALLS, held, manual)] == [None, "held", "manual"]
    assert [playlist_label(p) for p in (WALLS, held, manual)] == [
        "walls",
        "walls (held)",
        "walls (manual)",
    ]


def test_traits_are_exact() -> None:
    """They say the setting: an interval of 1h30m15s is not rounded."""
    assert traits(details(WALLS, 5415.0)) == ["shuffle", "every 1h30m15s"]
    held = WALLS.model_copy(update={"held": True})
    assert traits(details(held, 1800.0)) == ["shuffle", "every 30m", "held"]
    assert traits(details(WALLS, None)) == ["shuffle", "manual"]


def test_rounded_durations() -> None:
    """As the settings write durations; seconds only under 10 minutes."""
    assert rounded_duration(30) == "30s"
    assert rounded_duration(350) == "5m50s"
    assert rounded_duration(300) == "5m"
    assert rounded_duration(754) == "13m"
    assert rounded_duration(3600) == "1h"
    assert rounded_duration(3900) == "1h5m"
    assert rounded_duration(5415) == "1h30m"
    assert rounded_duration(2 * 86400 + 3 * 3600) == "2d3h"
    assert rounded_duration(-5) == "0s"
