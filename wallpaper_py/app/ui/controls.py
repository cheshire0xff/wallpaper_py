"""
The controls painted here, not by the style (docs/design/controls.dc.html):
one look on Windows and KDE, its colours from the palette (theme.tones), so
light / dark mode and the accent still follow the system. No stylesheet.

- `FieldCombo`: a drop-down, a filled box without a border with a solid ▾
  at the right, lighter under the pointer; its text starts where a
  borderless line edit's does, so the values in a column line up. Its
  open list is in the same tones, the current entry marked as the
  sidebar marks the current page (an accent bar on a faint accent fill).
  The list is a popup of our own: Qt's is drawn by the style, and
  restyling it would mean overriding Qt's non-event methods (AGENTS.md,
  rule 3).
- `Segments`: a few choices side by side (Mode, Order), each a button with
  an optional icon; the chosen one outlined in the accent on a faint
  accent fill.
- `Switch`: on / off (a setting): a thin track under a bigger knob, as in
  the NVIDIA app.
- `FilterChip`: on / off in a toolbar (what narrows a grid): drawn as a
  segment is, with a ✓ before its label while on.
- `Button`: the main action (`primary`: filled with the accent) or a
  neutral one (the field tone).
- `Slider`: a thin track, the accent up to a round knob.

Each shows hover, disabled and the keyboard focus (a 2 px accent ring,
only when the focus came from the keyboard), has an accessible name, and
is drawn at any device pixel ratio (QPainter in logical pixels).
"""

from __future__ import annotations

from typing import override

from PySide6.QtCore import (
    QEvent,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QFocusEvent,
    QFont,
    QFontMetrics,
    QHideEvent,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSlider,
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QSizePolicy,
    QStyle,
    QWidget,
)

from . import theme

ARROW = (10.0, 6.0)  # the drop-down's ▾: width, height
ARROW_RIGHT = 12  # px from the box's right edge
ITEM_HEIGHT = 32  # an entry in an open drop-down
ITEM_GAP = 2
POPUP_PADDING = 4
SEGMENT_GAP = 6
SEGMENT_PADDING = 12
SEGMENT_ICON = 16
CHIP_LABEL = 32  # a chip's label at most, in average characters, then …
BUTTON_PADDING = 18
SWITCH = (36, 12)  # the track
SWITCH_KNOB = 20  # taller than the track, as in the NVIDIA app
SWITCH_ROOM = theme.RING + 2  # around the knob's path: the ring, 2 px off it
KNOB = 16  # the slider's knob
TRACK = 4  # the slider's track: its thickness


def _bold(font: QFont) -> QFont:
    return theme.scaled_font(font, 1.0, bold=True)


def _arrow(rect: QRectF, up: bool) -> QPolygonF:
    """A solid triangle filling `rect`, pointing down (or up)."""
    top, bottom = (rect.bottom(), rect.top()) if up else (rect.top(), rect.bottom())
    return QPolygonF(
        [
            QPointF(rect.left(), top),
            QPointF(rect.right(), top),
            QPointF(rect.center().x(), bottom),
        ]
    )


def _paint_box(
    painter: QPainter,
    rect: QRectF,
    fill: QColor,
    focus: bool,
    edge: QColor | None = None,
) -> None:
    """
    A control's rounded box: `fill`, with a 1 px `edge` if given; with the
    focus ring, the box is inside it (`theme.inside_ring`).
    """
    radius = float(theme.RADIUS_SMALL)
    if focus:
        theme.draw_focus_ring(painter, rect)
        rect, radius = theme.inside_ring(rect), 2.0
    painter.save()
    if edge is None:
        painter.setPen(Qt.PenStyle.NoPen)
    else:
        painter.setPen(edge)
        rect = rect.adjusted(0.5, 0.5, -0.5, -0.5)
    painter.setBrush(fill)
    painter.drawRoundedRect(rect, radius, radius)
    painter.restore()


# -- drop-down -----------------------------------------------------------------------


class FieldCombo(QComboBox):
    """
    See the module docstring. A QComboBox for its entries, its value and
    its keys (arrows change the value while closed); its open list is
    `_EntryList`, opened by a click, Space, F4 or Alt+Down.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._keyboard_focus = False
        self._open = False
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(theme.FIELD_HEIGHT)
        self.entry_list = _EntryList(self)

    def is_open(self) -> bool:
        return self._open

    def shows_focus(self) -> bool:
        return self.hasFocus() and self._keyboard_focus

    def open_list(self) -> None:
        if self.count() and not self._open:
            self._open = True
            self.update()
            self.entry_list.show_entries()

    def list_closed(self) -> None:
        """Its list closed (an entry chosen, or not)."""
        self._open = False
        self.update()

    @override
    def mousePressEvent(
        self, event: QMouseEvent
    ) -> None:  # pylint: disable=invalid-name
        self._keyboard_focus = False  # the pointer took over: no ring
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            event.accept()
            self.open_list()
        else:
            super().mousePressEvent(event)

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:  # pylint: disable=invalid-name
        key = event.key()
        alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        if key in (Qt.Key.Key_Space, Qt.Key.Key_F4, Qt.Key.Key_Select) or (
            alt and key in (Qt.Key.Key_Up, Qt.Key.Key_Down)
        ):
            event.accept()
            self.open_list()
        else:
            super().keyPressEvent(event)

    @override
    def focusInEvent(self, event: QFocusEvent) -> None:  # pylint: disable=invalid-name
        super().focusInEvent(event)
        self._keyboard_focus = theme.keyboard_focus(event, self._keyboard_focus)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        tones = theme.tones()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = self.isEnabled()
        lit = enabled and (self.underMouse() or self._open)
        fill = tones.field_hover if lit else tones.field
        _paint_box(painter, QRectF(self.rect()), fill, self.shows_focus())
        if not enabled:
            ink = tones.disabled
        else:
            ink = tones.text if lit else tones.muted
        arrow = self._paint_arrow(painter, ink)
        left = theme.field_text_x(self)
        area = QRect(left, 0, max(round(arrow.left()) - 8 - left, 0), self.height())
        painter.setPen(tones.text if enabled else tones.disabled)
        text = self.fontMetrics().elidedText(
            self.currentText(), Qt.TextElideMode.ElideRight, area.width()
        )
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        painter.drawText(area, int(flags), text)
        painter.end()

    def _paint_arrow(self, painter: QPainter, colour: QColor) -> QRectF:
        """The ▾ at the right (▴ while open); returns where it is."""
        width, height = ARROW
        arrow = QRectF(
            self.width() - ARROW_RIGHT - width,
            (self.height() - height) / 2,
            width,
            height,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawPolygon(_arrow(arrow, up=self._open))
        return arrow


class _EntryList(QWidget):
    """
    A `FieldCombo`'s open list: a popup window under it (above it if there
    is no room below), as wide as it or its longest entry. The entry under
    the pointer (or picked with the arrow keys) has the hover tone, the
    current one the accent mark; a click, Enter or Space chooses one,
    Escape or a click elsewhere closes it. All entries show (the daemon's
    lists are short).
    """

    def __init__(self, combo: FieldCombo) -> None:
        flags = Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        super().__init__(combo, flags)
        self._combo = combo
        self.pointed = -1
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def show_entries(self) -> None:
        combo = self._combo
        rows = combo.count()
        height = 2 * POPUP_PADDING + rows * ITEM_HEIGHT + (rows - 1) * ITEM_GAP
        metrics = QFontMetrics(_bold(combo.font()))
        widest = max(metrics.horizontalAdvance(combo.itemText(i)) for i in range(rows))
        width = max(combo.width(), widest + 2 * (POPUP_PADDING + SEGMENT_PADDING))
        place = combo.mapToGlobal(QPoint(0, combo.height() + POPUP_PADDING))
        screen = combo.screen().availableGeometry()
        if place.y() + height > screen.bottom():
            above = combo.mapToGlobal(QPoint(0, -POPUP_PADDING - height))
            if above.y() >= screen.top():
                place = above
        self.setGeometry(QRect(place, QSize(width, height)))
        self.setAccessibleName(combo.accessibleName())
        self.pointed = combo.currentIndex()
        self.show()
        self.setFocus(Qt.FocusReason.PopupFocusReason)

    def entry_rect(self, row: int) -> QRectF:
        top = POPUP_PADDING + row * (ITEM_HEIGHT + ITEM_GAP)
        width = self.width() - 2 * POPUP_PADDING
        return QRectF(POPUP_PADDING, top, width, ITEM_HEIGHT)

    def entry_at(self, point: QPointF) -> int:
        """The entry at `point`; -1: none."""
        return next(
            (
                row
                for row in range(self._combo.count())
                if self.entry_rect(row).contains(point)
            ),
            -1,
        )

    def choose(self, row: int) -> None:
        self.close()
        if 0 <= row < self._combo.count():
            self._combo.setCurrentIndex(row)

    def _point(self, row: int) -> None:
        if row != self.pointed:
            self.pointed = row
            self.update()

    @override
    def mouseMoveEvent(
        self, event: QMouseEvent
    ) -> None:  # pylint: disable=invalid-name
        self._point(self.entry_at(event.position()))

    @override
    def mousePressEvent(
        self, event: QMouseEvent
    ) -> None:  # pylint: disable=invalid-name
        if self.rect().contains(event.position().toPoint()):
            event.accept()
            return
        # Outside: it closes; a click on its drop-down doesn't open it again.
        combo = self._combo
        if combo.rect().contains(combo.mapFromGlobal(event.globalPosition().toPoint())):
            self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay)
        self.close()

    @override
    def mouseReleaseEvent(
        self, event: QMouseEvent
    ) -> None:  # pylint: disable=invalid-name
        row = self.entry_at(event.position())
        if row >= 0:
            self.choose(row)

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:  # pylint: disable=invalid-name
        key, last = event.key(), self._combo.count() - 1
        moves = {
            Qt.Key.Key_Up: max(self.pointed - 1, 0),
            Qt.Key.Key_Down: min(self.pointed + 1, last),
            Qt.Key.Key_Home: 0,
            Qt.Key.Key_PageUp: 0,
            Qt.Key.Key_End: last,
            Qt.Key.Key_PageDown: last,
        }
        alt = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        if key in moves and not alt:
            self._point(moves[Qt.Key(key)])
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.choose(self.pointed)
        elif key in (Qt.Key.Key_Escape, Qt.Key.Key_F4, Qt.Key.Key_Tab) or alt:
            self.close()
        event.accept()

    @override
    def hideEvent(self, event: QHideEvent) -> None:  # pylint: disable=invalid-name
        super().hideEvent(event)
        self._combo.list_closed()

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        tones = theme.tones()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(tones.line)
        painter.setBrush(tones.popup)
        outline = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(outline, theme.RADIUS, theme.RADIUS)
        combo = self._combo
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        for row in range(combo.count()):
            rect = self.entry_rect(row)
            current = row == combo.currentIndex()
            _paint_marks(painter, rect, row == self.pointed, current)
            font = _bold(combo.font()) if current else QFont(combo.font())
            painter.setFont(font)
            painter.setPen(tones.text)
            area = rect.adjusted(SEGMENT_PADDING, 0, -SEGMENT_PADDING, 0)
            text = QFontMetrics(font).elidedText(
                combo.itemText(row), Qt.TextElideMode.ElideRight, round(area.width())
            )
            painter.drawText(area, int(flags), text)
        painter.end()


def _paint_marks(painter: QPainter, rect: QRectF, pointed: bool, current: bool) -> None:
    """An entry's hover tone (pointed) and the current entry's accent mark."""
    tones = theme.tones()
    painter.setPen(Qt.PenStyle.NoPen)
    radius = theme.RADIUS_SMALL
    if pointed:
        painter.setBrush(tones.hover)
        painter.drawRoundedRect(rect, radius, radius)
    if current:
        painter.setBrush(tones.accent_fill)
        painter.drawRoundedRect(rect, radius, radius)
        painter.setBrush(tones.accent)
        edge = QRectF(rect.left(), rect.center().y() - 8, 3, 16)
        painter.drawRoundedRect(edge, 1.5, 1.5)


# -- segmented choice --------------------------------------------------------------


def _choice_colours(button: QAbstractButton) -> tuple[QColor, QColor, QColor]:
    """
    A segment's or a filter chip's fill, edge and ink (text and icon) now:
    outlined; chosen (on), outlined in the accent on a faint accent fill.
    """
    tones = theme.tones()
    enabled = button.isEnabled()
    if button.isChecked():
        if not enabled:
            return tones.accent_fill, tones.disabled, tones.disabled
        return tones.accent_fill, tones.accent, tones.accent
    fill = QColor(0, 0, 0, 0)
    if enabled and (button.underMouse() or button.isDown()):
        fill = tones.accent_fill if button.isDown() else tones.hover
    return fill, tones.outline, tones.text if enabled else tones.disabled


class _Segment(theme.PaintedButton):
    """One choice of `Segments`: its icon (if any) and its label."""

    def __init__(self, text: str, icon_name: str | None, parent: QWidget) -> None:
        super().__init__(text, parent, checkable=True)
        self.icon_name = icon_name
        icon = SEGMENT_ICON + theme.space() if icon_name else 0
        # As wide as its bold label: choosing it doesn't move its neighbours.
        width = QFontMetrics(_bold(self.font())).horizontalAdvance(text)
        self.setFixedSize(width + icon + 2 * SEGMENT_PADDING, theme.FIELD_HEIGHT)

    def colours(self) -> tuple[QColor, QColor, QColor]:
        """Its fill, edge and ink (text and icon) now."""
        return _choice_colours(self)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        fill, edge, ink = self.colours()
        _paint_box(painter, rect, fill, self.shows_focus(), edge)
        font = _bold(self.font()) if self.isChecked() else QFont(self.font())
        content = QFontMetrics(font).horizontalAdvance(self.text())
        if self.icon_name:
            content += SEGMENT_ICON + theme.space()
        x = round((rect.width() - content) / 2)
        if self.icon_name:
            pixmap = theme.tinted_pixmap(
                self.icon_name, ink, SEGMENT_ICON, self.devicePixelRatioF()
            )
            painter.drawPixmap(x, round((rect.height() - SEGMENT_ICON) / 2), pixmap)
            x += SEGMENT_ICON + theme.space()
        painter.setFont(font)
        painter.setPen(ink)
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        painter.drawText(
            QRectF(x, 0, rect.width() - x, rect.height()), int(flags), self.text()
        )
        painter.end()


class Segments(QWidget):
    """
    A few choices side by side, one chosen (`add` them; the first is chosen
    at first). Each holds a value, as a drop-down's entries do.
    `current_changed(index)` when the user (or `set_current_index`) chooses
    another; `select` chooses without it. The arrow keys move between them.
    """

    current_changed = Signal(int)

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName(name)
        self._values: list[object] = []
        self._current = -1
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idClicked.connect(self._clicked)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(SEGMENT_GAP)
        self._row.addStretch(1)

    def add(
        self, text: str, value: object, icon_name: str | None = None, tooltip: str = ""
    ) -> None:
        index = len(self._values)
        button = _Segment(text, icon_name, self)
        button.setToolTip(tooltip)
        self._values.append(value)
        self._group.addButton(button, index)
        self._row.insertWidget(index, button)
        if index == 0:
            self._choose(0)

    def button(self, index: int) -> QAbstractButton:
        return self._group.button(index)

    def count(self) -> int:
        return len(self._values)

    def current_index(self) -> int:
        return self._current

    def current_value(self) -> object:
        return self._values[self._current] if self._current >= 0 else None

    def current_text(self) -> str:
        return self.button(self._current).text() if self._current >= 0 else ""

    def find_value(self, value: object) -> int:
        """The index of the choice with this value; -1: none has it."""
        return next((i for i, v in enumerate(self._values) if v == value), -1)

    def set_current_index(self, index: int) -> None:
        """Choose it, as a click does (`current_changed` if it changes)."""
        if index != self._current:
            self._choose(index)
            self.current_changed.emit(index)

    def select(self, value: object) -> None:
        """Choose the one with this value (else the first), without telling."""
        self._choose(max(self.find_value(value), 0))

    def _choose(self, index: int) -> None:
        self._current = index
        self._group.button(index).setChecked(True)

    @Slot(int)
    def _clicked(self, index: int) -> None:
        if index != self._current:
            self._current = index
            self.current_changed.emit(index)


# -- switch ---------------------------------------------------------------------------


def switch_on(tones: theme.Tones) -> QColor:
    """A switch's track while on: the accent, dimmed toward the page."""
    return theme.mix(tones.accent, tones.page, 0.55)


def knob_off(tones: theme.Tones) -> QColor:
    """A switch's knob while off: a grey between the track and the text."""
    return theme.mix(tones.page, tones.text, 0.62 if tones.dark else 0.5)


class Switch(theme.PaintedButton):
    """
    On / off, as in the NVIDIA app: a thin track under a round knob taller
    than it; on, the knob in the accent at the right, on the accent dimmed;
    off, a grey knob at the left on a grey track. It takes a little room
    around the knob's path for the focus ring.
    """

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__("", parent, checkable=True)
        self.setAccessibleName(name)
        width, _ = SWITCH
        self.setFixedSize(width + 2 * SWITCH_ROOM, SWITCH_KNOB + 2 * SWITCH_ROOM)

    def track(self) -> QRectF:
        """The thin pill, in the widget, centred on the knob's path."""
        width, height = SWITCH
        top = SWITCH_ROOM + (SWITCH_KNOB - height) / 2
        return QRectF(SWITCH_ROOM, top, width, height)

    def knob(self) -> QRectF:
        """The knob now: at the track's right end while on, else its left."""
        track = self.track()
        x = track.right() - SWITCH_KNOB if self.isChecked() else track.left()
        top = track.center().y() - SWITCH_KNOB / 2
        return QRectF(x, top, SWITCH_KNOB, SWITCH_KNOB)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        tones = theme.tones()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        on, enabled = self.isChecked(), self.isEnabled()
        hovered = enabled and self.underMouse()
        track = self.track()
        if self.shows_focus():
            ring = QRectF(self.rect())
            theme.draw_focus_ring(painter, ring, ring.height() / 2)
        if on:
            fill = switch_on(tones)
            knob = tones.accent_hover if hovered else tones.accent
        else:
            fill = tones.switch_off_hover if hovered else tones.switch_off
            knob = tones.text if hovered else knob_off(tones)
        if not enabled:  # faded, but solid: the track mustn't show through
            fill, knob = theme.mix(fill, tones.page, 0.6), theme.mix(
                knob, tones.page, 0.6
            )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        radius = track.height() / 2
        painter.drawRoundedRect(track, radius, radius)
        painter.setBrush(knob)
        painter.drawEllipse(self.knob())
        painter.end()


# -- chip -----------------------------------------------------------------------------


class FilterChip(theme.PaintedButton):
    """
    On / off in a toolbar, drawn as a chosen or other segment is (the same
    box, height and colours as Mode's), with a ✓ before its bold label
    while on. As wide as it needs: it grows by the ✓ when on (it ends a row
    of controls, so nothing after it moves). A label longer than
    `CHIP_LABEL` ends in …, the whole of it in the tooltip.
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent, checkable=True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.toggled.connect(self._toggled)
        self.set_label(text)

    def set_label(self, text: str) -> None:
        self.setText(text)
        self.setAccessibleName(text)
        self._fit()

    def label(self) -> str:
        """The label as drawn: elided past `CHIP_LABEL` (Only <a long name>)."""
        metrics = QFontMetrics(self.label_font())
        most = CHIP_LABEL * metrics.averageCharWidth()
        return metrics.elidedText(self.text(), Qt.TextElideMode.ElideRight, most)

    def colours(self) -> tuple[QColor, QColor, QColor]:
        """Its fill, edge and ink (text and ✓) now."""
        return _choice_colours(self)

    def label_font(self) -> QFont:
        return _bold(self.font()) if self.isChecked() else QFont(self.font())

    def _fit(self) -> None:
        label = self.label()
        self.setToolTip("" if label == self.text() else self.text())
        width = QFontMetrics(self.label_font()).horizontalAdvance(label)
        if self.isChecked():
            width += SEGMENT_ICON + theme.space()
        self.setFixedSize(width + 2 * SEGMENT_PADDING, theme.FIELD_HEIGHT)

    @override
    def changeEvent(self, event: QEvent) -> None:  # pylint: disable=invalid-name
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._fit()  # the label's width changed

    @Slot(bool)
    def _toggled(self, on: bool) -> None:
        del on
        self._fit()

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        fill, edge, ink = self.colours()
        _paint_box(painter, QRectF(self.rect()), fill, self.shows_focus(), edge)
        x = float(SEGMENT_PADDING)
        if self.isChecked():
            pixmap = theme.tinted_pixmap(
                "check", ink, SEGMENT_ICON, self.devicePixelRatioF()
            )
            top = round((self.height() - SEGMENT_ICON) / 2)
            painter.drawPixmap(round(x), top, pixmap)
            x += SEGMENT_ICON + theme.space()
        painter.setFont(self.label_font())
        painter.setPen(ink)
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        painter.drawText(
            QRectF(x, 0, self.width() - x, self.height()), int(flags), self.label()
        )
        painter.end()


# -- buttons --------------------------------------------------------------------------


class Button(theme.PaintedButton):
    """
    A button with a filled box: the main action (`primary`) in the accent,
    the others (Revert) in the field tone; disabled, both in the field tone
    with dim text. As wide as its bold label.
    """

    def __init__(
        self, text: str, parent: QWidget | None = None, *, primary: bool = False
    ) -> None:
        super().__init__(text, parent)
        self.primary = primary
        self.setFont(_bold(self.font()))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.set_label(text)

    def set_label(self, text: str) -> None:
        """A new label; the button takes its width."""
        self.setText(text)
        width = QFontMetrics(self.font()).horizontalAdvance(text)
        self.setFixedSize(width + 2 * BUTTON_PADDING, theme.FIELD_HEIGHT)

    def fill(self) -> QColor:
        """The box's colour now."""
        tones = theme.tones()
        if not self.isEnabled():
            return tones.field
        if self.primary:
            if self.isDown():
                return tones.accent_down
            return tones.accent_hover if self.underMouse() else tones.accent
        if self.isDown():
            return theme.mix(tones.field, tones.text, 0.12)
        return tones.field_hover if self.underMouse() else tones.field

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        tones = theme.tones()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        _paint_box(painter, QRectF(self.rect()), self.fill(), self.shows_focus())
        if not self.isEnabled():
            ink = tones.disabled
        else:
            ink = tones.on_accent if self.primary else tones.text
        painter.setPen(ink)
        painter.setFont(self.font())
        painter.drawText(
            QRectF(self.rect()), int(Qt.AlignmentFlag.AlignCenter), self.text()
        )
        painter.end()


# -- slider ---------------------------------------------------------------------------


class Slider(QAbstractSlider):
    """
    A horizontal slider: a thin track, the accent up to a round knob (a
    little larger under the pointer). A click moves the knob there, and it
    follows a drag; the keys and the wheel step as on any slider
    (QAbstractSlider). Its ends leave room for the knob and its ring.
    """

    ROOM = KNOB // 2 + 1 + 2 + theme.RING  # the knob's centre from an end

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._keyboard_focus = False
        self.setOrientation(Qt.Orientation.Horizontal)
        self.setAccessibleName(name)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(2 * self.ROOM)
        self.setMinimumWidth(4 * self.ROOM)

    def shows_focus(self) -> bool:
        return self.hasFocus() and self._keyboard_focus

    def _span(self) -> int:
        return max(self.width() - 2 * self.ROOM, 1)

    def knob_x(self) -> float:
        """The knob's centre, for the slider's position now."""
        position = QStyle.sliderPositionFromValue(
            self.minimum(), self.maximum(), self.sliderPosition(), self._span()
        )
        return float(self.ROOM + position)

    def _move_to(self, x: float) -> None:
        self.setSliderPosition(
            QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), round(x) - self.ROOM, self._span()
            )
        )

    @override
    def mousePressEvent(
        self, event: QMouseEvent
    ) -> None:  # pylint: disable=invalid-name
        self._keyboard_focus = False  # the pointer took over: no ring
        if event.button() != Qt.MouseButton.LeftButton or not self.isEnabled():
            super().mousePressEvent(event)
            return
        event.accept()
        self.setSliderDown(True)
        self._move_to(event.position().x())

    @override
    def mouseMoveEvent(
        self, event: QMouseEvent
    ) -> None:  # pylint: disable=invalid-name
        if self.isSliderDown():
            event.accept()
            self._move_to(event.position().x())
        else:
            super().mouseMoveEvent(event)

    @override
    def mouseReleaseEvent(
        self, event: QMouseEvent
    ) -> None:  # pylint: disable=invalid-name
        if self.isSliderDown() and event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            self.setSliderDown(False)
        else:
            super().mouseReleaseEvent(event)

    @override
    def focusInEvent(self, event: QFocusEvent) -> None:  # pylint: disable=invalid-name
        super().focusInEvent(event)
        self._keyboard_focus = theme.keyboard_focus(event, self._keyboard_focus)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        tones = theme.tones()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        enabled = self.isEnabled()
        middle = self.height() / 2
        left = self.ROOM - KNOB / 2
        track = QRectF(left, middle - TRACK / 2, self.width() - 2 * left, TRACK)
        painter.setBrush(tones.track)
        painter.drawRoundedRect(track, TRACK / 2, TRACK / 2)
        x = self.knob_x()
        filled = QRectF(track.left(), track.top(), x - track.left(), TRACK)
        painter.setBrush(tones.accent if enabled else tones.disabled)
        painter.drawRoundedRect(filled, TRACK / 2, TRACK / 2)

        lit = enabled and (self.underMouse() or self.isSliderDown())
        radius = KNOB / 2 + (1 if lit else 0)
        centre = QPointF(x, middle)
        if self.shows_focus():  # the accent, then the page's tone, then the knob
            painter.setBrush(tones.accent)
            painter.drawEllipse(centre, radius + 4, radius + 4)
        painter.setBrush(tones.page)
        painter.drawEllipse(centre, radius + 2, radius + 2)
        if not enabled:
            knob = tones.knob_disabled
        else:
            knob = tones.accent_hover if lit else tones.accent
        painter.setBrush(knob)
        painter.drawEllipse(centre, radius, radius)
        painter.end()
