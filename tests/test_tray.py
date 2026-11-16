"""The tray menu drawn from an Overview, and what its items do."""

from collections.abc import Callable
from contextlib import nullcontext
from importlib import resources
from pathlib import Path

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QImage, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from wallpaper_py.app.icon import IconState, app_icon
from wallpaper_py.app.tray import (
    NO_PLAYLISTS_NOTE,
    Tray,
    activate,
    fill_menu,
    icon_state,
    tooltip,
)
from wallpaper_py.app.ui.launcher import PageName
from wallpaper_py.app.worker import ServiceSettings, ServiceThread
from wallpaper_py.core.model import MonitorId
from wallpaper_py.protocol import (
    ErrorReply,
    MarkRequest,
    MonitorView,
    Overview,
    PlaylistView,
    Reply,
    Request,
    TargetCommand,
    target_request,
)

from .fakes import FakeAutostart, FakeBackend, FakePresence


def monitor(name: str, attached: bool | None) -> MonitorView:
    return MonitorView(id=MonitorId(name), name=name, attached=attached)


WALLS = PlaylistView(
    id="walls-id",
    name="walls",
    directory=Path("D:/pictures"),
    monitors=(monitor("AW3225QF", True), monitor("PLX2783H", False)),
    current=Path("D:/pictures/a & b.jpg"),
    favourite=True,
)
STRIP = PlaylistView(
    id="strip-id",
    name="strip",
    directory=Path("D:/strip"),
    monitors=(monitor("ZeroMOD", None),),
    current=None,
    favourite=False,
    manual=True,
)


def queued(command: TargetCommand, target: str) -> Request | MarkRequest:
    return target_request(command, target)


class Actions:
    """Records what the menu asks for (MenuActions)."""

    def __init__(self) -> None:
        self.calls: list[object] = []
        self.autostart = False

    def run(self, request: Request | MarkRequest) -> None:
        self.calls.append(request)

    def open_image(self, image: Path) -> None:
        self.calls.append(image)

    def open_window(self, page: PageName) -> None:
        self.calls.append(("page", page))

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
        ("Open wallpaper_py", True),
        ("Library", True),
        ("Settings…", True),
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
    assert actions.calls == [queued("resume", "walls-id")]  # held: the toggle resumes

    fill(menu, Overview(playlists=(WALLS,)), actions)
    find(menu, "Hold").trigger()
    assert actions.calls[-1] == queued("hold", "walls-id")


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
        queued("next", "walls-id"),
        queued("prev", "walls-id"),
        queued("unfav", "walls-id"),  # already a favourite: the toggle unmarks
        queued("remove", "walls-id"),
    ]


def test_favourite_marks_when_not_marked(menu: QMenu) -> None:
    actions = Actions()
    plain = WALLS.model_copy(update={"favourite": False})
    fill(menu, Overview(playlists=(plain,)), actions)
    favourite = find(menu, "Favourite")
    assert not favourite.isChecked()
    favourite.trigger()
    assert actions.calls == [queued("fav", "walls-id")]


def test_favourite_is_off_while_the_list_is_unknown(menu: QMenu) -> None:
    """favourites.txt not read yet: neither fav nor unfav is known to fit."""
    unknown = WALLS.model_copy(update={"favourite": None})
    fill(menu, Overview(playlists=(unknown,)), Actions())
    favourite = find(menu, "Favourite")
    assert not favourite.isChecked() and not favourite.isEnabled()


def test_app_items(menu: QMenu) -> None:
    actions = Actions()
    actions.autostart = True
    autostart = fill(menu, Overview(), actions)
    assert autostart.isChecked()
    for text in (
        "Open wallpaper_py",
        "Library",
        "Settings…",
        "Start at login",
        "Quit",
    ):
        find(menu, text).trigger()
    assert actions.calls == [
        ("page", "home"),
        ("page", "library"),
        ("page", "settings"),
        ("autostart", False),
        "quit",
    ]


def test_starting_and_error_states(menu: QMenu) -> None:
    fill(menu, None, Actions())
    assert items(menu)[0].text() == "Starting..."
    fill(menu, Overview(error="C:/x/favourites.txt is not UTF-8 text"), Actions())
    assert items(menu)[0].text() == "Error: C:/x/favourites.txt is not UTF-8 text"
    assert not items(menu)[0].isEnabled()


def test_no_playlists_item_opens_settings(menu: QMenu) -> None:
    actions = Actions()
    fill(menu, Overview(), actions)
    first = items(menu)[0]
    assert first.text() == "No playlists yet: add one…"
    first.trigger()
    assert actions.calls == [("page", "settings")]
    for overview in (
        Overview(error="favourites.txt is not UTF-8 text"),
        Overview(playlists=(WALLS,)),
    ):
        fill_menu(menu, overview, False)
        assert "No playlists yet: add one…" not in [a.text() for a in items(menu)]


def test_long_names_are_elided_in_the_middle(menu: QMenu) -> None:
    long = PlaylistView(
        id="p",
        name="p",
        directory=Path("."),
        monitors=(),
        current=Path("x" * 100 + ".png"),
        favourite=False,
    )
    fill(menu, Overview(playlists=(long,)), Actions())
    text = items(menu)[1].text()
    assert len(text) == 60 and "…" in text and text.endswith(".png")


def test_tooltip_and_icon_state() -> None:
    assert tooltip(None) == "wallpaper_py\nStarting..."
    assert tooltip(Overview()) == "wallpaper_py\nNo playlists yet"
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


def test_app_icon_keeps_transparency_and_tray_states() -> None:
    size = QSize(64, 64)
    normal = app_icon().pixmap(size).toImage()
    paused = app_icon(IconState.PAUSED).pixmap(size).toImage()
    error = app_icon(IconState.ERROR).pixmap(size).toImage()
    source = QPixmap.fromImage(
        QImage.fromData(
            resources.files("wallpaper_py.app")
            .joinpath("icons", "app-icon.png")
            .read_bytes()
        )
    )
    expected = source.scaled(
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )

    assert normal == expected.toImage()
    assert normal.pixelColor(0, 0).alpha() == 0
    assert paused.pixelColor(0, 0).alpha() == 0
    grey = paused.pixelColor(32, 32)
    assert grey.red() == grey.green() == grey.blue()
    assert error.pixelColor(54, 48).name() == "#e03c31"


def test_tray_follows_the_overview() -> None:
    service_thread = ServiceThread(  # never started: requests just queue up
        ServiceSettings(lambda: FakeBackend([]), FakePresence, nullcontext)
    )
    autostart = FakeAutostart()
    quits: list[bool] = []
    pages: list[PageName] = []
    tray = Tray(
        service_thread,
        autostart,
        ["py", "tray"],
        lambda: quits.append(True),
        open_window=pages.append,
    )
    assert items(tray.menu)[0].text() == "Starting..."

    service_thread.overview_changed.emit(Overview(playlists=(WALLS,)))
    assert items(tray.menu)[0].text() == "walls · AW3225QF + PLX2783H (off)"

    find(tray.menu, "Start at login").trigger()
    assert autostart.command == ["py", "tray"]
    find(tray.menu, "Start at login").trigger()
    assert autostart.command is None
    find(tray.menu, "Open wallpaper_py").trigger()
    find(tray.menu, "Library").trigger()
    find(tray.menu, "Settings…").trigger()
    assert pages == ["home", "library", "settings"]
    find(tray.menu, "Quit").trigger()
    assert quits == [True]


def test_left_click_opens_home_window() -> None:
    service_thread = ServiceThread(
        ServiceSettings(lambda: FakeBackend([]), FakePresence, nullcontext)
    )
    pages: list[PageName] = []
    tray = Tray(
        service_thread, FakeAutostart(), [], lambda: None, open_window=pages.append
    )

    tray._activated(  # pylint: disable=protected-access
        QSystemTrayIcon.ActivationReason.Trigger
    )

    assert pages == ["home"]


def test_tray_notifies_failed_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_thread = ServiceThread(
        ServiceSettings(lambda: FakeBackend([]), FakePresence, nullcontext)
    )
    error = ErrorReply.make("not_attached", "None attached")

    def submit(_: Request, callback: Callable[[Reply], None]) -> None:
        callback(error)  # as the service thread would answer

    monkeypatch.setattr(service_thread, "submit", submit)
    tray = Tray(service_thread, FakeAutostart(), [], lambda: None, open_window=print)
    notes: list[str] = []
    monkeypatch.setattr(tray, "notify", notes.append)
    tray.run(queued("next", "walls-id"))
    assert notes == ["None attached"]


def test_starting_without_playlists_notifies_once_and_opens_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_thread = ServiceThread(
        ServiceSettings(lambda: FakeBackend([]), FakePresence, nullcontext)
    )
    pages: list[PageName] = []
    tray = Tray(
        service_thread, FakeAutostart(), [], lambda: None, open_window=pages.append
    )
    shown: list[str] = []
    monkeypatch.setattr(tray, "_show_message", lambda m, _: shown.append(m))
    service_thread.overview_changed.emit(
        Overview(error="favourites.txt is not UTF-8 text")
    )  # not started yet
    service_thread.overview_changed.emit(Overview())
    service_thread.overview_changed.emit(Overview())
    assert shown == ["favourites.txt is not UTF-8 text", NO_PLAYLISTS_NOTE]  # once
    tray._message_clicked()  # pylint: disable=protected-access
    assert pages == ["settings"]


def test_other_notifications_open_nothing_when_clicked() -> None:
    service_thread = ServiceThread(
        ServiceSettings(lambda: FakeBackend([]), FakePresence, nullcontext)
    )
    pages: list[PageName] = []
    tray = Tray(
        service_thread, FakeAutostart(), [], lambda: None, open_window=pages.append
    )
    service_thread.overview_changed.emit(Overview())  # the no-playlists note
    tray.notify("None attached")  # shown over it
    tray._message_clicked()  # pylint: disable=protected-access
    assert not pages


def test_starting_with_playlists_does_not_notify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_thread = ServiceThread(
        ServiceSettings(lambda: FakeBackend([]), FakePresence, nullcontext)
    )
    tray = Tray(service_thread, FakeAutostart(), [], lambda: None, open_window=print)
    shown: list[str] = []
    monkeypatch.setattr(tray, "_show_message", lambda m, _: shown.append(m))
    service_thread.overview_changed.emit(Overview(playlists=(WALLS,)))
    service_thread.overview_changed.emit(Overview())  # the playlists deleted later
    assert not shown
