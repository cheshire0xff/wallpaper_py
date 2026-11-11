"""
The shared look: icons tinted from the palette (and again when it changes,
as when the system switches light / dark), the icon button, the image view,
and the icons shipping with the package.
"""

from __future__ import annotations

import fnmatch
import tomllib
from importlib import resources
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QIcon, QImage, QPalette, QPixmap
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


def test_icon_button_on_is_drawn_on_the_accent(qtbot: QtBot) -> None:
    button = theme.IconButton("pin", "Hold")
    qtbot.addWidget(button)
    button.setPalette(with_text("#101010"))
    button.resize(32, 32)
    assert not button.is_on()
    button.set_on(True)
    assert button.is_on()
    assert drawn(button) == {"#ffffff"}  # the text colour for the accent
    shot = button.grab().toImage()
    assert shot.pixelColor(4, shot.height() // 2).name() == "#0078d4"
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
    assert shot.pixelColor(20, 90) == theme.BACKDROP  # the backdrop beside it
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
