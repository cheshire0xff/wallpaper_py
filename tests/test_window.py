"""
The main window: the sidebar (its selected page and the app's status),
opening and switching pages (unsaved settings stay), closing with unsaved
settings (Save / Discard / Cancel), and the tones from the palette, derived
again when it changes. Through a fake Client (never a real app).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from wallpaper_py.app.ui import theme
from wallpaper_py.app.ui.settings import Choice, SettingsPage
from wallpaper_py.app.ui.window import MainWindow, status_of
from wallpaper_py.protocol import Overview

from .fake_client import FakeClient
from .test_settings import FakeDialogs, editor, reloads
from .test_settings import fixture_config  # noqa: F401  pylint: disable=unused-import
from .test_settings import LISTED
from .test_theme import DARK, LIGHT
from .test_theme import fixture_app_palette  # noqa: F401  pylint: disable=unused-import


@pytest.fixture(name="client")
def fixture_client(config: Path) -> FakeClient:
    client = FakeClient()
    client.publish(Overview(monitors=LISTED, config=config))
    return client


@pytest.fixture(name="dialogs")
def fixture_dialogs() -> FakeDialogs:
    return FakeDialogs()


@pytest.fixture(name="window")
def fixture_window(
    qtbot: QtBot, client: FakeClient, dialogs: FakeDialogs
) -> Iterator[MainWindow]:
    settings = SettingsPage(client, dialogs.dialogs(), version="1")
    window = MainWindow(client, settings=settings)
    qtbot.addWidget(window)
    yield window
    window.home.shutdown()


# -- pages and the sidebar ---------------------------------------------------------


def test_starts_on_home_with_its_sidebar_item_selected(window: MainWindow) -> None:
    assert window.current_page() == "home"
    assert window.stack.currentWidget() is window.home
    selected = [p for p, b in window.sidebar.buttons.items() if b.isChecked()]
    assert selected == ["home"]
    assert [b.text() for b in window.sidebar.buttons.values()] == [
        "Home",
        "Library",
        "Settings",
    ]
    assert window.windowTitle() == "wallpaper_py[*]"


@pytest.mark.parametrize("page", ["home", "library", "settings"])
def test_open_page_shows_the_window_on_it(
    qtbot: QtBot, window: MainWindow, page: str
) -> None:
    window.open_page(page)
    qtbot.waitExposed(window)
    assert window.isVisible() and window.current_page() == page
    assert window.stack.currentWidget() is window.pages[page]
    assert window.sidebar.buttons[page].isChecked()


def test_open_page_brings_a_minimised_window_back(
    qtbot: QtBot, window: MainWindow
) -> None:
    window.open_page("home")
    window.showMinimized()
    window.open_page("library")
    qtbot.waitUntil(lambda: not window.isMinimized())
    assert window.current_page() == "library"


def test_an_unknown_page_shows_home(window: MainWindow) -> None:
    window.show_page("settings")
    window.show_page("kitchen")
    assert window.current_page() == "home"


def test_clicking_the_sidebar_switches_pages(window: MainWindow) -> None:
    window.show()
    window.sidebar.buttons["settings"].click()
    assert window.current_page() == "settings"
    assert window.stack.currentWidget() is window.settings
    window.sidebar.buttons["library"].click()
    assert window.stack.currentWidget() is window.library
    assert not window.sidebar.buttons["settings"].isChecked()
    window.sidebar.buttons["library"].grab()  # paints the selected item


def test_homes_add_button_switches_to_settings(window: MainWindow) -> None:
    window.show()  # the client's overview has no playlists
    assert window.home.empty.isVisible()
    window.home.add_button.click()
    assert window.current_page() == "settings"


def dot(window: MainWindow) -> str:
    """What the status dot shows: ok, paused, error, connecting."""
    return window.sidebar.dot.kind


def test_the_sidebar_status_follows_the_app(
    window: MainWindow, client: FakeClient
) -> None:
    sidebar = window.sidebar
    assert sidebar.status.text() == "Rotation active"
    assert dot(window) == "ok"
    client.publish(Overview(paused="idle"))
    assert sidebar.status.text() == "Rotation paused: idle"
    assert dot(window) == "paused"
    client.publish(Overview(error="No config file"))
    assert sidebar.status.text() == "Config error"
    assert sidebar.status.toolTip() == "No config file"
    assert dot(window) == "error"
    client.failed.emit("could not start wallpaper_py")
    assert sidebar.status.text() == "Not connected"
    assert sidebar.status.toolTip() == "could not start wallpaper_py"
    sidebar.grab()


def test_status_of() -> None:
    assert status_of(None) == ("Connecting…", "connecting")
    assert status_of(Overview()) == ("Rotation active", "ok")
    assert status_of(Overview(paused="locked")) == ("Rotation paused: locked", "paused")
    assert status_of(Overview(error="x", paused="idle")) == ("Config error", "error")


def test_switching_pages_keeps_unsaved_settings(window: MainWindow) -> None:
    window.open_page("settings")
    editor(window.settings, "konachan").interval_edit.setText("1h")
    assert window.isWindowModified()  # "*" in the title
    window.sidebar.buttons["home"].click()
    window.sidebar.buttons["library"].click()
    window.open_page("settings")
    assert editor(window.settings, "konachan").interval_edit.text() == "1h"
    assert window.settings.dirty() and window.isWindowModified()
    window.settings.revert_button.click()
    assert not window.isWindowModified()


# -- closing -----------------------------------------------------------------------


@pytest.mark.parametrize("answer", ["cancel", "discard", "save"])
def test_closing_with_unsaved_settings_asks(
    window: MainWindow,
    dialogs: FakeDialogs,
    client: FakeClient,
    config: Path,
    answer: Choice,
) -> None:
    before = config.read_text(encoding="utf-8")
    window.open_page("settings")
    editor(window.settings, "konachan").interval_edit.setText("1h")
    window.show_page("home")  # the question brings Settings back
    dialogs.unsaved_answer = answer
    closed: list[bool] = []
    window.closed.connect(lambda: closed.append(True))
    assert window.close() == (answer != "cancel")
    assert dialogs.asked == 1
    assert closed == ([] if answer == "cancel" else [True])
    written = config.read_text(encoding="utf-8")
    assert (written != before) == (answer == "save")
    assert bool(reloads(client)) == (answer == "save")
    if answer == "cancel":  # open, on Settings, the edit still there
        assert window.isVisible() and window.current_page() == "settings"
        assert editor(window.settings, "konachan").interval_edit.text() == "1h"


def test_closing_without_changes_does_not_ask(
    window: MainWindow, dialogs: FakeDialogs
) -> None:
    window.open_page("library")
    with_closed: list[bool] = []
    window.closed.connect(lambda: with_closed.append(True))
    assert window.close() and with_closed == [True]
    assert dialogs.asked == 0


# -- tones -------------------------------------------------------------------------


def tones_of(window: MainWindow) -> theme.Tones:
    tones = window.applied_tones()
    assert tones is not None
    return tones


def save_colour(window: MainWindow) -> object:
    """Save's fill while it can be clicked (disabled, it is a plain button)."""
    palette = window.settings.save_button.palette()
    return palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Button)


def test_tones_come_from_the_palette_and_follow_it(
    qtbot: QtBot,
    client: FakeClient,
    app_palette: Callable[[QPalette], None],
) -> None:
    app_palette(DARK)
    window = MainWindow(client, settings=SettingsPage(client, version="1"))
    qtbot.addWidget(window)
    window.show()
    dark = theme.derive_tones(QApplication.palette())
    assert window.applied_tones() == dark
    palette = window.palette()
    assert palette.color(QPalette.ColorRole.Window) == dark.page
    assert palette.color(QPalette.ColorRole.Base) == dark.field
    assert save_colour(window) == dark.accent
    # The system switches to light: everything is derived again.
    app_palette(LIGHT)
    qtbot.waitUntil(lambda: not tones_of(window).dark)
    light = theme.derive_tones(QApplication.palette())
    assert window.applied_tones() == light
    assert window.palette().color(QPalette.ColorRole.Window) == light.page
    qtbot.waitUntil(lambda: save_colour(window) == light.accent)
    window.grab()  # every page paints with the new tones
    window.home.shutdown()
