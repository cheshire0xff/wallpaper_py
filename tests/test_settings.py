"""
The Settings page, through a fake Client (never a real app): it loads the
saved settings with `settings`, edits them monitor-first and saves them
with `save_settings`; Save / Revert / close-with-changes, problems next to
their fields, one playlist open at a time, the About tab. At the end, the
same with the real daemon's service and socket.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import QColorDialog, QStyleFactory, QWidget
from pytestqt.qtbot import QtBot

from wallpaper_py.app import ipc
from wallpaper_py.app.ui import theme
from wallpaper_py.app.ui.about import existing_folder, version_text
from wallpaper_py.app.ui.client import ReplyCallback, SocketClient
from wallpaper_py.app.ui.controls import Segments
from wallpaper_py.app.ui.settings import SettingsPage
from wallpaper_py.app.ui.settings_dialogs import Choice, Dialogs
from wallpaper_py.app.ui.settings_draft import COLOUR
from wallpaper_py.app.ui.settings_form import PlaylistEditor, combo, line_edit
from wallpaper_py.app.ui.thumbnails import Sizes, ThumbnailLoader
from wallpaper_py.core import paths
from wallpaper_py.core.model import Background, ImageMode, Look, MonitorId
from wallpaper_py.core.settings import (
    MonitorSettings,
    Order,
    PlaylistSettings,
    Settings,
)
from wallpaper_py.protocol import (
    CommandRequest,
    DesktopMonitor,
    ErrorReply,
    Geometry,
    MonitorView,
    Overview,
    PlaylistView,
    Reply,
    Request,
    SaveSettingsRequest,
    SaveSettingsResult,
    SettingsRequest,
    SettingsResult,
    StatusResult,
)

from .fake_client import FakeClient
from .fakes import FakeBackend, make_monitor, write_settings
from .hosts import make_service_thread

AW = DesktopMonitor(
    id=MonitorId("aw-id"),
    name="AW3225QF",
    geometry=Geometry(x=0, y=0, width=3840, height=2160),
)
PLX = DesktopMonitor(
    id=MonitorId("plx-id"),
    name="PLX2783H",
    geometry=Geometry(x=-1920, y=0, width=1920, height=1080),
)
ZERO = DesktopMonitor(id=MonitorId("zero-id"), name="ZeroMOD", geometry=None)
U27 = MonitorId(r"\\?\DISPLAY#DELA1B2#5&1&0&UID9#{guid}")  # not connected
LISTED = (AW, PLX, ZERO)
REVISION = 3


class FakeDialogs:
    """The modal questions, answered by the test (and recorded)."""

    def __init__(self) -> None:
        self.unsaved_answer: Choice = "discard"
        self.asked = 0
        self.folder: str | None = None
        self.list_file: str | None = None
        # The colour dialog, run by the test: it may move the colour; True: OK.
        self.colour: Callable[[QColorDialog], bool] = lambda _dialog: False

    def dialogs(self) -> Dialogs:
        return Dialogs(
            unsaved=self._unsaved,
            folder=lambda _parent, _start: self.folder,
            list_file=lambda _parent, _start: self.list_file,
            colour=self._colour,
        )

    def _colour(self, dialog: QColorDialog) -> bool:
        return self.colour(dialog)

    def _unsaved(self, _parent: object) -> Choice:
        self.asked += 1
        return self.unsaved_answer


@pytest.fixture(name="saved")
def fixture_saved(tmp_path: Path) -> Settings:
    """konachan on AW + PLX, fantasy (manual) on ZeroMOD (off, fit)."""
    for name in ("konachan", "fantasy"):
        (tmp_path / name).mkdir()
    konachan = PlaylistSettings(
        id="konachan-id",
        name="konachan",
        directory=tmp_path / "konachan",
        interval=1500.0,
    )
    fantasy = PlaylistSettings(
        id="fantasy-id",
        name="fantasy",
        directory=tmp_path / "fantasy",
        order=Order.SORTED,
        interval=None,
    )
    return Settings(
        playlists=(konachan, fantasy),
        monitors=(
            MonitorSettings(id=AW.id, playlist="konachan-id"),
            MonitorSettings(id=PLX.id, playlist="konachan-id"),
            MonitorSettings(
                id=ZERO.id, playlist="fantasy-id", look=Look(ImageMode.FIT)
            ),
            MonitorSettings(id=U27, look=Look(ImageMode.STRETCH)),
        ),
    )


@pytest.fixture(name="client")
def fixture_client() -> FakeClient:
    client = FakeClient()
    client.publish(Overview(monitors=LISTED))
    return client


@pytest.fixture(name="dialogs")
def fixture_dialogs() -> FakeDialogs:
    return FakeDialogs()


@pytest.fixture(name="opened")
def fixture_opened() -> list[QUrl]:
    return []


@pytest.fixture(name="loader")
def fixture_loader() -> Iterator[ThumbnailLoader]:
    """The window's, for a page made without one (the test closes it)."""
    loader = ThumbnailLoader(Sizes.at(1.0))
    yield loader
    loader.close()


@pytest.fixture(name="window")
def fixture_window(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    qtbot: QtBot,
    client: FakeClient,
    dialogs: FakeDialogs,
    opened: list[QUrl],
    saved: Settings,
    loader: ThumbnailLoader,
) -> Iterator[SettingsPage]:
    window = SettingsPage(
        client,
        dialogs.dialogs(),
        loader=loader,
        version="1.0 (abc1234)",
        open_url=opened.append,
    )
    qtbot.addWidget(window)
    answer(client, SettingsRequest, SettingsResult(settings=saved, revision=REVISION))
    yield window


def requests[R: CommandRequest](
    client: FakeClient, kind: type[R]
) -> list[tuple[R, ReplyCallback]]:
    return [(r, c) for r, c in client.calls if isinstance(r, kind)]


def answer(client: FakeClient, kind: type[CommandRequest], reply: Reply) -> None:
    """Answer the last request of this kind."""
    requests(client, kind)[-1][1](reply)


def sent(client: FakeClient) -> SaveSettingsRequest:
    """The last save sent."""
    return requests(client, SaveSettingsRequest)[-1][0]


def choose_value(choice: Segments, value: object) -> None:
    """Click the choice with this value, as the user does."""
    choice.button(choice.find_value(value)).click()


def editor(window: SettingsPage, name: str) -> PlaylistEditor:
    return next(e for e in window.editors.values() if e.name_edit.text() == name)


def labels(window: SettingsPage) -> list[str]:
    box = window.monitor_combo
    return [box.itemText(i) for i in range(box.count())]


def choose(window: SettingsPage, monitor: MonitorId, playlist: str) -> None:
    """Select `monitor` and put it on `playlist` (an id, "" for none, or "new")."""
    window.select_monitor(monitor)
    box = window.playlist_combo
    box.setCurrentIndex(box.findData(playlist))


# -- loading -------------------------------------------------------------------------


def test_loads_the_saved_settings(window: SettingsPage, tmp_path: Path) -> None:
    assert [e.name_edit.text() for e in window.editors.values()] == [
        "konachan",
        "fantasy",
    ]
    konachan = editor(window, "konachan")
    assert konachan.directory_edit.text() == str(tmp_path / "konachan")
    assert konachan.interval_edit.text() == "25m"
    assert konachan.shown_label.text() == "PLX2783H + AW3225QF"
    fantasy = editor(window, "fantasy")
    assert fantasy.changes_combo.currentText() == "Manually"
    assert fantasy.interval_edit.isHidden()
    assert fantasy.order_choice.current_text() == "Sorted"
    assert fantasy.shown_label.text() == "ZeroMOD (switched off)"
    # Closed: a summary each (monitors · order · interval).
    assert window.open_id is None and not konachan.details.isVisibleTo(window)
    assert fantasy.summary.summary == "ZeroMOD (switched off) · sorted · manually"
    assert window.locked_switch.isChecked() and window.idle_edit.text() == "5m"
    # Attached left to right, then switched off, then not connected.
    assert labels(window) == [
        "PLX2783H",
        "AW3225QF",
        "ZeroMOD · switched off",
        "DELA1B2 · not connected",
    ]
    assert window.selected == PLX.id  # the first one
    assert window.monitors_view.selected() == PLX.id
    # Only the attached ones are drawn.
    assert [p.monitor.id for p in window.monitors_view.placed()] == [AW.id, PLX.id]
    # Nothing changed: nothing to save or revert.
    assert not window.save_button.isEnabled() and not window.revert_button.isEnabled()
    assert window.save_button.toolTip() == "Nothing changed"
    assert not window.dirty() and window.status.text() == "No unsaved changes."


def test_waits_for_the_daemon_and_reads_again_on_request(
    qtbot: QtBot, dialogs: FakeDialogs, saved: Settings, loader: ThumbnailLoader
) -> None:
    client = FakeClient()
    client.publish(Overview(error="favourites.txt is not UTF-8 text"))
    window = SettingsPage(client, dialogs.dialogs(), loader=loader, version="1")
    qtbot.addWidget(window)
    assert not requests(client, SettingsRequest)  # the service isn't running
    assert (
        window.banner.text() == "The app has stopped: favourites.txt is not UTF-8 text"
    )
    assert not window.add_button.isEnabled()

    client.publish(Overview(monitors=LISTED))
    assert window.banner.isHidden()
    answer(client, SettingsRequest, ErrorReply.make("unreachable", "not running"))
    assert window.banner.text() == "Can't read the settings: not running"
    assert window.banner_button.isVisibleTo(window)
    window.banner_button.click()
    assert len(requests(client, SettingsRequest)) == 2
    answer(client, SettingsRequest, SettingsResult(settings=saved, revision=1))
    assert window.banner.isHidden() and len(window.editors) == 2


# -- the selected monitor ------------------------------------------------------------


def test_a_monitor_is_set_up_by_clicking_or_choosing_it(window: SettingsPage) -> None:
    window.monitors_view.clicked.emit(AW.id)  # a click on the drawing
    assert window.selected == AW.id and window.monitor_combo.currentText() == "AW3225QF"
    assert window.playlist_combo.currentText() == "konachan"
    assert window.playlist_row.description.text() == (
        "Mirrors PLX2783H: the same image, changed together"
    )
    assert window.look_editor.mode_choice.current_text() == "Fill"
    assert window.away_row.isHidden()

    window.monitor_combo.setCurrentIndex(2)  # by keyboard: ZeroMOD, switched off
    assert window.selected == ZERO.id
    assert window.playlist_combo.currentText() == "fantasy"
    assert window.playlist_row.description.text() == (
        "Pick the playlist of another monitor to mirror it"
    )
    assert window.look_editor.mode_choice.current_text() == "Fit"
    assert window.look_editor.mode_row.description.text() == (
        "Shows the whole image; bars fill the rest"
    )
    assert window.away_row.isVisibleTo(window)
    assert window.away_row.label.text() == "Switched off"
    assert not window.dirty()  # looking changes nothing


def test_a_monitor_changes_playlist_then_save(
    window: SettingsPage, client: FakeClient, saved: Settings
) -> None:
    choose(window, AW.id, "fantasy-id")
    assert window.playlist_row.description.text() == (
        "Mirrors ZeroMOD (switched off): the same image, changed together"
    )
    assert editor(window, "konachan").shown_label.text() == "PLX2783H"
    assert window.status.text() == "Changes: AW3225QF shows fantasy. Not saved yet."
    assert window.revert_button.isEnabled()
    window.save_button.click()
    request = sent(client)
    assert request.revision == REVISION
    aw, plx, zero, u27 = saved.monitors
    assert request.settings == saved.model_copy(
        update={
            "monitors": (
                aw.model_copy(update={"playlist": "fantasy-id"}),
                plx,
                zero,
                u27,
            )
        }
    )
    # While the daemon saves: nothing more to send.
    assert not window.save_button.isEnabled() and window.status.text() == "Saving…"
    answer(client, SaveSettingsRequest, SaveSettingsResult(revision=REVISION + 1))
    assert not window.dirty() and window.status.text() == "No unsaved changes."
    assert window.banner.isHidden()
    # The next save goes on the new revision.
    choose(window, AW.id, "")
    assert window.status.text() == "Changes: AW3225QF shows no playlist. Not saved yet."
    window.save_button.click()
    assert sent(client).revision == REVISION + 1


def test_a_monitors_mode(window: SettingsPage, client: FakeClient) -> None:
    window.select_monitor(AW.id)
    choose_value(window.look_editor.mode_choice, ImageMode.STRETCH)
    assert window.look_editor.mode_row.description.text() == (
        "Fills the screen; the image changes its shape"
    )
    assert window.status.text() == "Changes: mode of AW3225QF. Not saved yet."
    window.save_button.click()
    [aw] = [m for m in sent(client).settings.monitors if m.id == AW.id]
    assert aw.look == Look(ImageMode.STRETCH)
    # Back to fill: no change (and nothing new is stored for it).
    choose_value(window.look_editor.mode_choice, ImageMode.FILL)
    window.select_monitor(PLX.id)
    assert window.look_editor.mode_choice.current_text() == "Fill"


def edited_look(window: SettingsPage, monitor: MonitorId) -> Look:
    edited = window.edited()
    assert edited is not None
    return edited.monitor(monitor).look


def edge_colour(window: SettingsPage, monitor: MonitorId) -> tuple[int, int, int]:
    """The colour at the middle of the left edge of `monitor`'s preview."""
    pixmap = window.monitors_view.preview(monitor)
    assert pixmap is not None
    image = pixmap.toImage()
    colour = image.pixelColor(1, image.height() // 2)
    return colour.red(), colour.green(), colour.blue()


def test_the_previews_show_the_edit(
    qtbot: QtBot, window: SettingsPage, client: FakeClient, tmp_path: Path
) -> None:
    """Each monitor shows its playlist's current image in its look as edited."""
    image = tmp_path / "konachan" / "square.png"
    red = (200, 40, 40)
    Image.new("RGB", (900, 900), red).save(image)
    members = tuple(MonitorView(id=m.id, name=m.name, attached=True) for m in (AW, PLX))
    playlist = PlaylistView(
        id="konachan-id",
        name="konachan",
        directory=image.parent,
        monitors=members,
        current=image,
        favourite=False,
    )
    client.publish(Overview(playlists=(playlist,), monitors=LISTED))
    view = window.monitors_view
    try:
        qtbot.waitUntil(lambda: view.preview(AW.id) is not None)
        assert edge_colour(window, AW.id) == red  # fill: no bars
        # Fit, not saved yet: a bar on the left at once.
        window.select_monitor(AW.id)
        choose_value(window.look_editor.mode_choice, ImageMode.FIT)
        assert edge_colour(window, AW.id) == (0, 0, 0)
        assert edge_colour(window, PLX.id) == red  # only that monitor
        # Another folder: its image is unknown until saved.
        other = tmp_path / "other"
        other.mkdir()
        editor(window, "konachan").directory_edit.setText(str(other))
        assert view.preview(AW.id) is None
        window.revert()
        assert edge_colour(window, AW.id) == red
        # A monitor put on a new playlist has nothing to show yet.
        choose(window, PLX.id, "new")
        assert view.preview(PLX.id) is None
        window.grab()  # paints
    finally:
        window.loader.close()


def show_square(client: FakeClient, tmp_path: Path, rgb: tuple[int, int, int]) -> None:
    """konachan (AW + PLX) shows a square image of one colour."""
    image = tmp_path / "konachan" / "square.png"
    Image.new("RGB", (900, 900), rgb).save(image)
    members = tuple(MonitorView(id=m.id, name=m.name, attached=True) for m in (AW, PLX))
    playlist = PlaylistView(
        id="konachan-id",
        name="konachan",
        directory=image.parent,
        monitors=members,
        current=image,
        favourite=False,
    )
    client.publish(Overview(playlists=(playlist,), monitors=LISTED))


def test_a_fit_monitors_background(  # pylint: disable=too-many-statements
    qtbot: QtBot,
    window: SettingsPage,
    client: FakeClient,
    dialogs: FakeDialogs,
    tmp_path: Path,
) -> None:
    """Only for Fit; the image's edges, or a colour from the dialog, previewed."""
    red, black, blue = (200, 40, 40), (0, 0, 0), (0x33, 0x66, 0xCC)
    show_square(client, tmp_path, red)
    qtbot.waitUntil(lambda: window.monitors_view.preview(AW.id) is not None)
    window.select_monitor(AW.id)
    row, box, swatch = (
        window.look_editor.background_row,
        window.look_editor.background_combo,
        window.look_editor.colour_button,
    )
    assert row.isHidden()  # fill
    choose_value(window.look_editor.mode_choice, ImageMode.FIT)
    assert not row.isHidden() and not swatch.isHidden()
    assert box.currentText() == "Colour" and swatch.colour() == "#000000"
    assert edge_colour(window, AW.id) == black
    # The image's edges: the bars beside the square are red; no swatch.
    box.setCurrentIndex(box.findData(Background.EDGES))
    assert swatch.isHidden()
    assert edge_colour(window, AW.id) == red
    assert window.status.text() == (
        "Changes: mode of AW3225QF, background of AW3225QF. Not saved yet."
    )
    # Fill hides it; back to Fit, it is still there.
    choose_value(window.look_editor.mode_choice, ImageMode.FILL)
    assert row.isHidden()
    choose_value(window.look_editor.mode_choice, ImageMode.FIT)
    assert box.currentData() is Background.EDGES
    # A colour: black (none saved), then the dialog; the preview follows it
    # while it moves, and Cancel goes back.
    box.setCurrentIndex(box.findData(COLOUR))
    assert edge_colour(window, AW.id) == black
    seen = []

    def move(dialog: QColorDialog, ok: bool) -> bool:
        assert dialog.currentColor() == QColor(swatch.colour())  # starts there
        dialog.setCurrentColor(QColor("#3366cc"))
        seen.append(edge_colour(window, AW.id))
        return ok

    dialogs.colour = lambda dialog: move(dialog, False)
    swatch.click()
    assert seen == [blue]
    assert edge_colour(window, AW.id) == black and swatch.colour() == "#000000"
    dialogs.colour = lambda dialog: move(dialog, True)
    swatch.click()
    assert edge_colour(window, AW.id) == blue and swatch.colour() == "#3366cc"
    window.save_button.click()
    [aw] = [m for m in sent(client).settings.monitors if m.id == AW.id]
    assert aw.look == Look(ImageMode.FIT, "#3366cc")
    window.grab()  # paints


def test_blur_and_darkening(
    qtbot: QtBot, window: SettingsPage, client: FakeClient, tmp_path: Path
) -> None:
    """Sliders for the Blur background only; the preview follows them."""
    red = (200, 40, 40)
    show_square(client, tmp_path, red)
    qtbot.waitUntil(lambda: window.monitors_view.preview(AW.id) is not None)
    window.select_monitor(AW.id)
    rows = (window.look_editor.blur_row, window.look_editor.darken_row)
    choose_value(window.look_editor.mode_choice, ImageMode.FIT)
    assert all(row.isHidden() for row in rows)  # a colour
    box = window.look_editor.background_combo
    box.setCurrentIndex(box.findData(Background.BLUR))
    assert not any(row.isHidden() for row in rows)
    assert (
        window.look_editor.blur_slider.value() == 0
        and window.look_editor.darken_slider.value() == 5
    )
    assert edge_colour(window, AW.id) == (150, 30, 30)  # a quarter darker
    # Moved (as while dragged): the draft and the preview follow at once.
    window.look_editor.darken_slider.setValue(0)
    assert edge_colour(window, AW.id) == red
    window.look_editor.darken_slider.setValue(
        window.look_editor.darken_slider.maximum()
    )
    assert edited_look(window, AW.id).darken == 0.8
    assert edge_colour(window, AW.id) == (40, 8, 8)
    window.look_editor.blur_slider.setValue(4)  # twice the default radius
    assert edited_look(window, AW.id).blur == 0.01
    assert window.status.text() == (
        "Changes: mode of AW3225QF, background of AW3225QF, blur of AW3225QF, "
        "1 more. Not saved yet."
    )
    # Another background hides them, and keeps the values.
    box.setCurrentIndex(box.findData(Background.EDGES))
    assert all(row.isHidden() for row in rows)
    assert edited_look(window, AW.id).blur == 0.01
    # Another monitor shows its own.
    window.select_monitor(PLX.id)
    assert all(row.isHidden() for row in rows)
    assert window.look_editor.blur_slider.value() == 0
    window.select_monitor(AW.id)
    assert window.look_editor.blur_slider.value() == 4
    window.save_button.click()
    [aw] = [m for m in sent(client).settings.monitors if m.id == AW.id]
    assert aw.look == Look(ImageMode.FIT, Background.EDGES, blur=0.01, darken=0.8)


def test_a_look_edit_for_a_monitor_not_selected_is_dropped(
    window: SettingsPage,
) -> None:
    """
    The editor may still hold a monitor the page no longer selects (a
    reload while the colour dialog is open): its edit changes nothing.
    """
    window.select_monitor(AW.id)
    before = window.edited()
    window.look_editor.edited.emit(PLX.id, Look(ImageMode.FIT))
    assert window.edited() == before and not window.dirty()


def test_a_background_colour_is_kept(
    window: SettingsPage, dialogs: FakeDialogs
) -> None:
    """Back to Colour: the one picked; a monitor shown meanwhile keeps its own."""
    window.select_monitor(AW.id)
    choose_value(window.look_editor.mode_choice, ImageMode.FIT)
    box = window.look_editor.background_combo

    def pick(dialog: QColorDialog) -> bool:
        dialog.setCurrentColor(QColor("#3366cc"))
        return True

    dialogs.colour = pick
    window.look_editor.colour_button.click()
    box.setCurrentIndex(box.findData(Background.AVERAGE))
    box.setCurrentIndex(box.findData(COLOUR))
    assert edited_look(window, AW.id).background == "#3366cc"
    assert window.look_editor.colour_button.colour() == "#3366cc"

    def elsewhere(dialog: QColorDialog) -> bool:
        window.select_monitor(PLX.id)
        dialog.setCurrentColor(QColor("#ff0000"))
        return True

    plx = edited_look(window, PLX.id)
    dialogs.colour = elsewhere
    window.look_editor.colour_button.click()
    assert edited_look(window, PLX.id) == plx
    assert edited_look(window, AW.id).background == "#3366cc"


def test_a_new_playlist_from_a_monitor(
    window: SettingsPage, client: FakeClient, dialogs: FakeDialogs, tmp_path: Path
) -> None:
    choose(window, PLX.id, "new")
    new = editor(window, "New playlist")
    assert window.open_id == new.playlist_id and new.details.isVisibleTo(window)
    assert window.playlist_combo.currentText() == "New playlist"
    assert new.shown_label.text() == "PLX2783H"
    assert editor(window, "konachan").shown_label.text() == "AW3225QF"
    # No folder yet: Save is off, and the line beside it says why.
    assert new.problems["directory"].message == "Choose a folder"
    assert not window.save_button.isEnabled()
    assert window.status.text() == (
        "Can't save yet. New playlist, folder: Choose a folder"
    )
    (tmp_path / "art").mkdir()
    dialogs.folder = str(tmp_path / "art")
    new.browse_button.click()
    assert new.directory_edit.text() == str(tmp_path / "art")
    assert window.status.text() == (
        "Changes: PLX2783H shows New playlist, new playlist New playlist. "
        "Not saved yet."
    )
    window.save_button.click()
    playlists = sent(client).settings.playlists
    assert [p.name for p in playlists] == ["konachan", "fantasy", "New playlist"]
    assert playlists[2].directory == tmp_path / "art" and playlists[2].interval == 1800
    # A second one gets a free name.
    window.add_button.click()
    assert editor(window, "New playlist 2").expanded()


def test_forget_a_monitor_not_connected(
    window: SettingsPage, client: FakeClient
) -> None:
    window.select_monitor(U27)
    assert window.away_row.label.text() == "Not connected"
    assert window.look_editor.mode_choice.current_text() == "Stretch"
    window.forget_button.click()
    assert "DELA1B2 · not connected" not in labels(window)
    assert window.selected == PLX.id
    assert window.status.text() == "Changes: forget DELA1B2. Not saved yet."
    window.save_button.click()
    assert U27 not in [m.id for m in sent(client).settings.monitors]


def test_deleting_a_playlist_frees_its_monitors(
    window: SettingsPage, client: FakeClient
) -> None:
    fantasy = editor(window, "fantasy")
    fantasy.summary.click()  # open it
    fantasy.delete_button.click()
    assert [e.name_edit.text() for e in window.editors.values()] == ["konachan"]
    window.select_monitor(ZERO.id)
    assert window.playlist_combo.currentText() == "None"
    assert window.playlist_row.description.text() == "Nothing rotates on this monitor"
    assert window.status.text() == (
        "Changes: ZeroMOD shows no playlist, delete fantasy and its history. "
        "Not saved yet."
    )
    window.save_button.click()
    [zero] = [m for m in sent(client).settings.monitors if m.id == ZERO.id]
    assert zero.playlist is None and zero.look == Look(ImageMode.FIT)


# -- playlists -----------------------------------------------------------------------


def test_one_playlist_is_open_at_a_time(window: SettingsPage) -> None:
    konachan, fantasy = editor(window, "konachan"), editor(window, "fantasy")
    konachan.summary.click()
    assert window.open_id == konachan.playlist_id
    assert konachan.details.isVisibleTo(window) and konachan.summary.summary == ""
    assert konachan.summary.expanded
    fantasy.summary.click()
    assert window.open_id == fantasy.playlist_id
    assert not konachan.details.isVisibleTo(window)
    assert konachan.summary.summary == "PLX2783H + AW3225QF · shuffle · every 25m"
    fantasy.summary.click()  # closes it again
    assert window.open_id is None and not fantasy.details.isVisibleTo(window)


def test_problems_show_at_their_fields_and_keep_save_off(
    window: SettingsPage, tmp_path: Path
) -> None:
    konachan, fantasy = editor(window, "konachan"), editor(window, "fantasy")
    fantasy.name_edit.setText("Konachan")
    assert fantasy.problems["name"].message == "Another playlist is called konachan"
    assert fantasy.summary.problem
    konachan.interval_edit.setText("5s")
    assert konachan.problems["interval"].message == "At least 10s"
    konachan.directory_edit.setText(str(tmp_path / "gone"))
    assert konachan.problems["directory"].message == (
        f"No such folder: {tmp_path / 'gone'}"
    )
    assert not window.save_button.isEnabled()
    assert window.status.text() == (
        "Can't save yet. konachan, folder: No such folder: "
        f"{tmp_path / 'gone'} (and 2 more)"
    )
    window.idle_edit.setText("soon")
    assert window.general_problems["pause_when_idle"].message == (
        "Use a duration like 45s, 5m or 1h"
    )


def test_a_folder_gone_since_it_was_saved_does_not_block(
    window: SettingsPage, tmp_path: Path
) -> None:
    """A drive not plugged in must not keep anything else from being saved."""
    (tmp_path / "konachan").rmdir()
    editor(window, "fantasy").interval_edit.setText("1h")
    assert not editor(window, "konachan").problems["directory"].message


def test_changes_every_or_manually(window: SettingsPage, client: FakeClient) -> None:
    konachan = editor(window, "konachan")
    konachan.changes_combo.setCurrentIndex(1)  # Manually
    assert konachan.interval_edit.isHidden()
    assert konachan.changes_row.description.text() == (
        "Only next, previous or pick change it"
    )
    fantasy = editor(window, "fantasy")
    fantasy.changes_combo.setCurrentIndex(0)  # Every: the default interval
    assert fantasy.interval_edit.text() == "30m"
    window.save_button.click()
    konachan_saved, fantasy_saved = sent(client).settings.playlists
    assert konachan_saved.interval is None and fantasy_saved.interval == 1800


def test_the_status_line_counts_beyond_three_changes(window: SettingsPage) -> None:
    konachan = editor(window, "konachan")
    konachan.name_edit.setText("anime")
    choose_value(konachan.order_choice, Order.SORTED)
    konachan.interval_edit.setText("1h")
    window.locked_switch.setChecked(False)
    assert window.status.text() == (
        "Changes: name of konachan, order of konachan, interval of konachan, "
        "1 more. Not saved yet."
    )


def test_a_rename_keeps_the_id(window: SettingsPage, client: FakeClient) -> None:
    editor(window, "konachan").name_edit.setText(" anime ")
    window.save_button.click()
    anime = sent(client).settings.playlists[0]
    assert (anime.id, anime.name) == ("konachan-id", "anime")


def test_revert(window: SettingsPage) -> None:
    editor(window, "konachan").interval_edit.setText("1h")
    choose(window, AW.id, "")
    assert window.dirty()
    window.revert_button.click()
    assert not window.dirty()
    assert editor(window, "konachan").interval_edit.text() == "25m"
    window.select_monitor(AW.id)
    assert window.playlist_combo.currentText() == "konachan"


# -- saving --------------------------------------------------------------------------


def test_what_the_daemon_says_to_a_save(
    window: SettingsPage, client: FakeClient, saved: Settings
) -> None:
    editor(window, "konachan").interval_edit.setText("2m")
    window.save_button.click()
    answer(
        client,
        SaveSettingsRequest,
        SaveSettingsResult(revision=4, warnings=("konachan: the interval",)),
    )
    assert window.banner.text() == "Warning: konachan: the interval"
    assert not window.dirty()

    editor(window, "konachan").interval_edit.setText("3m")
    window.save_button.click()
    answer(
        client, SaveSettingsRequest, ErrorReply.make("invalid_settings", "Bad value")
    )
    assert window.banner.text() == "Not saved: Bad value"
    assert window.dirty() and window.save_button.isEnabled()  # try again

    window.save_button.click()
    answer(client, SaveSettingsRequest, ErrorReply.make("stale_settings", "changed"))
    assert window.banner.text().startswith("The settings changed since they were read")
    window.banner_button.click()  # load them again: the edits go
    answer(client, SettingsRequest, SettingsResult(settings=saved, revision=9))
    assert not window.dirty() and window.banner.isHidden()
    assert editor(window, "konachan").interval_edit.text() == "25m"


def test_closing_with_unsaved_changes_asks(
    window: SettingsPage, client: FakeClient, dialogs: FakeDialogs
) -> None:
    assert window.confirm_close() and dialogs.asked == 0  # nothing changed
    editor(window, "konachan").interval_edit.setText("1h")
    dialogs.unsaved_answer = "cancel"
    assert not window.confirm_close()
    dialogs.unsaved_answer = "discard"
    assert window.confirm_close()
    # Save: the window stays until the daemon has saved, then closes.
    dialogs.unsaved_answer = "save"
    window.show()
    assert not window.confirm_close()
    assert window.isVisible()
    answer(client, SaveSettingsRequest, SaveSettingsResult(revision=4))
    assert not window.isVisible() and not window.dirty()


def test_closing_with_changes_that_cant_be_saved(
    window: SettingsPage, client: FakeClient, dialogs: FakeDialogs
) -> None:
    editor(window, "konachan").name_edit.setText("")
    dialogs.unsaved_answer = "save"
    assert not window.confirm_close()
    assert not requests(client, SaveSettingsRequest)
    assert window.status.text() == "Can't save yet. a playlist, name: Give it a name"


def test_general_settings(
    window: SettingsPage, client: FakeClient, dialogs: FakeDialogs, tmp_path: Path
) -> None:
    window.idle_switch.setChecked(False)
    assert not window.idle_edit.isEnabled()
    dialogs.list_file = str(tmp_path / "favs.txt")
    window.list_buttons["favourites_file"].click()
    assert window.status.text() == (
        "Changes: the idle pause, the favourites file. Not saved yet."
    )
    window.save_button.click()
    settings = sent(client).settings
    assert settings.pause_when_idle is None
    assert settings.favourites_file == tmp_path / "favs.txt"
    answer(client, SaveSettingsRequest, SaveSettingsResult(revision=4))
    favourites = next(l for l in window.about.locations if l.label == "Favourites")
    assert favourites.path == tmp_path / "favs.txt"  # the saved settings say so
    window.list_edits["to_remove_file"].setText("relative.txt")
    assert window.general_problems["to_remove_file"].message == (
        "Use the file's full path"
    )


# -- layout --------------------------------------------------------------------------


def text_x(widget: QWidget, show: Callable[[str], None]) -> float:
    """Where `widget` starts drawing a text (logical px): the first column
    that differs between it with the text and without."""
    show("Shuffle")
    with_text = widget.grab().toImage()
    show("")
    without = widget.grab().toImage()
    for x in range(with_text.width()):
        for y in range(with_text.height()):
            if with_text.pixelColor(x, y) != without.pixelColor(x, y):
                return x / with_text.devicePixelRatio()
    raise AssertionError("no text drawn")


@pytest.mark.parametrize(
    "style_name",
    [
        n
        for n in ("Fusion", "windows11")
        if n.lower() in map(str.lower, QStyleFactory.keys())
    ],
)
def test_values_in_the_control_column_start_at_the_same_x(
    qtbot: QtBot, style_name: str
) -> None:
    style = QStyleFactory.create(style_name)
    parent = QWidget()
    qtbot.addWidget(parent)
    edit = line_edit("", "a field", parent)
    box = combo(parent, "a drop-down")
    box.addItem("")
    value = theme.ValueLabel(parent=parent)
    for widget in (edit, box, value):
        widget.setStyle(style)
        widget.resize(theme.CONTROL_WIDTH, theme.FIELD_HEIGHT)

    def show_in_box(text: str) -> None:
        box.setItemText(0, text)

    edit_x = text_x(edit, edit.setText)
    assert text_x(box, show_in_box) == pytest.approx(edit_x, abs=1)
    assert text_x(value, value.setText) == pytest.approx(edit_x, abs=1)
    assert edit_x == theme.field_text_x(edit)  # where it is meant to start


def test_paints_without_error(window: SettingsPage) -> None:
    editor(window, "konachan").summary.click()
    window.resize(1100, 900)
    window.grab()


# -- about ---------------------------------------------------------------------------


def test_about_lists_the_data_locations(
    window: SettingsPage, opened: list[QUrl]
) -> None:
    about = window.about
    assert about.version.text() == "1.0 (abc1234)"
    assert [(l.label, l.path) for l in about.locations] == [
        ("Settings and state", paths.state_db()),
        ("Favourites", paths.data_dir() / "favourites.txt"),
        ("To remove", paths.data_dir() / "to-remove.txt"),
        ("App log", paths.log_file()),
        ("Windows log", paths.ui_log_file()),
        ("Rendered wallpapers", paths.render_dir()),
        ("Thumbnail cache", paths.thumbnail_dir()),
    ]
    buttons = {b.accessibleName(): b for b in about.findChildren(theme.TextButton)}
    buttons["Copy the path of: Settings and state"].click()
    assert QGuiApplication.clipboard().text() == str(paths.state_db())
    # The data folders don't exist in the test: the nearest one that does.
    buttons["Open the folder of: Thumbnail cache"].click()
    assert [u.toLocalFile() for u in opened] == [
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


# -- with the daemon -------------------------------------------------------------------


def test_edits_the_running_daemons_settings(
    qtbot: QtBot, tmp_path: Path, dialogs: FakeDialogs, loader: ThumbnailLoader
) -> None:
    write_settings(tmp_path)
    name = ipc.server_name(tmp_path)
    backend = FakeBackend([make_monitor("Main")])
    service_thread = make_service_thread(lambda: backend, name)
    client = SocketClient(name, None)
    try:
        with qtbot.waitSignal(service_thread.overview_changed):
            service_thread.start()
        window = SettingsPage(client, dialogs.dialogs(), loader=loader, version="1")
        qtbot.addWidget(window)
        with qtbot.waitSignal(client.overview_changed):
            client.start()
        qtbot.waitUntil(lambda: bool(window.editors))
        walls = editor(window, "walls")
        assert walls.shown_label.text() == "Main"

        walls.interval_edit.setText("1h")
        window.save_button.click()
        qtbot.waitUntil(lambda: window.status.text() == "No unsaved changes.")
        replies: list[Reply] = []
        client.run(Request(command="status"), replies.append)
        qtbot.waitUntil(lambda: bool(replies))
        status = replies[0]
        assert isinstance(status, StatusResult)
        assert status.playlists[0].interval == 3600  # the daemon runs on them now
    finally:
        client.close()
        assert service_thread.shutdown(5_000)
