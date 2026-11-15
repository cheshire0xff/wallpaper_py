"""
The KDE backend on recorded tool output, and read-only checks against the
real Plasma desktop when there is one. Never sets anything on it.
"""

import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

if sys.platform != "linux":
    pytest.skip("Linux only", allow_module_level=True)
# Never fails (skipped above); tells mypy the rest is Linux-only.
assert sys.platform == "linux"

# pylint: disable=wrong-import-position
from wallpaper_py.backends.kde import tools
from wallpaper_py.backends.kde.backend import KdeBackend
from wallpaper_py.backends.kde.edid import connector_name, monitor_name
from wallpaper_py.backends.kde.presence import KdePresence
from wallpaper_py.core.setter import rendered_path
from wallpaper_py.core.model import (
    MonitorId,
    MonitorNotAttachedError,
    Rectangle,
    WallpaperError,
)


def edid(name: str | None, maker: str = "DEL", product: int = 0xA246) -> bytes:
    """A base EDID block with a name descriptor (if `name`), as monitors send."""
    data = bytearray(128)
    data[0:8] = bytes([0, 255, 255, 255, 255, 255, 255, 0])
    packed = sum((ord(c) - 64) << shift for c, shift in zip(maker, (10, 5, 0)))
    data[8:10] = packed.to_bytes(2, "big")
    data[10:12] = product.to_bytes(2, "little")
    # Two descriptors: a serial number first, then the name.
    data[72:77] = bytes([0, 0, 0, 0xFF, 0])
    data[77:90] = b"SERIAL123\n   "
    if name is not None:
        data[90:95] = bytes([0, 0, 0, 0xFC, 0])
        data[95:108] = (name.encode() + b"\n").ljust(13, b" ")
    return bytes(data)


def output(
    name: str,
    size: tuple[int, int] = (1920, 1080),
    connected: bool = True,
    enabled: bool = True,
) -> dict[str, object]:
    """One output as `kscreen-doctor -j` lists it (the fields used, and some not)."""
    return {
        "id": 1,
        "name": name,
        "connected": connected,
        "enabled": enabled,
        "pos": {"x": 0, "y": 0},
        "scale": 1.5,
        "rotation": 1,
        "size": {"width": size[0], "height": size[1]},
        "modes": [],
    }


def screen(x: int, width: int, height: int, image: str = "") -> dict[str, object]:
    """One connector's screen, as the backend's Plasma script prints it."""
    return {"x": x, "y": 0, "width": width, "height": height, "image": image}


class FakeDesktop:
    """Answers the backend's commands as kscreen-doctor and plasmashell would."""

    def __init__(self) -> None:
        self.outputs = [
            output("DP-4", (3840, 2160)),
            output("HDMI-A-2"),
            output("HDMI-A-3", (1920, 480)),
        ]
        # Connector -> its Plasma screen; one Plasma has no screen on is
        # left out (the script skips it).
        self.screens = {
            "DP-4": screen(1920, 2560, 1440, "file:///walls/a%20b%5B1%5D.jpg"),
            "HDMI-A-2": screen(0, 1920, 1080, "/walls/plain.png"),
            "HDMI-A-3": screen(4480, 960, 240),
        }
        self.scripts: list[str] = []
        self.set_count = "1"
        self.fail: str | None = None  # stderr of a failing call

    def __call__(self, argv: Sequence[str]) -> str:
        if self.fail is not None:
            raise WallpaperError(f"{argv[0]} failed: {self.fail}")
        if list(argv) == ["kscreen-doctor", "-j"]:
            return json.dumps({"outputs": self.outputs, "screens": []})
        assert list(argv[:8]) == [
            "busctl",
            "--user",
            "--json=short",
            "call",
            "org.kde.plasmashell",
            "/PlasmaShell",
            "org.kde.PlasmaShell",
            "evaluateScript",
        ]
        assert argv[8] == "s"
        script = argv[9]
        self.scripts.append(script)
        if "screenGeometry" in script:
            names = json.loads(script.split("var names = ")[1].split(";\n")[0])
            listed = {n: s for n, s in self.screens.items() if n in names}
            printed = json.dumps(listed)
        else:
            printed = self.set_count
        return json.dumps({"type": "s", "data": [printed]})


@pytest.fixture(name="desktop")
def fixture_desktop() -> FakeDesktop:
    return FakeDesktop()


@pytest.fixture(name="drm")
def fixture_drm(tmp_path: Path) -> Path:
    """/sys/class/drm with EDIDs: two cards; an empty one for a port in use."""
    drm = tmp_path / "drm"
    for connector, data in {
        "card2-DP-4": edid("AW3225QF"),
        "card2-HDMI-A-2": edid("PLX2783H", "IVM", 0x6611),
        "card2-HDMI-A-3": edid(None, "___", 0x3132),
        "card1-DP-1": b"",  # nothing plugged in
    }.items():
        (drm / connector).mkdir(parents=True)
        (drm / connector / "edid").write_bytes(data)
    return drm


def test_monitors(desktop: FakeDesktop, drm: Path) -> None:
    monitors = KdeBackend(desktop, drm).monitors()
    assert [(str(m.id), m.name) for m in monitors] == [
        ("DP-4", "AW3225QF"),
        ("HDMI-A-2", "PLX2783H"),
        ("HDMI-A-3", "___3132"),  # no name in its EDID: its codes, as on Windows
    ]
    main, side, strip = monitors[0], monitors[1], monitors[2]
    # Plasma's (logical) desktop coordinates; KScreen's pixels.
    assert main.rect == Rectangle(1920, 0, 4480, 1440, pixels=(3840, 2160))
    assert main.rect.size == (3840, 2160)
    assert side.rect == Rectangle(0, 0, 1920, 1080, pixels=(1920, 1080))
    assert strip.rect is not None and strip.rect.size == (1920, 480)
    assert main.wallpaper == Path("/walls/a b[1].jpg")  # from a file URL
    assert side.wallpaper == Path("/walls/plain.png")
    assert strip.wallpaper is None


def test_a_monitor_switched_off_stays_listed_detached(
    desktop: FakeDesktop, drm: Path
) -> None:
    backend = KdeBackend(desktop, drm)
    backend.monitors()
    # Switched off: KScreen drops it, and its EDID can't be read any more.
    desktop.outputs = desktop.outputs[1:]
    (drm / "card2-DP-4" / "edid").write_bytes(b"")
    monitors = backend.monitors()
    assert [(str(m.id), m.name, m.attached) for m in monitors] == [
        ("HDMI-A-2", "PLX2783H", True),
        ("HDMI-A-3", "___3132", True),
        ("DP-4", "AW3225QF", False),
    ]


def test_disabled_or_without_a_plasma_screen_is_detached(
    desktop: FakeDesktop, drm: Path
) -> None:
    desktop.outputs[1]["enabled"] = False
    del desktop.screens["HDMI-A-3"]  # e.g. mirroring another output
    monitors = KdeBackend(desktop, drm).monitors()
    assert [(str(m.id), m.attached) for m in monitors] == [
        ("DP-4", True),
        ("HDMI-A-2", False),
        ("HDMI-A-3", False),
    ]
    # Plasma is asked only about enabled outputs.
    assert '["DP-4", "HDMI-A-3"]' in desktop.scripts[0]


def test_empty_ports_are_no_monitors(desktop: FakeDesktop, drm: Path) -> None:
    # X11's KScreen lists every port.
    desktop.outputs.append(output("DP-5", connected=False, enabled=False))
    assert "DP-5" not in [str(m.id) for m in KdeBackend(desktop, drm).monitors()]


def test_no_monitors_asks_plasma_nothing(desktop: FakeDesktop, drm: Path) -> None:
    desktop.outputs = []
    assert not KdeBackend(desktop, drm).monitors()
    assert not desktop.scripts


def test_set_wallpaper(desktop: FakeDesktop, drm: Path, tmp_path: Path) -> None:
    image = tmp_path / "a b#1.bmp"
    KdeBackend(desktop, drm).set_wallpaper(MonitorId("DP-4"), image)
    script = desktop.scripts[-1]
    # Values go in as JSON (valid JavaScript literals), the path as a file URL.
    assert 'var name = "DP-4"' in script
    assert json.dumps(image.as_uri()) in script
    assert "%23" in script and "%20" in script
    # Every activity's desktop on that screen, kept at "Scaled and Cropped".
    assert "desktopsForActivity" in script
    assert 'd.writeConfig("FillMode", 2)' in script


def test_render_files_alternate_through_a_symlink(
    desktop: FakeDesktop, drm: Path, tmp_path: Path
) -> None:
    """
    A symlinked cache (~/.cache elsewhere): Plasma gets, and reports back,
    the path as given, so the setter sees which render file is shown and
    writes the other one.
    """
    (tmp_path / "real").mkdir()
    cache = tmp_path / "cache"
    cache.symlink_to(tmp_path / "real")
    backend = KdeBackend(desktop, drm)
    shown = []
    for _ in range(3):
        monitor = next(m for m in backend.monitors() if str(m.id) == "DP-4")
        render = rendered_path(cache, monitor)
        backend.set_wallpaper(monitor.id, render)
        url = json.loads(desktop.scripts[-1].split("url = ")[1].split(";")[0])
        desktop.screens["DP-4"]["image"] = url  # what Plasma now shows
        shown.append(render.name[-5])
    assert shown == ["0", "1", "0"]


def test_set_wallpaper_on_no_plasma_screen(desktop: FakeDesktop, drm: Path) -> None:
    desktop.set_count = "0"
    with pytest.raises(MonitorNotAttachedError):
        KdeBackend(desktop, drm).set_wallpaper(MonitorId("DP-9"), Path("x.bmp"))


def test_plasmashell_away_is_a_wallpaper_error(desktop: FakeDesktop, drm: Path) -> None:
    desktop.fail = "The name is not activatable"
    with pytest.raises(WallpaperError, match="not activatable"):
        KdeBackend(desktop, drm).monitors()


@pytest.mark.parametrize(
    "answer",
    [
        "not json",
        json.dumps({"type": "s", "data": ["not json"]}),
        json.dumps({"type": "s", "data": ['{"DP-4": {"x": "left"}}']}),
        json.dumps({"type": "s", "data": ["[]"]}),
        json.dumps({"type": "u", "data": [3]}),
    ],
)
def test_answers_not_understood(drm: Path, answer: str) -> None:
    def run(argv: Sequence[str]) -> str:
        return (
            json.dumps({"outputs": [output("DP-4")]}) if argv[0] != "busctl" else answer
        )

    with pytest.raises(WallpaperError):
        KdeBackend(run, drm).monitors()


def test_kscreen_output_not_understood(drm: Path) -> None:
    with pytest.raises(WallpaperError, match="kscreen-doctor"):
        KdeBackend(lambda argv: '{"outputs": 3}', drm).monitors()


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (edid("AW3225QF"), "AW3225QF"),
        (edid(None), "DELA246"),
        (edid("Äx"[1:]), "x"),
        (b"", None),
        (b"\0" * 128, None),
    ],
)
def test_monitor_name(data: bytes, expected: str | None) -> None:
    assert monitor_name(data) == expected


def test_connector_name(drm: Path) -> None:
    assert connector_name("DP-4", drm) == "AW3225QF"
    assert connector_name("DP-1", drm) is None  # nothing plugged in
    assert connector_name("DP-9", drm) is None  # no such port


def test_run_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    assert tools.run(["echo", "hi"]) == "hi\n"
    # Never passed on: kscreen-doctor would see no outputs.
    monkeypatch.setenv("QT_QPA_PLATFORM", "xcb")
    assert tools.run(["sh", "-c", "echo ${QT_QPA_PLATFORM-unset}"]) == "unset\n"
    with pytest.raises(WallpaperError, match="kscreen-doctor-nope is missing"):
        tools.run(["kscreen-doctor-nope"])
    with pytest.raises(WallpaperError, match="false failed: exit code 1"):
        tools.run(["false"])
    with pytest.raises(WallpaperError, match="sh failed: boom"):
        tools.run(["sh", "-c", "echo boom >&2; exit 3"])
    # Output that isn't UTF-8 is read, not an exception.
    assert tools.run(["printf", "a\\377b"]) == "a\ufffdb"


def test_a_tool_that_cant_be_run(tmp_path: Path) -> None:
    script = tmp_path / "kscreen-doctor"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o644)  # not executable: PermissionError, not FileNotFoundError
    with pytest.raises(WallpaperError, match="can't be run"):
        tools.run([str(script)])
    assert KdePresence(lambda argv: tools.run([str(script)])).session_locked() is None


def test_presence() -> None:
    def answer(value: object) -> tools.Runner:
        return lambda argv: json.dumps({"type": "b", "data": [value]})

    def fail(argv: Sequence[str]) -> str:
        raise WallpaperError("busctl failed: no screen locker")

    assert KdePresence(answer(True)).session_locked() is True
    assert KdePresence(answer(False)).session_locked() is False
    assert KdePresence(answer("yes")).session_locked() is None
    assert KdePresence(fail).session_locked() is None
    assert KdePresence(fail).idle_seconds() is None


# -- the real desktop, read-only ------------------------------------------------


def _plasma_runs() -> bool:
    if "KDE" not in os.environ.get("XDG_CURRENT_DESKTOP", "").upper().split(":"):
        return False
    try:
        tools.call(
            tools.run,
            "org.kde.plasmashell",
            "/PlasmaShell",
            "org.kde.PlasmaShell.color",
        )
    except WallpaperError:
        return False
    return True


real_plasma = pytest.mark.skipif(not _plasma_runs(), reason="needs KDE Plasma")


@pytest.mark.kde
@real_plasma
def test_real_monitors() -> None:
    monitors = KdeBackend().monitors()
    assert monitors
    assert len({m.id for m in monitors}) == len(monitors)
    for monitor in monitors:
        assert monitor.name
        if monitor.rect is not None:
            assert monitor.rect.width > 0 and monitor.rect.height > 0
            assert min(monitor.rect.size) > 0


@pytest.mark.kde
@real_plasma
def test_real_presence() -> None:
    assert KdePresence().session_locked() is False  # running tests: not locked
