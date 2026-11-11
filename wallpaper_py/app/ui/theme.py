"""
The look every window shares (docs/plan.md, "Look"): Qt's native style and
the system's palette, plus a few constants and two widgets. Not a framework.

- Spacing in 8 px steps (`space`), corner radii (`RADIUS` for cards,
  `RADIUS_SMALL` for chips and buttons).
- `BACKDROP`: the one hard-coded colour, a dark neutral behind images (in
  light mode too: wallpapers look washed out on white), with its own text
  colours. Everything else comes from the `QPalette`.
- Icons: bundled Lucide SVGs (`icons/`, ISC licence kept with them), tinted
  with the palette's text colour when drawn (`icon`). `IconButton` tints
  again when its palette changes, i.e. when the system switches between
  light and dark.
- `ImageView`: an image letterboxed on the backdrop.
- `page`, `heading`: a window's scrolling content and its section headings.

No style or stylesheet is set anywhere: the OS's own style draws the
widgets (Windows 11 style, Breeze on KDE).
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources

from PySide6.QtCore import QByteArray, QEvent, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget

SPACING = 8  # px; everything is spaced in steps of this
RADIUS = 8  # cards
RADIUS_SMALL = 4  # chips, buttons
ICON_SIZE = 20

BACKDROP = QColor("#161616")  # behind images, in light and dark mode
BACKDROP_RAISED = QColor("#262626")  # an empty image area on the backdrop
BACKDROP_TEXT = QColor("#e8e8e8")
BACKDROP_TEXT_DIM = QColor("#a0a0a0")

# The bundled icons: Lucide names (https://lucide.dev), files in icons/.
ICON_NAMES = (
    "circle-alert",
    "link",
    "pin",
    "skip-back",
    "skip-forward",
    "star",
    "trash-2",
)


def space(steps: float = 1) -> int:
    return round(SPACING * steps)


def page(parent: QWidget, spacing: float = 1.5) -> tuple[QWidget, QVBoxLayout]:
    """A window's content (e.g. inside a scroll area): margins, spacing."""
    content = QWidget(parent)
    layout = QVBoxLayout(content)
    margin = space(2)
    layout.setContentsMargins(margin, margin, margin, margin)
    layout.setSpacing(space(spacing))
    return content, layout


def heading(text: str, parent: QWidget | None = None) -> QLabel:
    """A section heading: bold, a little larger."""
    label = QLabel(text, parent)
    font = QFont(label.font())
    font.setBold(True)
    font.setPointSizeF(font.pointSizeF() * 1.15)
    label.setFont(font)
    label.setAccessibleName(text)
    return label


def icon_svg(name: str) -> bytes:
    """An icon's SVG, from wherever the package is installed."""
    return resources.files(__package__).joinpath("icons", f"{name}.svg").read_bytes()


@lru_cache(maxsize=256)
def _tinted(name: str, rgb: str, alpha: float, pixels: int) -> QImage:
    data = icon_svg(name).replace(b"currentColor", rgb.encode("ascii"))
    renderer = QSvgRenderer(QByteArray(data))
    image = QImage(pixels, pixels, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setOpacity(alpha)
    renderer.render(painter)
    painter.end()
    return image


def tinted_pixmap(name: str, color: QColor, size: int, ratio: float) -> QPixmap:
    """The icon `name` in `color`, `size` logical px at device pixel `ratio`."""
    image = _tinted(name, color.name(), color.alphaF(), round(size * ratio))
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def icon(
    name: str,
    palette: QPalette,
    role: QPalette.ColorRole = QPalette.ColorRole.WindowText,
    size: int = ICON_SIZE,
    ratio: float = 1.0,
) -> QIcon:
    """The icon tinted from `palette`: normal and disabled."""
    result = QIcon()
    for group, mode in (
        (QPalette.ColorGroup.Active, QIcon.Mode.Normal),
        (QPalette.ColorGroup.Disabled, QIcon.Mode.Disabled),
    ):
        color = palette.color(group, role)
        result.addPixmap(tinted_pixmap(name, color, size, ratio), mode)
    return result


def accent(palette: QPalette) -> QColor:
    return palette.color(QPalette.ColorRole.Accent)


class IconButton(QToolButton):
    """
    A flat button showing one icon, with a tooltip and an accessible name.
    It may be *on* (a toggle that is on is filled with the accent); the
    owner sets that from the app's state, never the click.
    """

    def __init__(self, icon_name: str, label: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._icon_name = icon_name
        self._on = False
        self.setAutoRaise(True)
        self.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.set_label(label)
        self._tint()

    def set_label(self, label: str) -> None:
        self.setToolTip(label)
        self.setAccessibleName(label)

    def set_on(self, on: bool) -> None:
        if on != self._on:
            self._on = on
            self._tint()
            self.update()

    def is_on(self) -> bool:
        return self._on

    def icon_color(self) -> QColor:
        """The colour the icon is drawn in now (from the palette)."""
        return self.palette().color(QPalette.ColorGroup.Active, self._role())

    def _role(self) -> QPalette.ColorRole:
        # On the accent fill, the icon takes the colour meant for text on it.
        if self._on:
            return QPalette.ColorRole.HighlightedText
        return QPalette.ColorRole.ButtonText

    def _tint(self) -> None:
        ratio = self.devicePixelRatioF()
        self.setIcon(icon(self._icon_name, self.palette(), self._role(), ratio=ratio))

    def changeEvent(self, event: QEvent) -> None:  # pylint: disable=invalid-name
        super().changeEvent(event)
        if event.type() in (
            QEvent.Type.PaletteChange,
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.StyleChange,
        ):
            self._tint()

    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        if self._on:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            color = accent(self.palette())
            if not self.isEnabled():
                color.setAlphaF(0.4)
            painter.setBrush(color)
            painter.drawRoundedRect(
                QRectF(self.rect()).adjusted(1, 1, -1, -1), RADIUS_SMALL, RADIUS_SMALL
            )
            painter.end()
        super().paintEvent(event)


class Chip(QLabel):
    """A small rounded label (e.g. *Held*), outlined in the palette's mid tone."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setContentsMargins(space(0.75), 0, space(0.75), 0)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self.palette().color(QPalette.ColorRole.Mid))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
            RADIUS_SMALL,
            RADIUS_SMALL,
        )
        painter.end()
        super().paintEvent(event)


class ImageView(QWidget):
    """
    An image letterboxed on the backdrop, at a fixed aspect ratio (16:9 by
    default), with rounded corners. Without an image it shows `placeholder`.
    """

    def __init__(self, aspect: float = 16 / 9, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._aspect = aspect
        self._pixmap: QPixmap | None = None
        self._scaled: QPixmap | None = None  # _pixmap at the current size
        self._placeholder = ""
        # The height follows the width (resizeEvent): layouts ignore
        # height-for-width inside grids, and overriding Qt's sizing methods
        # is against the rules (AGENTS.md, rule 3).
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(160)
        self.setFixedHeight(self.height_for(320))

    def set_pixmap(self, pixmap: QPixmap | None, placeholder: str = "") -> None:
        self._pixmap = None if pixmap is None or pixmap.isNull() else pixmap
        self._scaled = None
        self._placeholder = placeholder
        self.update()

    def pixmap(self) -> QPixmap | None:
        return self._pixmap

    def placeholder(self) -> str:
        return self._placeholder

    def height_for(self, width: int) -> int:
        return round(width / self._aspect)

    def resizeEvent(self, event: QResizeEvent) -> None:  # pylint: disable=invalid-name
        super().resizeEvent(event)
        height = self.height_for(event.size().width())
        if height != self.height():  # only the width drives this: no loop
            self.setFixedHeight(height)

    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(area, RADIUS, RADIUS)
        painter.setClipPath(clip)
        painter.fillRect(area, BACKDROP)
        if self._pixmap is not None:
            scaled = self._fitted()
            ratio = scaled.devicePixelRatio()
            width, height = scaled.width() / ratio, scaled.height() / ratio
            x = (area.width() - width) / 2
            y = (area.height() - height) / 2
            painter.drawPixmap(QRectF(x, y, width, height).toRect(), scaled)
        elif self._placeholder:
            painter.setPen(BACKDROP_TEXT_DIM)
            painter.drawText(area, int(Qt.AlignmentFlag.AlignCenter), self._placeholder)
        painter.end()

    def _fitted(self) -> QPixmap:
        """The image scaled to fit (letterboxed), cached for the size."""
        assert self._pixmap is not None
        ratio = self.devicePixelRatioF()
        box = QSize(round(self.width() * ratio), round(self.height() * ratio))
        if self._scaled is None or not _fits(self._scaled.size(), box):
            self._scaled = self._pixmap.scaled(
                box,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._scaled.setDevicePixelRatio(ratio)
        return self._scaled


def _fits(size: QSize, box: QSize) -> bool:
    """`size` is the largest that keeps its aspect inside `box`."""
    return (size.width() == box.width() and size.height() <= box.height()) or (
        size.height() == box.height() and size.width() <= box.width()
    )
