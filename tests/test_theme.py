"""
The shared look: the tones derived from a dark and a light palette, icons
tinted from the palette (and again when it changes, as when the system
switches light / dark), the icon button, the image view, and the icons
shipping with the package.
"""

from __future__ import annotations

import fnmatch
import tomllib
from collections.abc import Callable, Iterator
from importlib import resources
from pathlib import Path

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QIcon, QImage, QPalette, QPixmap
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from wallpaper_py.app.ui import theme

ROOT = Path(__file__).parents[1]


def ink(image: QImage) -> set[str]:
    """
    The colours of the clearly drawn (mostly opaque) pixels, rounded to
    multiples of 4 per channel: premultiplied edges are off by one.
    """
    colours = set()
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() > 200:
                channels = (pixel.red(), pixel.green(), pixel.blue())
                rounded = QColor(
                    *(255 if c >= 254 else 4 * round(c / 4) for c in channels)
                )
                colours.add(rounded.name())
    return colours


def drawn(button: theme.IconButton) -> set[str]:
    pixmap = button.icon().pixmap(QSize(theme.ICON_SIZE, theme.ICON_SIZE))
    return ink(pixmap.toImage())


def with_text(colour: str) -> QPalette:
    palette = QPalette()
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        palette.setColor(group, QPalette.ColorRole.ButtonText, QColor(colour))
        palette.setColor(group, QPalette.ColorRole.WindowText, QColor(colour))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Accent, QColor("#0078d4"))
    return palette


def system_palette(window: str, text: str, accent: str = "#0078d4") -> QPalette:
    """A palette as the system gives it: Window, its text, the accent."""
    palette = QPalette(QApplication.palette())
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        for role, colour in (
            (QPalette.ColorRole.Window, window),
            (QPalette.ColorRole.WindowText, text),
            (QPalette.ColorRole.ButtonText, text),
            (QPalette.ColorRole.Text, text),
            (QPalette.ColorRole.Accent, accent),
        ):
            palette.setColor(group, role, QColor(colour))
    return palette


DARK = system_palette("#202020", "#ffffff")  # Windows 11's dark window
LIGHT = system_palette("#f3f3f3", "#1a1a1a")


@pytest.fixture(name="app_palette")
def fixture_app_palette() -> Iterator[Callable[[QPalette], None]]:
    """Sets the application's palette (as the system would); restored after."""
    original = QPalette(QApplication.palette())
    yield QApplication.setPalette
    QApplication.setPalette(original)


def test_dark_tones() -> None:
    tones = theme.derive_tones(DARK)
    assert tones.dark
    # The page a shade darker than the palette's window; the sidebar's edge,
    # the panels behind images and the fields lighter than the page.
    assert tones.page.name() == "#1a1a1a"
    assert tones.line.name() == "#2f2f2f"
    assert tones.panel.name() == "#232323"
    assert tones.field.name() == "#2a2a2a"
    page = tones.page.lightness()
    assert page < tones.panel.lightness() < tones.field.lightness()
    assert tones.panel_text == tones.text
    # The system's accent, made light enough to read on the dark page.
    assert tones.accent.lightnessF() >= 0.6
    assert tones.accent_fill.alphaF() == pytest.approx(0.14, abs=0.01)
    assert tones.on_accent.name() == "#000000"


def test_light_tones() -> None:
    tones = theme.derive_tones(LIGHT)
    assert not tones.dark
    assert tones.page.name() == "#f3f3f3"  # the palette's own window colour
    assert tones.line.lightness() < tones.page.lightness()
    # A silver panel behind images, a step darker than the page, with the
    # page's dark text and accent on it.
    assert tones.panel.lightness() < tones.page.lightness()
    assert 0.78 < tones.panel.lightnessF() < 0.86  # silver, about #d4d4d4
    assert tones.panel_text == tones.text
    assert tones.accent.lightnessF() <= 0.45
    assert tones.panel_accent == tones.accent
    assert tones.on_accent.name() == "#ffffff"


def test_tones_follow_the_application_palette(
    app_palette: Callable[[QPalette], None],
) -> None:
    app_palette(DARK)
    assert theme.tones().dark and theme.tones().page.name() == "#1a1a1a"
    app_palette(LIGHT)
    assert not theme.tones().dark


def test_window_palette_puts_the_tones_in_its_roles() -> None:
    tones = theme.derive_tones(DARK)
    palette = theme.window_palette(tones).resolve(DARK)
    assert palette.color(QPalette.ColorRole.Window) == tones.page
    assert palette.color(QPalette.ColorRole.Base) == tones.field  # fields
    assert palette.color(QPalette.ColorRole.Button) == tones.field
    assert palette.color(QPalette.ColorRole.PlaceholderText) == tones.muted
    # The rest stays the system's (and follows it).
    assert palette.color(QPalette.ColorRole.WindowText) == QColor("#ffffff")
    assert palette.color(QPalette.ColorRole.Accent) == QColor("#0078d4")


def test_every_icon_tints_to_the_given_colour() -> None:
    for name in theme.ICON_NAMES:
        pixmap = theme.tinted_pixmap(name, QColor("#ff0000"), 24, 2.0)
        assert pixmap.devicePixelRatio() == 2.0
        assert (pixmap.width(), pixmap.height()) == (48, 48)
        assert ink(pixmap.toImage()) == {"#ff0000"}, name


def test_icon_has_a_disabled_look() -> None:
    palette = with_text("#000000")
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#808080")
    )
    icon = theme.icon("star", palette)
    size = QSize(theme.ICON_SIZE, theme.ICON_SIZE)
    normal = icon.pixmap(size, QIcon.Mode.Normal).toImage()
    disabled = icon.pixmap(size, QIcon.Mode.Disabled).toImage()
    assert ink(normal) == {"#000000"}
    assert ink(disabled) == {"#808080"}


def test_icon_button_follows_its_palette(qtbot: QtBot) -> None:
    button = theme.IconButton("star", "Favourite")
    qtbot.addWidget(button)
    assert button.toolTip() == "Favourite" and button.accessibleName() == "Favourite"
    button.setPalette(with_text("#101010"))  # light mode: dark text
    assert drawn(button) == {"#101010"}
    button.setPalette(with_text("#f0f0f0"))  # the system switched to dark
    assert drawn(button) == {"#f0f0f0"}
    assert button.icon_color() == QColor("#f0f0f0")


def test_filled_icons() -> None:
    outline = theme.tinted_pixmap("star", QColor("#ff0000"), 24, 1.0).toImage()
    filled = theme.tinted_pixmap("star", QColor("#ff0000"), 24, 1.0, True).toImage()
    centre = (12, 13)
    assert outline.pixelColor(*centre).alpha() == 0  # inside the outline
    assert filled.pixelColor(*centre).name() == "#ff0000"


def test_icon_button_on_shows_the_accent(
    qtbot: QtBot, app_palette: Callable[[QPalette], None]
) -> None:
    app_palette(DARK)
    accent = theme.tones().accent
    button = theme.IconButton("pin", "Hold")
    qtbot.addWidget(button)
    button.setPalette(with_text("#101010"))
    assert not button.is_on()
    off = button.grab().toImage().pixelColor(3, button.height() // 2)
    button.set_on(True)
    assert button.is_on()
    assert button.icon_color() == accent  # an accent icon, filled
    assert {accent.name()} == {c for c in drawn(button) if QColor(c).saturation() > 50}
    # On a faint accent fill (the plain button has none).
    on = button.grab().toImage().pixelColor(3, button.height() // 2)
    assert on != off and on.blue() > on.red()
    button.set_label("Resume")
    assert button.toolTip() == "Resume" and button.accessibleName() == "Resume"
    button.set_on(False)
    assert drawn(button) == {"#101010"}


def test_image_view_keeps_its_aspect_and_letterboxes(qtbot: QtBot) -> None:
    view = theme.ImageView()
    qtbot.addWidget(view)
    view.resize(320, 50)
    qtbot.waitUntil(lambda: view.height() == 180)
    tall = QPixmap(90, 160)
    tall.fill(QColor("#ff0000"))
    view.set_pixmap(tall)
    shot = view.grab().toImage()
    assert shot.pixelColor(160, 90).name() == "#ff0000"  # the image, centred
    assert shot.pixelColor(20, 90) == theme.tones().panel  # the panel beside it
    view.set_pixmap(None, "Loading…")
    assert view.pixmap() is None and view.placeholder() == "Loading…"
    view.grab()


def test_icons_ship_with_the_package() -> None:
    """Loaded through importlib.resources, listed as package data."""
    package = "wallpaper_py.app.ui"
    icons = resources.files(package).joinpath("icons")
    files = {f.name for f in icons.iterdir() if f.is_file()}
    assert {f"{n}.svg" for n in theme.ICON_NAMES} | {"LICENSE"} == files
    assert "ISC License" in icons.joinpath("LICENSE").read_text(encoding="utf-8")
    patterns = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["setuptools"]["package-data"][package]
    for name in files:
        assert any(fnmatch.fnmatch(f"icons/{name}", p) for p in patterns), name
    for name in theme.ICON_NAMES:
        assert b"currentColor" in theme.icon_svg(name)  # what the tint replaces
