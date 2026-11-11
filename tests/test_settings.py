"""
The settings window, through a fake Client (never a real app): it edits
the file the app runs, Save / Revert / close-with-changes, problems next to
their fields, conflicts with hand edits, monitors from the drawing's menu,
and the About page.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import QMenu
from pytestqt.qtbot import QtBot

from wallpaper_py.app import ipc
from wallpaper_py.app.ui.about import existing_folder, version_text
from wallpaper_py.app.ui.client import ReplyCallback, SocketClient
from wallpaper_py.app.ui.settings import Choice, Dialogs, SettingsWindow
from wallpaper_py.app.ui.settings_form import PlaylistEditor
from wallpaper_py.core import paths
from wallpaper_py.core.config_edit import DIRECTORY, IDLE, INTERVAL, MONITORS, NAME
from wallpaper_py.protocol import (
    DesktopMonitor,
    ErrorReply,
    Geometry,
    Overview,
    ReloadRequest,
    ReloadResult,
    Reply,
    Request,
    StatusResult,
)

from .fake_client import FakeClient
from .fakes import FakeBackend, make_monitor, write_config
from .hosts import make_host
from .test_config_edit import COMMENTED

AW = DesktopMonitor(
    id=r"\\?\DISPLAY#DELA246#1",
    name="AW3225QF",
    geometry=Geometry(x=0, y=0, width=3840, height=2160),
)
PLX = DesktopMonitor(
    id=r"\\?\DISPLAY#PLX#1",
    name="PLX2783H",
    geometry=Geometry(x=-1920, y=0, width=1920, height=1080),
)
ZERO = DesktopMonitor(id=r"\\?\DISPLAY#AUS27B1#1", name="ZeroMOD", geometry=None)
LISTED = (AW, PLX, ZERO)


class FakeDialogs:
    """The modal questions, answered by the test (and recorded)."""

    def __init__(self) -> None:
        self.unsaved_answer: Choice = "discard"
        self.asked = 0
        self.folder: str | None = None
        self.list_file: str | None = None

    def dialogs(self) -> Dialogs:
        return Dialogs(
            unsaved=self._unsaved,
            folder=lambda _parent, _start: self.folder,
            list_file=lambda _parent, _start: self.list_file,
        )

    def _unsaved(self, _parent: object) -> Choice:
        self.asked += 1
        return self.unsaved_answer


@pytest.fixture(name="config")
def fixture_config(tmp_path: Path) -> Path:
    """The commented example, on folders that exist (relative to the file)."""
    folder = tmp_path / "custom"  # not the default config: the app's
    for name in ("konachan", "fantasy"):
        (folder / name).mkdir(parents=True)
    text = COMMENTED.replace("D:\\pictures\\KONACHAN", "konachan").replace(
        "D:\\pictures\\Fantasy", "fantasy"
    )
    path = folder / "config.toml"
    path.write_bytes(text.encode())
    return path


@pytest.fixture(name="client")
def fixture_client(config: Path) -> FakeClient:
    client = FakeClient()
    client.publish(Overview(monitors=LISTED, config=config))
    return client


@pytest.fixture(name="dialogs")
def fixture_dialogs() -> FakeDialogs:
    return FakeDialogs()


@pytest.fixture(name="opened")
def fixture_opened() -> list[QUrl]:
    return []


@pytest.fixture(name="window")
def fixture_window(
    qtbot: QtBot, client: FakeClient, dialogs: FakeDialogs, opened: list[QUrl]
) -> Iterator[SettingsWindow]:
    window = SettingsWindow(
        client, dialogs.dialogs(), version="1.0 (abc1234)", open_url=opened.append
    )
    qtbot.addWidget(window)
    yield window


def editor(window: SettingsWindow, name: str) -> PlaylistEditor:
    return next(e for e in window.editors.values() if e.name_edit.text() == name)


def changes(old: str, new: str) -> list[str]:
    return [
        line
        for line in difflib.ndiff(old.splitlines(), new.splitlines())
        if line[:2] in ("- ", "+ ")
    ]


def find(menu: QMenu, text: str) -> QAction:
    return next(a for a in menu.actions() if a.text() == text)


def reloads(client: FakeClient) -> list[ReplyCallback]:
    return [c for r, c in client.calls if isinstance(r, ReloadRequest)]


# -- loading -------------------------------------------------------------------------


def test_edits_the_file_the_app_runs(window: SettingsWindow, config: Path) -> None:
    assert window.path == config  # from the overview, not the default config
    assert config != paths.config_file()
    assert [e.name_edit.text() for e in window.editors.values()] == [
        "konachan",
        "fantasy",
    ]
    konachan = editor(window, "konachan")
    assert konachan.directory_edit.text() == "konachan"
    assert konachan.interval_edit.text() == "30m"
    assert konachan.monitors_label.text() == "AW3225QF + PLX2783H"
    fantasy = editor(window, "fantasy")
    assert fantasy.manual_check.isChecked() and not fantasy.interval_edit.isEnabled()
    assert fantasy.monitors_label.text() == "ZeroMOD (off)"
    assert window.locked_check.isChecked() and window.idle_edit.text() == "5m"
    # Nothing changed: nothing to save or revert.
    assert not window.save_button.isEnabled() and not window.revert_button.isEnabled()
    assert window.save_button.toolTip() == "Nothing changed"
    assert not window.isWindowModified()


def test_waits_for_the_app_to_say_which_file(
    qtbot: QtBot, dialogs: FakeDialogs, config: Path
) -> None:
    client = FakeClient()
    window = SettingsWindow(client, dialogs.dialogs(), version="1")
    qtbot.addWidget(window)
    assert window.banner.text() == "Connecting to wallpaper_py…"
    assert not window.editors and not window.add_button.isEnabled()
    client.publish(Overview(monitors=LISTED, config=config))
    assert window.path == config and len(window.editors) == 2
    assert window.banner.isHidden()


def test_a_missing_or_unreadable_file_is_explained(
    qtbot: QtBot, dialogs: FakeDialogs, tmp_path: Path
) -> None:
    client = FakeClient()
    missing = tmp_path / "none.toml"
    client.publish(Overview(config=missing, error=f"No config file at {missing}"))
    window = SettingsWindow(client, dialogs.dialogs(), version="1")
    qtbot.addWidget(window)
    assert "There is no config file at" in window.banner.text()
    assert not window.banner_button.isHidden() and not window.editors
    assert not window.save_button.isEnabled()

    missing.write_text("[playlists.a\n", encoding="utf-8")  # not TOML
    window.banner_button.click()
    assert (
        "Can't edit" in window.banner.text()
        and "Not valid TOML" in window.banner.text()
    )

    missing.write_text(
        "[playlists.a]\ndirectory = '.'\nmonitors = ['ZeroMOD']\ninterval = '5s'\n",
        encoding="utf-8",
    )
    window.banner_button.click()  # valid TOML the core refuses: fix it here
    assert window.editors[0].problems[INTERVAL].message == "At least 10s"
    # Loaded; what the app said last shows again (until a save reloads it).
    assert window.banner.text().startswith("The app can't use the config: No config")
    assert window.banner_button.isHidden()


# -- saving --------------------------------------------------------------------------


def test_save_writes_only_the_change_then_reloads(
    window: SettingsWindow, client: FakeClient, config: Path
) -> None:
    before = config.read_text(encoding="utf-8")
    editor(window, "konachan").interval_edit.setText("1h")
    assert window.isWindowModified()
    assert window.save_button.isEnabled() and window.revert_button.isEnabled()
    window.save_button.click()
    after = config.read_text(encoding="utf-8")
    assert changes(before, after) == [
        '- interval = "30m"                     # active time since the last change',
        '+ interval = "1h"                      # active time since the last change',
    ]
    assert not window.save_button.isEnabled()  # saved: nothing changed now
    assert window.banner.text() == "Saved; asking the app to reload it…"

    [reply] = reloads(client)
    reply(ReloadResult(warnings=("interval is shorter than pause_when_idle",)))
    assert window.banner.text() == (
        "Saved; the app is using it.\n"
        "Warning: interval is shorter than pause_when_idle"
    )
    # A second edit diffs against what was saved.
    editor(window, "konachan").interval_edit.setText("2h")
    window.save_button.click()
    assert changes(after, config.read_text(encoding="utf-8"))[1].startswith(
        '+ interval = "2h"'
    )


def test_the_apps_error_after_a_save_shows(
    window: SettingsWindow, client: FakeClient
) -> None:
    editor(window, "konachan").interval_edit.setText("1h")
    window.save_button.click()
    reloads(client)[0](ErrorReply.make("config_error", "it broke"))
    assert window.banner.text() == "Saved, but the app reports: it broke"
    client.publish(Overview(monitors=LISTED, config=window.path, error="still bad"))
    assert window.banner.text() == "The app can't use the config: still bad"


def test_revert(window: SettingsWindow, config: Path) -> None:
    konachan = editor(window, "konachan")
    konachan.name_edit.setText("anime")
    window.locked_check.setChecked(False)
    window.revert_button.click()
    assert [e.name_edit.text() for e in window.editors.values()] == [
        "konachan",
        "fantasy",
    ]
    assert window.locked_check.isChecked()
    assert not window.revert_button.isEnabled() and not window.dirty()
    assert config.read_text(encoding="utf-8").count("[playlists.konachan]") == 1


def test_problems_show_at_their_fields_and_keep_save_off(
    window: SettingsWindow, config: Path
) -> None:
    before = config.read_bytes()
    konachan = editor(window, "konachan")
    konachan.interval_edit.setText("30")
    assert konachan.problems[INTERVAL].message == (
        "Use a duration like 45s, 30m, 1h30m or 1d"
    )
    assert not window.save_button.isEnabled()
    why = (
        "Can't save yet. konachan, interval: Use a duration like 45s, 30m, 1h30m or 1d"
    )
    assert window.save_button.toolTip() == why == window.status.text()

    konachan.directory_edit.setText("missing")
    assert konachan.problems[DIRECTORY].message == (
        f"No such folder: {config.parent / 'missing'}"
    )
    assert window.status.text().endswith("(and 1 more)")
    editor(window, "fantasy").name_edit.setText("my strip")
    assert editor(window, "my strip").problems[NAME].message == (
        "Use only letters, digits, - and _ (no spaces)"
    )
    window.idle_edit.setText("soon")
    assert window.general_problems[IDLE].message is not None

    window.save_button.click()  # disabled: nothing happens
    assert window.save() is False
    assert config.read_bytes() == before

    konachan.interval_edit.setText("30m")
    konachan.directory_edit.setText("konachan")
    editor(window, "my strip").name_edit.setText("strip")
    window.idle_edit.setText("10m")
    assert window.problems == {} and window.save_button.isEnabled()


def test_a_hand_edit_meanwhile_is_not_overwritten(
    window: SettingsWindow, config: Path
) -> None:
    editor(window, "konachan").interval_edit.setText("1h")
    hand_edited = config.read_text(encoding="utf-8") + "# added by hand\n"
    config.write_bytes(hand_edited.encode())
    window.save_button.click()
    assert config.read_text(encoding="utf-8") == hand_edited  # untouched
    assert "changed on disk since this window read it" in window.banner.text()
    assert not window.banner_button.isHidden()
    assert window.dirty()  # the edits are still here

    window.banner_button.click()  # load the file again: the edits go
    assert not window.dirty() and window.banner.isHidden()
    assert editor(window, "konachan").interval_edit.text() == "30m"
    editor(window, "konachan").interval_edit.setText("1h")
    window.save_button.click()
    saved = config.read_text(encoding="utf-8")
    assert '"1h"' in saved and saved.endswith("# added by hand\n")


@pytest.mark.parametrize("answer", ["cancel", "discard", "save"])
def test_closing_with_unsaved_changes_asks(
    window: SettingsWindow,
    dialogs: FakeDialogs,
    config: Path,
    client: FakeClient,
    answer: Choice,
) -> None:
    before = config.read_text(encoding="utf-8")
    editor(window, "konachan").interval_edit.setText("1h")
    dialogs.unsaved_answer = answer
    window.show()
    closed = window.close()
    assert dialogs.asked == 1
    assert closed == (answer != "cancel")
    written = config.read_text(encoding="utf-8")
    assert (written != before) == (answer == "save")
    assert bool(reloads(client)) == (answer == "save")


def test_closing_unchanged_or_unsavable(
    window: SettingsWindow, dialogs: FakeDialogs
) -> None:
    window.show()
    assert window.close() and dialogs.asked == 0  # nothing changed: no question
    window.show()
    editor(window, "konachan").interval_edit.setText("x")
    dialogs.unsaved_answer = "save"
    assert not window.close()  # can't be saved: it stays open, and says why
    assert window.status.text().startswith("Can't save yet.")


# -- monitors ------------------------------------------------------------------------


def test_a_monitor_moves_to_another_playlist(
    window: SettingsWindow, config: Path
) -> None:
    before = config.read_text(encoding="utf-8")
    menu = window.monitor_menu(PLX.id)
    assert menu is not None
    assert [(a.text(), a.isChecked()) for a in menu.actions() if a.text()] == [
        ("PLX2783H", False),  # its name, as a title
        ("konachan", True),
        ("fantasy", False),
        ("No playlist", False),
        ("New playlist…", False),
    ]
    find(menu, "fantasy").trigger()
    assert editor(window, "konachan").monitors_label.text() == "AW3225QF"
    assert editor(window, "fantasy").monitors_label.text() == "ZeroMOD (off) + PLX2783H"
    window.save_button.click()
    assert changes(before, config.read_text(encoding="utf-8")) == [
        '- monitors = ["AW3225QF", "PLX2783H"]',
        '+ monitors = ["AW3225QF"]',
        '- monitors = ["ZeroMOD"]              # the strip',
        '+ monitors = ["ZeroMOD", "PLX2783H"]  # the strip',
    ]


def test_no_playlist_and_new_playlist(
    window: SettingsWindow, dialogs: FakeDialogs, config: Path
) -> None:
    menu = window.monitor_menu(ZERO.id)
    assert menu is not None
    find(menu, "No playlist").trigger()
    fantasy = editor(window, "fantasy")
    assert fantasy.problems[MONITORS].message == (
        "No monitor: choose one, or delete the playlist"
    )
    assert not window.save_button.isEnabled()

    menu = window.monitor_menu(ZERO.id)
    assert menu is not None
    find(menu, "New playlist…").trigger()
    new = editor(window, "playlist")
    assert new.monitors_label.text() == "ZeroMOD (off)"
    assert new.problems[DIRECTORY].message == "Choose a folder"
    fantasy.delete_button.click()
    assert "fantasy" not in [e.name_edit.text() for e in window.editors.values()]
    dialogs.folder = str(config.parent / "fantasy")
    new.browse_button.click()
    new.name_edit.setText("strip")
    assert window.problems == {}
    window.save_button.click()
    text = config.read_text(encoding="utf-8")
    assert "[playlists.fantasy]" not in text
    assert text.endswith(
        "[playlists.strip]\n"
        f"directory = '{config.parent / 'fantasy'}'\n"
        'monitors = ["ZeroMOD"]\n'
        'order = "shuffle"\n'
        'interval = "30m"\n'
        'mode = "fill"\n'
    )


def test_a_monitor_not_connected_stays_until_removed(
    window: SettingsWindow, client: FakeClient
) -> None:
    client.publish(Overview(monitors=(AW, PLX), config=window.path))  # no ZeroMOD
    fantasy = editor(window, "fantasy")
    assert fantasy.monitors_label.text() == "ZeroMOD (not connected)"
    assert "not connected now: ZeroMOD (fantasy)" in window.unlisted.text()
    assert not window.dirty()

    window.fill_monitors_menu(fantasy.monitors_menu, fantasy.uid)
    items = [(a.text(), a.isChecked()) for a in fantasy.monitors_menu.actions()]
    assert items == [
        ("AW3225QF — now in konachan", False),
        ("PLX2783H — now in konachan", False),
        ("ZeroMOD (not connected)", True),
    ]
    find(fantasy.monitors_menu, "AW3225QF — now in konachan").trigger()
    assert fantasy.monitors_label.text() == "ZeroMOD (not connected) + AW3225QF"
    assert editor(window, "konachan").monitors_label.text() == "PLX2783H"
    window.fill_monitors_menu(fantasy.monitors_menu, fantasy.uid)
    find(fantasy.monitors_menu, "ZeroMOD (not connected)").trigger()
    assert fantasy.monitors_label.text() == "AW3225QF"
    assert window.unlisted.isHidden()


def test_add_playlist_button(
    window: SettingsWindow, dialogs: FakeDialogs, config: Path
) -> None:
    window.add_button.click()
    new = editor(window, "playlist")
    assert set(new.problems) >= {DIRECTORY, MONITORS}
    assert new.problems[MONITORS].message is not None
    dialogs.folder = str(config.parent / "konachan")
    new.browse_button.click()
    assert new.directory_edit.text() == str(config.parent / "konachan")
    window.fill_monitors_menu(new.monitors_menu, new.uid)
    find(new.monitors_menu, "PLX2783H — now in konachan").trigger()
    assert window.problems == {}
    window.save_button.click()
    assert "[playlists.playlist]" in config.read_text(encoding="utf-8")


# -- general -------------------------------------------------------------------------


def test_general_settings(
    window: SettingsWindow, dialogs: FakeDialogs, config: Path
) -> None:
    before = config.read_text(encoding="utf-8")
    window.idle_check.setChecked(False)
    assert not window.idle_edit.isEnabled()
    dialogs.list_file = str(config.parent / "favs.txt")
    window.list_buttons["favourites_file"].click()
    window.save_button.click()
    assert changes(before, config.read_text(encoding="utf-8")) == [
        '- pause_when_idle = "5m"',
        "+ pause_when_idle = false",
        f"+ favourites_file = '{config.parent / 'favs.txt'}'",
    ]
    favourites = next(l for l in window.about.locations if l.label == "Favourites")
    assert favourites.path == config.parent / "favs.txt"  # what the file says now


# -- about ---------------------------------------------------------------------------


def test_about_lists_the_data_locations(
    window: SettingsWindow, config: Path, opened: list[QUrl]
) -> None:
    about = window.about
    assert about.version.text() == "Version 1.0 (abc1234)"
    assert [(l.label, l.path) for l in about.locations] == [
        ("Config file", config),
        ("State database", paths.state_db()),
        ("Favourites", paths.data_dir() / "favourites.txt"),
        ("To remove", paths.data_dir() / "to-remove.txt"),
        ("App log", paths.log_file()),
        ("Windows log", paths.ui_log_file()),
        ("Rendered wallpapers", paths.render_dir()),
        ("Thumbnail cache", paths.thumbnail_dir()),
    ]
    buttons = {
        b.accessibleName(): b for b in about.findChildren(type(window.save_button))
    }
    buttons["Copy the path of: State database"].click()
    assert QGuiApplication.clipboard().text() == str(paths.state_db())
    buttons["Open the folder of: Config file"].click()
    # The data folders don't exist in the test: the nearest one that does.
    buttons["Open the folder of: Thumbnail cache"].click()
    assert [u.toLocalFile() for u in opened] == [
        QUrl.fromLocalFile(str(config.parent)).toLocalFile(),
        QUrl.fromLocalFile(str(existing_folder(paths.thumbnail_dir()))).toLocalFile(),
    ]


def test_existing_folder(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    assert existing_folder(tmp_path / "a") == tmp_path / "a"
    assert existing_folder(tmp_path / "a" / "x.txt") == tmp_path / "a"
    assert existing_folder(tmp_path / "a" / "b" / "c") == tmp_path / "a"


def test_version_text() -> None:
    text = version_text()
    assert text and text[0].isdigit()  # "0.3.0", then the commit in a checkout


# -- with the app ----------------------------------------------------------------------


def test_edits_and_reloads_the_running_apps_config(
    qtbot: QtBot, tmp_path: Path, dialogs: FakeDialogs
) -> None:
    config = write_config(tmp_path)  # a custom config, not the default
    name = ipc.server_name(tmp_path)
    backend = FakeBackend([make_monitor("Main")])
    host = make_host(config, lambda: backend, name)
    client = SocketClient(name, None, None)  # no --config: the app's
    try:
        with qtbot.waitSignal(host.overview_changed):
            host.start()
        window = SettingsWindow(client, dialogs.dialogs(), version="1")
        qtbot.addWidget(window)
        with qtbot.waitSignal(client.overview_changed):
            client.start()
        assert window.path == config
        assert editor(window, "walls").monitors_label.text() == "Main"

        editor(window, "walls").interval_edit.setText("1h")
        window.save_button.click()
        qtbot.waitUntil(lambda: window.banner.text() == "Saved; the app is using it.")
        replies: list[Reply] = []
        client.run(Request(command="status"), replies.append)
        qtbot.waitUntil(lambda: bool(replies))
        status = replies[0]
        assert isinstance(status, StatusResult)
        assert status.playlists[0].interval == 3600  # the app runs the new file
    finally:
        client.close()
        assert host.shutdown(5_000)
