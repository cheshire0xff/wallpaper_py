"""
The Library's enlarged view, offscreen through a fake Client: the zoom and
pan numbers, reading an image at full size (or why not), going through the
images and back, the info card, the toggles and *Show on* (the commands
they send), and the thumbnail standing in until the image is read.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QPoint, QPointF, QSizeF, Qt
from PySide6.QtGui import QImage, QWheelEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from wallpaper_py.app.ui import enlarged
from wallpaper_py.app.ui.enlarged import (
    EnlargedView,
    Full,
    ImageCanvas,
    details,
    fit_scale,
    max_scale,
    placed,
    read_full,
    zoomed_at,
)
from wallpaper_py.app.ui.entries import Entry
from wallpaper_py.app.ui.grid import Marks
from wallpaper_py.app.ui.thumbnails import ImageInfo
from wallpaper_py.app.ui.tiles import TileLoader, tile_pixels
from wallpaper_py.protocol import (
    ErrorReply,
    MarkRequest,
    MarkResult,
    PickRequest,
)

from .fake_client import FakeClient
from .fakes import FakeTileDecoder, make_images
from .test_settings import answer, requests

PLAYLISTS = [("konachan-id", "konachan"), ("fantasy-id", "fantasy")]


def entries_of(paths: list[Path]) -> list[Entry]:
    return [Entry(p, i) for i, p in enumerate(paths)]


def big_image(path: Path, size: tuple[int, int] = (1600, 900)) -> Path:
    Image.new("RGB", size, (30, 90, 160)).save(path)
    return path


class Views:
    """Makes shown views, and stops their threads after the test."""

    def __init__(self, qtbot: QtBot, client: FakeClient) -> None:
        self._qtbot = qtbot
        self._client = client
        self.decoder = FakeTileDecoder(tile_pixels(1.0))
        self.tiles = TileLoader(self.decoder, threads=1)
        self.made: list[EnlargedView] = []

    def __call__(self) -> EnlargedView:
        view = EnlargedView(self._client, self.tiles)
        self._qtbot.addWidget(view)
        view.resize(1000, 700)
        view.show()
        self._qtbot.waitExposed(view)
        self.made.append(view)
        return view

    def close(self) -> None:
        for view in self.made:
            view.shutdown()
        self.tiles.close()


@pytest.fixture(name="client")
def fixture_client() -> FakeClient:
    return FakeClient()


@pytest.fixture(name="views")
def fixture_views(qtbot: QtBot, client: FakeClient) -> Iterator[Views]:
    views = Views(qtbot, client)
    yield views
    views.close()


@pytest.fixture(name="view")
def fixture_view(views: Views) -> EnlargedView:
    return views()


@pytest.fixture(name="images")
def fixture_images(tmp_path: Path) -> list[Path]:
    return make_images(tmp_path / "walls", "a.png", "b.png", "c.png")


def read(qtbot: QtBot, view: EnlargedView) -> Full:
    """The image shown, once read."""
    qtbot.waitUntil(lambda: view.full() is not None)
    full = view.full()
    assert full is not None
    return full


# -- the numbers -------------------------------------------------------------------


def test_the_fit_never_enlarges_past_the_images_own_pixels() -> None:
    view = QSizeF(1000, 500)
    assert fit_scale(QSizeF(4000, 1000), view, 1.0) == 0.25  # the width decides
    assert fit_scale(QSizeF(1000, 2000), view, 1.0) == 0.25  # the height
    assert fit_scale(QSizeF(200, 100), view, 1.0) == 1.0  # small: its own size
    assert fit_scale(QSizeF(200, 100), view, 2.0) == 0.5  # one px per device px


def test_the_wheel_zooms_to_five_times_the_fit_and_at_least_its_own_pixels() -> None:
    assert max_scale(0.25, 1.0) == 1.25
    assert max_scale(0.1, 1.0) == 1.0  # a huge image: as far as its own pixels
    assert max_scale(0.1, 2.0) == 0.5


def test_the_image_stays_in_the_view() -> None:
    view = QSizeF(1000, 500)
    image = QSizeF(400, 1000)
    # Narrower than the view: centred across; taller: no gap at either end.
    assert placed(QPointF(-50, 30), image, 1.0, view) == QPointF(300, 0)
    assert placed(QPointF(0, -800), image, 1.0, view) == QPointF(300, -500)
    assert placed(QPointF(0, -200), image, 1.0, view) == QPointF(300, -200)


def test_zooming_keeps_the_point_under_the_pointer() -> None:
    offset, point = QPointF(100, 50), QPointF(300, 250)
    image_point = (point - offset) / 0.5
    new = zoomed_at(offset, 0.5, 2.0, point)
    assert new + image_point * 2.0 == point


# -- reading the image -----------------------------------------------------------


def test_an_image_is_read_at_its_own_size(tmp_path: Path) -> None:
    path = big_image(tmp_path / "wide.png", (1600, 900))
    full = read_full(path)
    assert (full.image.width(), full.image.height()) == (1600, 900)
    assert full.info == ImageInfo(1600, 900, path.stat().st_size)
    assert details(full).startswith("1600×900 · ")


def test_a_missing_or_broken_file_says_why(tmp_path: Path) -> None:
    gone = read_full(tmp_path / "gone.jpg")
    assert gone.missing and gone.image.isNull()
    assert details(gone) == "Missing: the file is gone"
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image")
    full = read_full(broken)
    assert full.image.isNull() and full.error is not None
    assert details(full).startswith("can't open: ")


# -- the canvas -------------------------------------------------------------------


def test_the_canvas_fits_zooms_at_the_pointer_and_pans(qtbot: QtBot) -> None:
    canvas = ImageCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(800, 450)
    canvas.show()
    qtbot.waitExposed(canvas)
    canvas.set_image(solid(3200, 1800))
    fit = canvas.fit()
    assert canvas.fitted() and canvas.scale() == fit
    point = QPointF(200, 100)
    under = (point - canvas.offset()) / canvas.scale()
    wheel(canvas, point, 120)
    assert not canvas.fitted() and canvas.scale() == pytest.approx(fit * 1.25)
    after = (point - canvas.offset()) / canvas.scale()
    assert after.x() == pytest.approx(under.x()) and after.y() == pytest.approx(
        under.y()
    )
    for _ in range(20):
        wheel(canvas, point, 120)
    assert canvas.scale() == pytest.approx(max_scale(fit, canvas.devicePixelRatioF()))
    for _ in range(40):
        wheel(canvas, point, -120)
    assert canvas.fitted() and canvas.scale() == pytest.approx(fit)

    canvas.toggle_zoom(point)
    zoomed = canvas.offset()
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=QPoint(400, 200))
    QTest.mouseMove(canvas, QPoint(380, 190))
    QTest.mouseMove(canvas, QPoint(340, 170))
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=QPoint(340, 170))
    assert canvas.offset() == zoomed + QPointF(-60, -30)  # dragged
    canvas.toggle_zoom(point)
    assert canvas.fitted()

    for _ in range(20):
        wheel(canvas, point, 120)  # as far as it goes
    canvas.resize(400, 225)
    top = max_scale(canvas.fit(), canvas.devicePixelRatioF())
    assert canvas.scale() == pytest.approx(top)  # within the new limits
    wheel(canvas, point, 120)
    assert canvas.scale() == pytest.approx(top)  # never zooms out


def test_a_resize_keeps_the_middle_of_a_zoomed_image(qtbot: QtBot) -> None:
    canvas = ImageCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(800, 450)
    canvas.show()
    qtbot.waitExposed(canvas)
    canvas.set_image(solid(3200, 1800))
    wheel(canvas, QPointF(400, 225), 120)
    wheel(canvas, QPointF(400, 225), 120)
    pan = QPointF(-100, -50)
    canvas.pan_by(pan)
    middle = (QPointF(400, 225) - canvas.offset()) / canvas.scale()
    canvas.resize(1000, 600)
    after = (QPointF(500, 300) - canvas.offset()) / canvas.scale()
    assert (after.x(), after.y()) == pytest.approx((middle.x(), middle.y()))


def test_a_click_is_one_once_no_second_click_came(qtbot: QtBot) -> None:
    canvas = ImageCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(800, 450)
    canvas.show()
    canvas.set_image(solid(3200, 1800))
    with qtbot.waitSignal(canvas.clicked):
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=QPoint(400, 200))
    clicks: list[None] = []
    canvas.clicked.connect(lambda: clicks.append(None))
    QTest.mouseDClick(canvas, Qt.MouseButton.LeftButton, pos=QPoint(400, 200))
    assert not canvas.fitted()  # zoomed
    qtbot.wait(QApplication.doubleClickInterval() + 100)
    assert not clicks  # half of a double-click is no click
    canvas.quiet()  # just opened by a click on a tile
    QTest.mouseDClick(canvas, Qt.MouseButton.LeftButton, pos=QPoint(400, 200))
    assert not canvas.fitted()  # not zoomed back: the tile's second click


def solid(width: int, height: int) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.darkCyan)
    return image


def wheel(canvas: ImageCanvas, point: QPointF, angle: int) -> None:
    event = QWheelEvent(
        point,
        canvas.mapToGlobal(point),
        QPoint(),
        QPoint(0, angle),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(canvas, event)


# -- the view ----------------------------------------------------------------------


def test_it_goes_through_the_images_and_back(
    qtbot: QtBot, view: EnlargedView, images: list[Path]
) -> None:
    view.open_entries(entries_of(images), 1, None)
    assert view.name.text() == "b.png" and view.position.text() == "2 of 3"
    full = read(qtbot, view)
    assert view.canvas.image() is full.image
    assert view.card.path.text() == str(images[1])
    assert view.card.details.text() == details(full)
    QTest.keyClick(view.canvas, Qt.Key.Key_Right)
    assert view.current() == Entry(images[2], 2)
    assert not view.next_button.isEnabled()
    QTest.keyClick(view.canvas, Qt.Key.Key_Right)  # the last: stays
    assert view.row() == 2
    QTest.keyClick(view.canvas, Qt.Key.Key_Left)
    QTest.keyClick(view.canvas, Qt.Key.Key_Left)
    assert view.row() == 0 and not view.previous_button.isEnabled()
    qtbot.waitUntil(lambda: view.canvas.image() is not None)
    with qtbot.waitSignal(view.closed):
        QTest.keyClick(view.canvas, Qt.Key.Key_Escape)
    assert view.current() is None and view.canvas.image() is None  # not kept
    view.open_entries(entries_of(images), 0, None)
    with qtbot.waitSignal(view.closed):
        view.back_button.click()


def test_the_arrows_still_go_through_after_a_button_is_clicked(
    view: EnlargedView, images: list[Path]
) -> None:
    view.open_entries(entries_of(images), 0, None)
    view.canvas.setFocus()
    QTest.mouseClick(view.next_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(view.fav_button, Qt.MouseButton.LeftButton)
    assert view.row() == 1
    QTest.keyClick(view.focusWidget() or view, Qt.Key.Key_Right)
    assert view.row() == 2  # the click left the focus on the image


def test_passing_images_drops_their_stand_ins(
    view: EnlargedView, views: Views, images: list[Path]
) -> None:
    view.open_entries(entries_of(images), 0, None)
    view.show_next()
    view.show_next()
    assert views.tiles.pending() <= {images[2], *views.decoder.started}


def test_a_missing_image_says_so(
    qtbot: QtBot, view: EnlargedView, tmp_path: Path
) -> None:
    gone = tmp_path / "gone.jpg"
    view.open_entries(entries_of([gone]), 0, None)
    read(qtbot, view)
    assert view.canvas.lines() == ("gone.jpg", "Missing")
    assert view.card.details.text() == "Missing: the file is gone"
    assert view.card.path.text() == str(gone)  # still there to copy


def test_a_click_hides_and_shows_the_info_card(
    qtbot: QtBot, view: EnlargedView, images: list[Path]
) -> None:
    view.open_entries(entries_of(images), 0, None)
    center = view.canvas.rect().center()
    QTest.mouseClick(view.canvas, Qt.MouseButton.LeftButton, pos=center)
    qtbot.wait(QApplication.doubleClickInterval() + 100)
    assert view.card.isVisible()  # the tile's second click: ignored
    with qtbot.waitSignal(view.canvas.clicked):
        QTest.mouseClick(view.canvas, Qt.MouseButton.LeftButton, pos=center)
    assert view.card.isHidden()
    with qtbot.waitSignal(view.canvas.clicked):
        QTest.mouseClick(view.canvas, Qt.MouseButton.LeftButton, pos=center)
    assert view.card.isVisible()


def test_copy_path(view: EnlargedView, images: list[Path]) -> None:
    view.open_entries(entries_of(images), 2, None)
    view.card.copy_button.click()
    assert QApplication.clipboard().text() == str(images[2])


def test_the_toggles_show_the_lists_and_send_by_path(
    view: EnlargedView, client: FakeClient, images: list[Path]
) -> None:
    entries = entries_of(images)
    view.open_entries(entries, 0, None)
    assert not view.fav_button.isEnabled()  # the lists aren't read yet
    view.set_marks(Marks(favourites=frozenset({entries[0].key})))
    assert view.fav_button.is_on() and not view.remove_button.is_on()
    view.fav_button.click()
    view.remove_button.click()
    assert [r for r, _ in requests(client, MarkRequest)] == [
        MarkRequest(command="unfav", image=images[0]),
        MarkRequest(command="remove", image=images[0]),
    ]
    assert view.fav_button.is_on()  # the lists decide, never the click
    view.set_marks(Marks(to_remove=frozenset({entries[0].key})))
    assert view.remove_button.is_on() and not view.fav_button.is_on()
    view.remove_button.click()
    assert requests(client, MarkRequest)[-1][0] == MarkRequest(
        command="unremove", image=images[0]
    )
    answer(client, MarkRequest, ErrorReply.make("failed", "favourites.txt is busy"))
    assert view.banner.text() == "Error: favourites.txt is busy"
    QTest.keyClick(view.canvas, Qt.Key.Key_Right)
    assert view.banner.isHidden()  # another image
    view.fav_button.click()
    result = MarkResult(
        command="fav",
        playlist=None,
        image=images[1],
        list_file=Path("/f.txt"),
        changed=True,
    )
    answer(client, MarkRequest, result)
    assert view.banner.isHidden()
    view.fav_button.click()
    QTest.keyClick(view.canvas, Qt.Key.Key_Right)  # before the reply came
    answer(client, MarkRequest, ErrorReply.make("failed", "for the image before"))
    assert view.banner.isHidden()  # not under this one


def test_show_on_picks_on_the_playlist_chosen(
    view: EnlargedView, client: FakeClient, images: list[Path]
) -> None:
    assert view.show_button.isHidden()  # no playlists yet
    view.set_playlists(PLAYLISTS[:1])
    assert view.show_button.text() == "Show on konachan"
    assert view.show_combo.isHidden()
    view.set_playlists(PLAYLISTS)
    view.open_entries(entries_of(images), 1, "fantasy-id")  # the page's playlist
    assert view.show_button.text() == "Show on" and view.show_combo.isVisible()
    assert view.show_combo.currentText() == "fantasy"
    view.show_button.click()
    (request,) = [r for r, _ in requests(client, PickRequest)]
    assert request == PickRequest(target="fantasy-id", image=images[1])
    answer(client, PickRequest, ErrorReply.make("failed", "listed for removal"))
    assert view.banner.text() == "Error: listed for removal"
    view.show_combo.setCurrentIndex(0)
    view.show_button.click()
    assert requests(client, PickRequest)[-1][0].target == "konachan-id"
    view.set_playlists([])
    assert view.show_button.isHidden()


def test_a_lists_line_that_is_no_full_path_is_not_sent(
    view: EnlargedView, client: FakeClient
) -> None:
    line = Entry(Path("pics/a.jpg"), 0)  # written by hand, or from another system
    view.set_playlists(PLAYLISTS)
    view.open_entries([line], 0, None)
    view.set_marks(Marks(favourites=frozenset({line.key})))
    view.fav_button.click()
    assert view.banner.text().startswith("Error: not a full path")
    view.show_button.click()
    assert not requests(client, MarkRequest) and not requests(client, PickRequest)


def test_the_thumbnail_stands_in_until_the_image_is_read(
    qtbot: QtBot,
    views: Views,
    images: list[Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = threading.Event()

    def slow_read(path: Path) -> Full:
        assert gate.wait(10)
        return read_full(path)

    monkeypatch.setattr(enlarged, "read_full", slow_read)
    view = views()
    try:
        view.open_entries(entries_of(images), 0, None)
        assert view.canvas.stand_in() is None  # asked for
        qtbot.waitUntil(lambda: view.canvas.stand_in() is not None)
        assert views.decoder.started == [images[0]]
    finally:
        gate.set()
    full = read(qtbot, view)
    assert view.canvas.image() is full.image and view.canvas.stand_in() is None
