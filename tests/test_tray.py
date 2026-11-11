"""The tray menu drawn from an Overview, and what its items do."""

from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path

import pytest
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from wallpaper_py.app.icon import IconState, app_icon
from wallpaper_py.app.tray import Tray, activate, fill_menu, icon_state, tooltip
from wallpaper_py.app.ui.launcher import WindowKind
from wallpaper_py.app.worker import ServiceHost, ServiceSettings
from wallpaper_py.protocol import (
    CommandRequest,
    ErrorReply,
    MonitorView,
    Overview,
    PlaylistView,
    ReloadRequest,
    ReloadResult,
    Reply,
    Request,
)

from .fakes import FakeAutostart, FakeBackend, FakePresence


def monitor(name: str, attached: bool | None) -> MonitorView:
    return MonitorView(selector=name, name=name, attached=attached)


WALLS = PlaylistView(
    name="walls",
    monitors=(monitor("AW3225QF", True), monitor("PLX2783H", False)),
    current=Path("D:/pictures/a & b.jpg"),
    favourite=True,
)
STRIP = PlaylistView(
    name="strip",
    monitors=(monitor("ZeroMOD", None),),
    current=None,
    favourite=False,
    manual=True,
)


def queued(command: str, target: str) -> Request:
    return Request.model_validate({"command": command, "target": target})


class Actions:
    """Records what the menu asks for (MenuActions)."""

    def __init__(self) -> None:
        self.calls: list[object] = []
        self.autostart = False

    def run(self, request: Request) -> None:
        self.calls.append(request)

    def open_image(self, image: Path) -> None:
        self.calls.append(image)

    def open_window(self, window: WindowKind) -> None:
        self.calls.append(("window", window))

    def reload(self) -> None:
        self.calls.append("reload")

    def open_config_folder(self) -> None:
        self.calls.append("folder")

    def triggered(self, action: QAction) -> None:
        """Connected to QMenu.triggered, as in Tray."""
        activate(action, self)

    def set_autostart(self, enabled: bool) -> None:
        self.calls.append(("autostart", enabled))

    def quit(self) -> None:
        self.calls.append("quit")


def fill(menu: QMenu, overview: Overview | None, actions: Actions) -> QAction:
    """The menu as the tray builds it, its items going to `actions`."""
    menu.triggered.connect(actions.triggered)
    return fill_menu(menu, overview, actions.autostart)


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
    fill(menu, Overview(playlists=(WALLS, STRIP), paused="idle"), Actions())
    assert [(a.text(), a.isEnabled()) for a in items(menu)] == [
        ("walls · AW3225QF + PLX2783H (off)", False),
        ("a && b.jpg", True),
        ("Next", True),
        ("Previous", True),
        ("Favourite", True),
        ("Remove (list and skip)", True),
        ("Hold", True),
        ("strip (manual) · ZeroMOD (not found)", False),
        ("Nothing shown yet", False),
        ("Next", False),
        ("Previous", False),
        ("Favourite", False),
        ("Remove (list and skip)", False),
        # no Hold: a manual playlist never rotates
        ("Paused: idle", False),
        ("Open layout", True),
        ("Open library", True),
        ("Settings…", True),
        ("Reload config", True),
        ("Open config folder", True),
        ("Start at login", True),
        ("Quit", True),
    ]
    assert find(menu, "Favourite").isChecked()
    strip = "strip (manual) · ZeroMOD (not found)"
    assert not find(menu, "Favourite", after=strip).isChecked()
    assert find(menu, "Hold").isCheckable() and not find(menu, "Hold").isChecked()


def test_hold_toggle(menu: QMenu) -> None:
    actions = Actions()
    held = WALLS.model_copy(update={"held": True})
    fill(menu, Overview(playlists=(held,)), actions)
    assert items(menu)[0].text() == "walls (held) · AW3225QF + PLX2783H (off)"
    hold = find(menu, "Hold")
    assert hold.isChecked() and hold.isEnabled()
    hold.trigger()
    assert actions.calls == [queued("resume", "walls")]  # held: the toggle resumes

    fill(menu, Overview(playlists=(WALLS,)), actions)
    find(menu, "Hold").trigger()
    assert actions.calls[-1] == queued("hold", "walls")


def test_items_queue_commands(menu: QMenu) -> None:
    actions = Actions()
    fill(menu, Overview(playlists=(WALLS, STRIP)), actions)
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
        queued("next", "walls"),
        queued("prev", "walls"),
        queued("unfav", "walls"),  # already a favourite: the toggle unmarks
        queued("remove", "walls"),
    ]


def test_favourite_marks_when_not_marked(menu: QMenu) -> None:
    actions = Actions()
    plain = WALLS.model_copy(update={"favourite": False})
    fill(menu, Overview(playlists=(plain,)), actions)
    favourite = find(menu, "Favourite")
    assert not favourite.isChecked()
    favourite.trigger()
    assert actions.calls == [queued("fav", "walls")]


def test_app_items(menu: QMenu) -> None:
    actions = Actions()
    actions.autostart = True
    autostart = fill(menu, Overview(), actions)
    assert autostart.isChecked()
    for text in (
        "Open layout",
        "Open library",
        "Reload config",
        "Open config folder",
        "Start at login",
        "Quit",
    ):
        find(menu, text).trigger()
    assert actions.calls == [
        ("window", "layout"),
        ("window", "library"),
        "reload",
        "folder",
        ("autostart", False),
        "quit",
    ]


def test_starting_and_error_states(menu: QMenu) -> None:
    fill(menu, None, Actions())
    assert items(menu)[0].text() == "Starting..."
    fill(menu, Overview(error="No config file at C:/x/config.toml"), Actions())
    assert items(menu)[0].text() == "Error: No config file at C:/x/config.toml"
    assert not items(menu)[0].isEnabled()


def test_long_names_are_elided_in_the_middle(menu: QMenu) -> None:
    long = PlaylistView(
        name="p", monitors=(), current=Path("x" * 100 + ".png"), favourite=False
    )
    fill(menu, Overview(playlists=(long,)), Actions())
    text = items(menu)[1].text()
    assert len(text) == 60 and "…" in text and text.endswith(".png")


def test_tooltip_and_icon_state() -> None:
    assert tooltip(None) == "wallpaper_py\nStarting..."
    assert tooltip(Overview(playlists=(WALLS, STRIP), paused="idle")) == (
        "wallpaper_py\nwalls: a & b.jpg\nstrip (manual): -\nPaused: idle"
    )
    held = WALLS.model_copy(update={"held": True})
    assert (
        tooltip(Overview(playlists=(held,))) == "wallpaper_py\nwalls (held): a & b.jpg"
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
    windows: list[WindowKind] = []
    tray = Tray(
        host,
        autostart,
        ["py", "tray"],
        tmp_path,
        lambda: quits.append(True),
        open_window=windows.append,
    )
    assert items(tray.menu)[0].text() == "Starting..."

    host.overview_changed.emit(Overview(playlists=(WALLS,)))
    assert items(tray.menu)[0].text() == "walls · AW3225QF + PLX2783H (off)"

    find(tray.menu, "Start at login").trigger()
    assert autostart.command == ["py", "tray"]
    find(tray.menu, "Start at login").trigger()
    assert autostart.command is None
    find(tray.menu, "Open library").trigger()
    assert windows == ["library"]
    find(tray.menu, "Quit").trigger()
    assert quits == [True]


def test_tray_notifies_failed_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = ServiceHost(
        ServiceSettings(tmp_path, lambda: FakeBackend([]), FakePresence, nullcontext)
    )
    error = ErrorReply.make("not_attached", "None attached")

    def submit(_: Request, callback: Callable[[Reply], None]) -> None:
        callback(error)  # as the service thread would answer

    monkeypatch.setattr(host, "submit", submit)
    tray = Tray(host, FakeAutostart(), [], tmp_path, lambda: None, open_window=print)
    notes: list[str] = []
    monkeypatch.setattr(tray, "notify", notes.append)
    tray.run(queued("next", "walls"))
    assert notes == ["None attached"]


def test_reload_config_notifies_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = ServiceHost(
        ServiceSettings(tmp_path, lambda: FakeBackend([]), FakePresence, nullcontext)
    )
    pending: list[tuple[CommandRequest, Callable[[Reply], None]]] = []
    monkeypatch.setattr(host, "submit", lambda r, c: pending.append((r, c)))
    tray = Tray(host, FakeAutostart(), [], tmp_path, lambda: None, open_window=print)
    notes: list[str] = []
    monkeypatch.setattr(tray, "notify", notes.append)

    find(tray.menu, "Reload config").trigger()
    assert len(pending) == 1
    request, reply = pending[0]
    assert request == ReloadRequest()  # the `reload` command, as from the CLI
    # The new overview comes first (the reload publishes it), then the reply:
    # one notification, from the reply, even for an error seen before.
    host.overview_changed.emit(Overview(error="bad config"))
    reply(ErrorReply.make("config_error", "bad config"))
    assert notes == ["bad config"]

    find(tray.menu, "Reload config").trigger()
    pending[-1][1](ReloadResult(warnings=("w1", "w2")))
    assert notes == ["bad config", "w1\nw2"]
    find(tray.menu, "Reload config").trigger()
    pending[-1][1](ReloadResult())
    assert len(notes) == 2  # fine: nothing to say

    host.overview_changed.emit(Overview(error="another"))  # not from a reload
    assert notes[-1] == "another"
