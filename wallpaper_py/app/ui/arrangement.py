"""
The monitors drawn to scale in their real arrangement, on the backdrop.

`arrange()` is the geometry, kept free of widgets so it is easy to test:
it scales the whole desktop (positions may be negative: a monitor left of
the main one starts at a negative x) to fit an area, keeping aspect ratios.
Detached monitors have no position on the desktop; they are drawn after
the arrangement, in a row to its right, at their last known size (else
1920×1080), dimmed and marked "off".

`MonitorsView` paints it: each monitor with the current image of its
playlist, and under each its name, resolution and playlist ("no playlist"
for one no playlist drives). No outlines, except on hover: pointing at a
monitor (`hovered`) or at a playlist's card highlights that playlist's
monitors in the accent (`set_highlight`), so the accent only ever means
"what you point at".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...protocol import DesktopMonitor, Overview
from . import theme

DEFAULT_SIZE = (1920, 1080)  # for a detached monitor never seen attached
DETACHED_GAP = 0.15  # between the arrangement and detached ones, x its height
MARGIN = theme.space(2)
LABEL_HEIGHT = 40  # two lines under each monitor
MIN_HEIGHT = 220
MAX_HEIGHT = 400
INSET = 3  # px between neighbouring monitors, so they read as separate
HIGHLIGHT_WIDTH = 2  # px, the hover outline


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def intersects(self, other: Box) -> bool:
        return (
            self.x < other.right
            and other.x < self.right
            and self.y < other.bottom
            and other.y < self.bottom
        )


@dataclass(frozen=True)
class Placed:
    monitor: DesktopMonitor
    box: Box  # in the drawing area's coordinates

    @property
    def attached(self) -> bool:
        return self.monitor.geometry is not None


def arrange(
    monitors: Sequence[DesktopMonitor],
    width: float,
    height: float,
    last_sizes: Mapping[str, tuple[int, int]] | None = None,
) -> list[Placed]:
    """
    Every monitor's box in an area of `width` x `height`: the attached ones
    as on the desktop, the detached ones in a row to the right, all scaled
    by one factor (aspect ratios kept) and centred, inside a MARGIN and
    with LABEL_HEIGHT under the bottom row for its labels. `last_sizes`: by
    id, for detached ones.
    """
    desktop = _desktop_boxes(monitors, last_sizes or {})
    if not desktop:
        return []
    bounds = _bounds([box for _, box in desktop])
    room = Box(
        MARGIN,
        MARGIN,
        max(width - 2 * MARGIN, 1.0),
        max(height - 2 * MARGIN - LABEL_HEIGHT, 1.0),
    )
    scale = min(room.width / bounds.width, room.height / bounds.height)
    x = room.x + (room.width - bounds.width * scale) / 2 - bounds.x * scale
    y = room.y + (room.height - bounds.height * scale) / 2 - bounds.y * scale
    return [
        Placed(
            monitor,
            Box(x + b.x * scale, y + b.y * scale, b.width * scale, b.height * scale),
        )
        for monitor, b in desktop
    ]


def _bounds(boxes: Sequence[Box]) -> Box:
    left, top = min(b.x for b in boxes), min(b.y for b in boxes)
    right, bottom = max(b.right for b in boxes), max(b.bottom for b in boxes)
    return Box(left, top, right - left, bottom - top)


def _desktop_boxes(
    monitors: Sequence[DesktopMonitor], last_sizes: Mapping[str, tuple[int, int]]
) -> list[tuple[DesktopMonitor, Box]]:
    """In desktop pixels: attached where they are, detached to the right."""
    boxes: list[tuple[DesktopMonitor, Box]] = []
    for monitor in monitors:
        if (g := monitor.geometry) is not None:
            boxes.append((monitor, Box(g.x, g.y, g.width, g.height)))
    if boxes:
        right = max(b.right for _, b in boxes)
        top = min(b.y for _, b in boxes)
        gap = DETACHED_GAP * max(b.height for _, b in boxes)
        x = right + gap
    else:
        top, gap, x = 0, 0.0, 0.0
    for monitor in monitors:
        if monitor.geometry is None:
            w, h = last_sizes.get(monitor.id, DEFAULT_SIZE)
            boxes.append((monitor, Box(x, top, w, h)))
            x += w + (gap or DETACHED_GAP * h)
    return boxes


def playlists_by_monitor(overview: Overview) -> dict[str, str]:
    """Monitor id -> the playlist that drives it."""
    result: dict[str, str] = {}
    for playlist in overview.playlists:
        for member in playlist.monitors:
            if member.id is not None:
                result.setdefault(member.id, playlist.name)
    return result


def caption(monitor: DesktopMonitor, playlist: str | None) -> tuple[str, str]:
    """The two lines under a monitor: its name, then size (or off) · playlist."""
    g = monitor.geometry
    state = f"{g.width}×{g.height}" if g is not None else "off"
    return monitor.name, f"{state} · {playlist or 'no playlist'}"


class MonitorsView(QWidget):
    """The arrangement, painted; redrawn on resize and on each overview."""

    hovered = Signal(object)  # the playlist under the pointer, or None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._overview = Overview()
        self._highlight: str | None = None  # playlist outlined in the accent
        self._hovered: str | None = None
        self.setMouseTracking(True)
        self._images: dict[Path, QPixmap] = {}
        self._last_sizes: dict[str, tuple[int, int]] = {}
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(MIN_HEIGHT)
        self.setAccessibleName("Monitors")

    def resizeEvent(self, event: QResizeEvent) -> None:  # pylint: disable=invalid-name
        super().resizeEvent(event)
        # Taller as the window widens; the drawing itself redraws in paintEvent.
        width = event.size().width()
        height = max(MIN_HEIGHT, min(MAX_HEIGHT, round(width * 0.32)))
        if height != self.height():
            self.setFixedHeight(height)

    def show_overview(self, overview: Overview) -> None:
        self._overview = overview
        for monitor in overview.monitors:
            if (g := monitor.geometry) is not None:
                self._last_sizes[monitor.id] = (g.width, g.height)
        current = {p.current for p in overview.playlists if p.current is not None}
        self._images = {p: i for p, i in self._images.items() if p in current}
        lines = []
        by_monitor = playlists_by_monitor(overview)
        for monitor in overview.monitors:
            name, detail = caption(monitor, by_monitor.get(monitor.id))
            lines.append(f"{name}: {detail}")
        self.setAccessibleDescription("; ".join(lines))
        self.setToolTip("\n".join(lines))
        self.update()

    @Slot(object)
    def set_highlight(self, playlist: str | None) -> None:
        """Outline this playlist's monitors in the accent (None: no outline)."""
        if playlist != self._highlight:
            self._highlight = playlist
            self.update()

    def highlight(self) -> str | None:
        return self._highlight

    def playlist_at(self, point: QPointF) -> str | None:
        """The playlist driving the monitor drawn at `point`, if any."""
        by_monitor = playlists_by_monitor(self._overview)
        for item in self.placed():
            box = item.box
            if QRectF(box.x, box.y, box.width, box.height).contains(point):
                return by_monitor.get(item.monitor.id)
        return None

    def mouseMoveEvent(
        self, event: QMouseEvent
    ) -> None:  # pylint: disable=invalid-name
        super().mouseMoveEvent(event)
        self._set_hovered(self.playlist_at(event.position()))

    def leaveEvent(self, event: QEvent) -> None:  # pylint: disable=invalid-name
        super().leaveEvent(event)
        self._set_hovered(None)

    def _set_hovered(self, playlist: str | None) -> None:
        if playlist != self._hovered:
            self._hovered = playlist
            self.hovered.emit(playlist)

    def set_image(self, path: Path, pixmap: QPixmap | None) -> None:
        if pixmap is None:
            self._images.pop(path, None)
        else:
            self._images[path] = pixmap
        self.update()

    def placed(self) -> list[Placed]:
        return arrange(
            self._overview.monitors,
            self.width(),
            self.height(),
            self._last_sizes,
        )

    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        area = QRectF(self.rect())
        backdrop = QPainterPath()
        backdrop.addRoundedRect(area, theme.RADIUS, theme.RADIUS)
        painter.fillPath(backdrop, theme.BACKDROP)

        placed = self.placed()
        by_monitor = playlists_by_monitor(self._overview)
        images = {
            member.id: playlist.current
            for playlist in self._overview.playlists
            for member in playlist.monitors
            if member.id is not None and playlist.current is not None
        }
        outline = _on_backdrop(theme.accent(self.palette()))
        for item in placed:
            image = images.get(item.monitor.id)
            lit = self._highlight is not None and (
                by_monitor.get(item.monitor.id) == self._highlight
            )
            self._paint_monitor(
                painter,
                item,
                self._images.get(image) if image is not None else None,
                outline if lit else None,
            )
        boxes = [p.box for p in placed]
        for item in placed:
            self._paint_caption(painter, item, by_monitor.get(item.monitor.id), boxes)
        painter.end()

    @staticmethod
    def _paint_monitor(
        painter: QPainter,
        item: Placed,
        pixmap: QPixmap | None,
        outline: QColor | None,
    ) -> None:
        """The fill marks the edge; an outline only when highlighted (hover)."""
        box = item.box
        rect = QRectF(box.x, box.y, box.width, box.height).adjusted(
            INSET, INSET, -INSET, -INSET
        )
        painter.save()
        if not item.attached:
            painter.setOpacity(0.45)
        painter.fillRect(rect, theme.BACKDROP_RAISED)
        if pixmap is not None and item.attached:
            painter.save()
            painter.setClipRect(rect)
            painter.drawPixmap(_cover(rect, pixmap), pixmap, QRectF(pixmap.rect()))
            painter.restore()
        if not item.attached:
            painter.setOpacity(1.0)
            painter.setPen(theme.BACKDROP_TEXT)
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), "off")
        if outline is not None:
            painter.setOpacity(1.0)
            painter.setPen(QPen(outline, HIGHLIGHT_WIDTH))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            half = HIGHLIGHT_WIDTH / 2
            painter.drawRect(rect.adjusted(half, half, -half, -half))
        painter.restore()

    def _paint_caption(
        self,
        painter: QPainter,
        item: Placed,
        playlist: str | None,
        boxes: Sequence[Box],
    ) -> None:
        box = item.box
        below = Box(box.x, box.bottom, box.width, LABEL_HEIGHT)
        # A monitor right below (a stacked arrangement): the label goes
        # inside, at the bottom, on a dark band instead.
        inside = any(b is not box and below.intersects(b) for b in boxes)
        if inside:
            below = Box(box.x, box.bottom - LABEL_HEIGHT, box.width, LABEL_HEIGHT)
            band = QRectF(below.x, below.y, below.width, below.height).adjusted(
                INSET, 0, -INSET, -INSET
            )
            painter.fillRect(band, _translucent_backdrop())
        name, detail = caption(item.monitor, playlist)
        rect = QRectF(below.x, below.y + 4, below.width, below.height - 4)
        bold = QFont(painter.font())
        bold.setBold(True)
        painter.save()
        if not item.attached:
            painter.setOpacity(0.6)
        painter.setFont(bold)
        painter.setPen(theme.BACKDROP_TEXT)
        flags = int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        metrics = painter.fontMetrics()
        painter.drawText(
            rect,
            flags,
            metrics.elidedText(name, Qt.TextElideMode.ElideRight, int(rect.width())),
        )
        painter.setFont(self.font())
        painter.setPen(theme.BACKDROP_TEXT_DIM)
        rect.setTop(rect.top() + metrics.height())
        metrics = painter.fontMetrics()
        painter.drawText(
            rect,
            flags,
            metrics.elidedText(detail, Qt.TextElideMode.ElideRight, int(rect.width())),
        )
        painter.restore()


def _on_backdrop(accent: QColor) -> QColor:
    """The accent, lightened if it is too dark to see on the dark backdrop."""
    color = QColor(accent)
    for _ in range(8):
        if color.lightnessF() >= 0.55:
            break
        color = color.lighter(125)
    return color


def _translucent_backdrop() -> QColor:
    color = QColor(theme.BACKDROP)
    color.setAlphaF(0.75)
    return color


def _cover(rect: QRectF, pixmap: QPixmap) -> QRectF:
    """Where to draw `pixmap` so it fills `rect` (cropped, aspect kept)."""
    ratio = pixmap.devicePixelRatio()
    width, height = pixmap.width() / ratio, pixmap.height() / ratio
    if width <= 0 or height <= 0:
        return rect
    scale = max(rect.width() / width, rect.height() / height)
    w, h = width * scale, height * scale
    return QRectF(
        rect.x() + (rect.width() - w) / 2, rect.y() + (rect.height() - h) / 2, w, h
    )
