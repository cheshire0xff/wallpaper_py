"""The bundled app icon and its tray-state variants."""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from importlib import resources

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap

SIZES = (16, 20, 24, 32, 40, 48, 64)
_ICON_SIZE = 64


class IconState(Enum):
    NORMAL = "normal"
    PAUSED = "paused"  # greyed: rotation waits (locked / idle)
    ERROR = "error"  # badge: the service could not start


def app_icon(state: IconState = IconState.NORMAL) -> QIcon:
    icon = QIcon()
    source = _state_pixmap(state)
    icon.addPixmap(source)  # 256 px: HiDPI and large (Alt+Tab) sizes
    for size in SIZES:
        icon.addPixmap(
            source.scaled(
                size,
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
    return icon


@lru_cache(maxsize=1)
def _source_image() -> QImage:
    data = resources.files(__package__).joinpath("icons", "app-icon.png").read_bytes()
    image = QImage.fromData(data)
    if image.isNull():
        raise RuntimeError("Cannot load the bundled app icon (icons/app-icon.png)")
    return image


def _state_pixmap(state: IconState) -> QPixmap:
    image = _source_image()
    if state is IconState.PAUSED:
        grey = image.convertToFormat(QImage.Format.Format_Grayscale8).convertToFormat(
            QImage.Format.Format_ARGB32
        )
        # Grayscale8 has no alpha channel, so restore the source alpha.
        painter = QPainter(grey)
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_DestinationIn
        )
        painter.drawImage(QPoint(0, 0), image)
        painter.end()
        image = grey

    pixmap = QPixmap.fromImage(image)
    if state is IconState.ERROR:
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(pixmap.width() / _ICON_SIZE, pixmap.height() / _ICON_SIZE)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#e03c31"))
        painter.drawEllipse(QPointF(48, 48), 14, 14)
        pen = QPen(QColor("white"), 5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(48, 40), QPointF(48, 49))
        painter.drawPoint(QPointF(48, 56))
        painter.end()
    return pixmap
