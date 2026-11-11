"""The tray menu drawn from an Overview, and what its items do."""

from contextlib import nullcontext
from pathlib import Path

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from wallpaper_py.app.icon import IconState, app_icon
from wallpaper_py.app.tray import Tray, fill_menu, icon_state, tooltip
from wallpaper_py.app.worker import ServiceHost, ServiceSettings
from wallpaper_py.commands import MonitorView, Overview, PlaylistView, Request

from .fakes import FakeAutostart, FakeBackend, FakePresence

WALLS = PlaylistView(
    "walls",
    (MonitorView("AW3225QF", True), MonitorView("PLX2783H", False)),
    current=Path("D:/pictures/a & b.jpg"),
    favourite=True,
)
STRIP = PlaylistView(
    "strip", (MonitorView("ZeroMOD", None),), current=None, favourite=False
)


class Actions:
    """Records what the menu asks for (MenuActions)."""

    def __init__(self) -> None:
        self.calls: list[object] = []
        self.autostart = False

    def run(self, request: Request) -> None:
        self.calls.append(request)

    def open_image(self, image: Path) -> None:
        self.calls.append(image)

    def reload(self) -> None:
        self.calls.append("reload")

    def open_config_folder(self) -> None:
        self.calls.append("folder")

    def autostart_enabled(self) -> bool:
        return self.autostart

    def set_autostart(self, enabled: bool) -> None:
        self.calls.append(("autostart", enabled))

    def quit(self) -> None:
        self.calls.append("quit")


def items(menu: QMenu) -> list[QAction]:
    return [a for a in menu.actions() if not a.isSeparator()]


def find(menu: QMenu, text: str, after: str = "") -> QAction:
    """The first item called `text` (after the one called `after`)."""
    texts = [a.text() for a in items(menu)]
    start = texts.index(after) if after else 0
    return items(menu)[texts.index(text, start)]


@pytest.fixture(name="menu")
def fixture_menu() -> QMenu:
    return QMenu()


def test_flat_sections(menu: QMenu) -> None:
    fill_menu(menu, Overview((WALLS, STRIP), paused="idle"), Actions())
    assert [(a.text(), a.isEnabled()) for a in items(menu)] == [
        ("walls · AW3225QF + PLX2783H (off)", False),
        ("a && b.jpg", True),
        ("Next", True),
        ("Previous", True),
        ("Favourite", True),
        ("Remove (list and skip)", True),
        ("strip · ZeroMOD (not found)", False),
        ("Nothing shown yet", False),
        ("Next", False),
        ("Previous", False),
        ("Favourite", False),
        ("Remove (list and skip)", False),
        ("Paused: idle", False),
        ("Reload config", True),
        ("Open config folder", True),
        ("Start at login", True),
        ("Quit", True),
    ]
    assert find(menu, "Favourite").isChecked()
    assert not find(menu, "Favourite", after="strip · ZeroMOD (not found)").isChecked()


def test_items_queue_commands(menu: QMenu) -> None:
    actions = Actions()
    fill_menu(menu, Overview((WALLS, STRIP)), actions)
    for text in (
        "a && b.jpg",
        "Next",
        "Previous",
        "Favourite",
        "Remove (list and skip)",
    ):
        find(menu, text).trigger()
    assert actions.calls == [
        WALLS.current,
        Request("next", "walls"),
        Request("prev", "walls"),
        Request("unfav", "walls"),  # already a favourite: the toggle unmarks
        Request("remove", "walls"),
    ]


def test_favourite_marks_when_not_marked(menu: QMenu) -> None:
    actions = Actions()
    plain = PlaylistView(WALLS.name, WALLS.monitors, WALLS.current, favourite=False)
    fill_menu(menu, Overview((plain,)), actions)
    favourite = find(menu, "Favourite")
    assert not favourite.isChecked()
    favourite.trigger()
    assert actions.calls == [Request("fav", "walls")]


def test_app_items(menu: QMenu) -> None:
    actions = Actions()
    actions.autostart = True
    autostart = fill_menu(menu, Overview(), actions)
    assert autostart.isChecked()
    for text in ("Reload config", "Open config folder", "Start at login", "Quit"):
        find(menu, text).trigger()
    assert actions.calls == ["reload", "folder", ("autostart", False), "quit"]


def test_starting_and_error_states(menu: QMenu) -> None:
    fill_menu(menu, None, Actions())
    assert items(menu)[0].text() == "Starting..."
    fill_menu(menu, Overview(error="No config file at C:/x/config.toml"), Actions())
    assert items(menu)[0].text() == "Error: No config file at C:/x/config.toml"
    assert not items(menu)[0].isEnabled()


def test_long_names_are_elided_in_the_middle(menu: QMenu) -> None:
    long = PlaylistView("p", (), Path("x" * 100 + ".png"), False)
    fill_menu(menu, Overview((long,)), Actions())
    text = items(menu)[1].text()
    assert len(text) == 60 and "…" in text and text.endswith(".png")


def test_tooltip_and_icon_state() -> None:
    assert tooltip(None) == "wallpaper_py\nStarting..."
    assert tooltip(Overview((WALLS, STRIP), paused="idle")) == (
        "wallpaper_py\nwalls: a & b.jpg\nstrip: -\nPaused: idle"
    )
    assert icon_state(Overview(error="x")) is IconState.ERROR
    assert icon_state(Overview(paused="idle")) is IconState.PAUSED
    assert icon_state(Overview()) is IconState.NORMAL
    for state in IconState:
        assert not app_icon(state).isNull()


def test_tray_follows_the_overview(tmp_path: Path) -> None:
    host = ServiceHost(  # never started: requests just queue up
        ServiceSettings(tmp_path, lambda: FakeBackend([]), FakePresence, nullcontext)
    )
    autostart = FakeAutostart()
    quits: list[bool] = []
    tray = Tray(host, autostart, ["py", "tray"], tmp_path, lambda: quits.append(True))
    assert items(tray.menu)[0].text() == "Starting..."

    host.overview_changed.emit(Overview((WALLS,)))
    assert items(tray.menu)[0].text() == "walls · AW3225QF + PLX2783H (off)"

    find(tray.menu, "Start at login").trigger()
    assert autostart.command == ["py", "tray"]
    find(tray.menu, "Start at login").trigger()
    assert autostart.command is None
    find(tray.menu, "Quit").trigger()
    assert quits == [True]
