"""The tray icon, drawn in code (no image files to ship): a small landscape."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

SIZES = (16, 20, 24, 32, 40, 48, 64)


class IconState(Enum):
    NORMAL = "normal"
    PAUSED = "paused"  # greyed: rotation waits (locked / idle)
    ERROR = "error"  # badge: the service could not start


def app_icon(state: IconState = IconState.NORMAL) -> QIcon:
    icon = QIcon()
    for size in SIZES:
        icon.addPixmap(_pixmap(size, state))
    return icon


def _pixmap(size: int, state: IconState) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 64, size / 64)  # draw on a 64x64 grid

    grey = state is IconState.PAUSED
    frame = QRectF(4, 10, 56, 44)
    sky = QLinearGradient(QPointF(0, 10), QPointF(0, 54))
    sky.setColorAt(0, QColor("#8a8a8a" if grey else "#3b6fd8"))
    sky.setColorAt(1, QColor("#c8c8c8" if grey else "#9cc3ff"))
    clip = QPainterPath()
    clip.addRoundedRect(frame, 8, 8)
    painter.fillPath(clip, QBrush(sky))
    painter.setClipPath(clip)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#d0d0d0" if grey else "#ffd75e"))
    painter.drawEllipse(QPointF(45, 23), 6, 6)
    hills = QPainterPath(QPointF(4, 54))
    hills.lineTo(4, 44)
    hills.lineTo(22, 26)
    hills.lineTo(36, 40)
    hills.lineTo(44, 33)
    hills.lineTo(60, 47)
    hills.lineTo(60, 54)
    painter.fillPath(hills, QColor("#5a5a5a" if grey else "#2e5e3a"))
    painter.setClipping(False)

    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor("#303030"), 3))
    painter.drawRoundedRect(frame, 8, 8)

    if state is IconState.ERROR:
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
