"""
The Settings page's form (settings.py), as rows without boxes
(docs/design/settings-monitors.dc.html): each `SettingRow` has its name
and a muted one-line description on the left and its control on the
right, all controls in one column; fields are borderless and filled.

One `PlaylistEditor` per playlist: a header row (its name, a muted summary
while closed, a chevron) that opens or closes it, and when open its rows:
Name, Folder, Order, Changes, the read-only *Shown on*, and *Delete
playlist*. Also the `Problem` shown under a field, and the `ColourButton`
of a monitor's background. Widgets only report edits; the page owns the
draft (settings_draft.py).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import override

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPalette, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core.model import BLACK
from ...core.settings import MANUAL, Order
from . import theme
from .settings_draft import DIRECTORY, INTERVAL, NAME, NEW_INTERVAL, PlaylistDraft

EVERY, MANUALLY = "every", MANUAL  # the Changes row's choices

ORDERS: tuple[tuple[str, Order, str], ...] = (  # label, value, description
    ("Shuffle", Order.SHUFFLE, "Shuffle shows every image once before any repeats"),
    ("Sorted", Order.SORTED, "Sorted goes in natural order: 2 before 10"),
)
CHANGES = (("Every", EVERY), ("Manually", MANUALLY))
CHANGES_HELP = {
    EVERY: "Active time: pauses while locked, idle or asleep",
    MANUALLY: "Only next, previous or pick change it",
}


# -- small widgets -------------------------------------------------------------------


class Problem(QWidget):
    """A problem with a field, under it: an alert icon and the text."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.message: str | None = None
        self._icon = QLabel(self)
        self._text = QLabel(self)
        self._text.setWordWrap(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.space(0.5))
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self._text, 1)
        self._tint()
        self.setVisible(False)

    def set_problem(self, message: str | None) -> None:
        if message != self.message:
            self.message = message
            self._text.setText(message or "")
            self.setVisible(bool(message))
            self.setAccessibleDescription(message or "")

    def _tint(self) -> None:
        color = theme.tones().error
        palette = QPalette()  # only this role; the rest is inherited
        palette.setColor(QPalette.ColorRole.WindowText, color)
        self._text.setPalette(palette)
        ratio = self.devicePixelRatioF()
        self._icon.setPixmap(theme.tinted_pixmap("circle-alert", color, 16, ratio))

    @override
    def changeEvent(self, event: QEvent) -> None:  # pylint: disable=invalid-name
        super().changeEvent(event)
        if theme.palette_changed(event):
            self._tint()


def borderless(field: QLineEdit | QComboBox) -> QLineEdit | QComboBox:
    """A field as the design has it: no frame, the filled Base / Button tone."""
    field.setFrame(False)
    field.setFixedHeight(theme.FIELD_HEIGHT)
    if isinstance(field, QLineEdit):
        padding = theme.FIELD_PADDING
        field.setTextMargins(padding, 0, padding, 0)
    return field


def line_edit(text: str, name: str, parent: QWidget) -> QLineEdit:
    edit = QLineEdit(text, parent)
    edit.setAccessibleName(name)
    borderless(edit)
    return edit


def combo(parent: QWidget, name: str) -> QComboBox:
    box = theme.FieldCombo(parent)
    box.setAccessibleName(name)
    borderless(box)
    return box


def hint(text: str, parent: QWidget) -> QLabel:
    return theme.muted(text, parent, wrap=True)


def select(box: QComboBox, value: object) -> None:
    """Choose the item with this data, without reporting it as an edit."""
    index = max(box.findData(value), 0)
    if index != box.currentIndex():
        box.blockSignals(True)
        box.setCurrentIndex(index)
        box.blockSignals(False)


class SettingRow(QWidget):
    """
    One setting: its name and a muted one-line description on the left; its
    control (a widget or a row of them) in the control column on the right,
    with the field's `Problem` under it.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        name: str,
        description: str,
        control: QWidget | QHBoxLayout,
        problem: Problem | None = None,
        *,
        parent: QWidget | None = None,
        indent: int = 0,
    ) -> None:
        super().__init__(parent)
        self.label = QLabel(name, self)
        # Wraps rather than widening the row (a long "Mirrors …" text).
        self.description = theme.muted(description, self, wrap=True)
        self.description.setVisible(bool(description))
        labels = QVBoxLayout()
        labels.setSpacing(2)
        labels.addWidget(self.label)
        labels.addWidget(self.description)

        column = QWidget(self)
        column.setFixedWidth(theme.CONTROL_WIDTH)
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(theme.space(0.5))
        if isinstance(control, QHBoxLayout):
            column_layout.addLayout(control)
        else:
            column_layout.addWidget(control)
        if problem is not None:
            column_layout.addWidget(problem)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(indent, theme.space(1.25), 0, theme.space(1.25))
        layout.setSpacing(theme.space(3))
        layout.addLayout(labels, 1)
        layout.addWidget(column)
        self.setMinimumHeight(theme.space(7))

    def set_description(self, text: str) -> None:
        self.description.setText(text)
        self.description.setVisible(bool(text))


def control_row(*widgets: QWidget) -> QHBoxLayout:
    """Widgets side by side in the control column; fields take the room."""
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(theme.space(1.5))
    for widget in widgets:
        stretch = 1 if isinstance(widget, (QLineEdit, QComboBox, QLabel)) else 0
        layout.addWidget(widget, stretch)
    return layout


class ColourButton(theme.PaintedButton):
    """
    A colour as a swatch on a field-toned button, beside a drop-down; a
    click is for choosing another (the page opens the dialog).
    """

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(name, parent)
        self.setFixedSize(theme.space(7), theme.FIELD_HEIGHT)
        self._colour = QColor(BLACK)

    def set_colour(self, colour: str) -> None:
        """`#rrggbb`."""
        self._colour = QColor(colour)
        self.setToolTip(f"{colour}: click to choose another colour")
        self.update()

    def colour(self) -> str:
        return self._colour.name()

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        tones = theme.tones()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect())
        fill = tones.field
        if self.underMouse() and self.isEnabled():
            fill = theme.mix(fill, tones.text, 0.08)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(area, theme.RADIUS_SMALL, theme.RADIUS_SMALL)
        # The swatch, outlined so black shows on a dark field (white on light).
        swatch = area.adjusted(5.5, 5.5, -5.5, -5.5)
        painter.setPen(QPen(tones.line, 1))
        painter.setBrush(self._colour)
        if not self.isEnabled():
            painter.setOpacity(0.4)
        painter.drawRoundedRect(swatch, 2, 2)
        painter.setOpacity(1.0)
        self.focus_ring(painter)
        painter.end()


class PlaylistSummary(theme.PaintedButton):
    """
    A playlist's header row: its name, a muted summary (while closed), a
    chevron (up while open); a click opens or closes it.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self.summary = ""
        self.problem = False
        self.expanded = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(theme.space(7))

    def set_summary(
        self, name: str, summary: str, problem: bool, expanded: bool
    ) -> None:
        self.setText(name)
        self.summary = summary
        self.problem = problem
        self.expanded = expanded
        note = " (has a problem)" if problem else ""
        action = "Close" if expanded else "Open"
        text = f"{name}: {summary}{note}" if summary else f"{name}{note}"
        self.setAccessibleName(f"{text}. {action}")
        self.update()

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        tones = theme.tones()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        if self.underMouse() or self.hasFocus():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(tones.hover)
            painter.drawRoundedRect(rect, theme.RADIUS_SMALL, theme.RADIUS_SMALL)
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        painter.setFont(theme.scaled_font(self.font(), 1.15, bold=True))
        painter.setPen(tones.text)
        name = painter.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, int(rect.width() / 3)
        )
        painter.drawText(rect, flags, name)
        left = painter.fontMetrics().horizontalAdvance(name) + theme.space(2)
        right = self._paint_icons(painter, tones)
        painter.setFont(theme.scaled_font(self.font(), 0.93))
        painter.setPen(tones.muted)
        summary = QRectF(left, 0, max(right - left, 0), rect.height())
        text = painter.fontMetrics().elidedText(
            self.summary, Qt.TextElideMode.ElideRight, int(summary.width())
        )
        painter.drawText(summary, flags, text)
        painter.end()

    def _paint_icons(self, painter: QPainter, tones: theme.Tones) -> float:
        """The chevron (and the alert) at the right; returns where they start."""
        size, ratio = theme.SMALL_ICON, self.devicePixelRatioF()
        middle = round((self.height() - size) / 2)
        x = round(self.width() - size - theme.space(0.5))
        chevron = theme.tinted_pixmap("chevron-down", tones.text, size, ratio)
        painter.save()
        painter.translate(QPointF(x + size / 2, middle + size / 2))
        if self.expanded:
            painter.rotate(180)
        painter.drawPixmap(QPointF(-size / 2, -size / 2), chevron)
        painter.restore()
        if self.problem:
            x -= size + theme.space()
            alert = theme.tinted_pixmap("circle-alert", tones.error, size, ratio)
            painter.drawPixmap(QPointF(x, middle), alert)
        return x - theme.space()


# -- one playlist ------------------------------------------------------------------


class PlaylistEditor(QWidget):
    """
    One playlist's fields, under a thin divider. `edited` on every change;
    the page collects the values (`draft`) and shows problems
    (`show_problems`), the summary (`set_summary`) and where it is shown
    (`set_shown_on`). The header row asks the page to open or close it
    (`toggle_requested`).
    """

    # pylint: disable=too-many-instance-attributes

    edited = Signal()
    delete_requested = Signal(str)  # playlist id
    browse_requested = Signal(str)  # playlist id
    toggle_requested = Signal(str)  # playlist id

    def __init__(self, playlist: PlaylistDraft, parent: QWidget | None = None):
        super().__init__(parent)
        self.playlist_id = playlist.id
        self._expanded = True

        self.summary = PlaylistSummary(self)
        self.delete_button = theme.TextButton("Delete playlist", self)
        self.name_edit = line_edit(playlist.name, "Playlist name", self)
        self.directory_edit = line_edit(playlist.directory, "Folder", self)
        self.directory_edit.setPlaceholderText("Choose a folder")
        self.directory_edit.setCursorPosition(0)  # the start of the path shows
        self.browse_button = theme.TextButton("Browse…", self)
        self.order_combo = combo(self, "Order")
        for text, order, description in ORDERS:
            self.order_combo.addItem(text, order)
            self.order_combo.setItemData(
                self.order_combo.count() - 1, description, Qt.ItemDataRole.ToolTipRole
            )
        select(self.order_combo, playlist.order)
        self.changes_combo = combo(self, "Changes")
        for text, value in CHANGES:
            self.changes_combo.addItem(text, value)
        select(self.changes_combo, MANUALLY if playlist.manual else EVERY)
        self.interval_edit = line_edit(
            NEW_INTERVAL if playlist.manual else playlist.interval,
            "Interval, in active time",
            self,
        )
        self.interval_edit.setPlaceholderText("e.g. 30m")
        self.interval_edit.setFixedWidth(theme.space(14))
        self.interval_edit.setVisible(not playlist.manual)
        self.shown_label = theme.ValueLabel(parent=self)
        self.shown_label.setWordWrap(True)
        self.problems = {key: Problem(self) for key in (NAME, DIRECTORY, INTERVAL)}
        self._build()

        self.name_edit.textChanged.connect(self._changed)
        self.directory_edit.textChanged.connect(self._changed)
        self.interval_edit.textChanged.connect(self._changed)
        self.order_combo.currentIndexChanged.connect(self._order_changed)
        self.changes_combo.currentIndexChanged.connect(self._changes_changed)
        self.delete_button.clicked.connect(self._delete)
        self.browse_button.clicked.connect(self._browse)
        self.summary.clicked.connect(self._toggle)

    def _build(self) -> None:
        indent = theme.space(3)
        self.order_row = SettingRow(
            "Order", "", self.order_combo, parent=self, indent=indent
        )
        self.changes_row = SettingRow(
            "Changes",
            "",
            control_row(self.changes_combo, self.interval_edit),
            self.problems[INTERVAL],
            parent=self,
            indent=indent,
        )
        self.shown_row = SettingRow(
            "Shown on",
            "Set on each monitor, above",
            self.shown_label,
            parent=self,
            indent=indent,
        )
        rows = [
            SettingRow(
                "Name",
                "",
                self.name_edit,
                self.problems[NAME],
                parent=self,
                indent=indent,
            ),
            SettingRow(
                "Folder",
                "Scanned with its subfolders",
                control_row(self.directory_edit, self.browse_button),
                self.problems[DIRECTORY],
                parent=self,
                indent=indent,
            ),
            self.order_row,
            self.changes_row,
            self.shown_row,
        ]
        self._describe()

        self.details = QWidget(self)
        details = QVBoxLayout(self.details)
        details.setContentsMargins(0, 0, 0, theme.space())
        details.setSpacing(0)
        for row in rows:
            details.addWidget(row)
        delete = QHBoxLayout()
        delete.setContentsMargins(theme.space(3), theme.space(0.5), 0, 0)
        delete.addWidget(self.delete_button)
        delete.addStretch(1)
        details.addLayout(delete)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(theme.Divider(self))
        layout.addWidget(self.summary)
        layout.addWidget(self.details)

    # -- values ------------------------------------------------------------------

    def draft(self) -> PlaylistDraft:
        return PlaylistDraft(
            id=self.playlist_id,
            name=self.name_edit.text(),
            directory=self.directory_edit.text().strip(),
            order=self.order_combo.currentData(),
            interval=MANUAL if self.manual() else self.interval_edit.text().strip(),
        )

    def show_problems(self, problems: Mapping[str, str]) -> None:
        for key, problem in self.problems.items():
            problem.set_problem(problems.get(key))

    def set_summary(self, summary: str, problem: bool) -> None:
        """The header row; the summary shows only while closed."""
        self.summary.set_summary(
            self.name_edit.text().strip() or "(no name)",
            "" if self._expanded else summary,
            problem,
            self._expanded,
        )

    def set_shown_on(self, text: str, muted: bool) -> None:
        self.shown_label.setText(text)
        self.shown_label.setEnabled(not muted)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self.details.setVisible(expanded)

    def expanded(self) -> bool:
        return self._expanded

    def manual(self) -> bool:
        return bool(self.changes_combo.currentData() == MANUALLY)

    def _describe(self) -> None:
        order = self.order_combo.currentIndex()
        self.order_row.set_description(ORDERS[max(order, 0)][2])
        self.changes_row.set_description(
            CHANGES_HELP[MANUALLY if self.manual() else EVERY]
        )

    # -- slots -------------------------------------------------------------------

    @Slot()
    def _changed(self) -> None:
        self.edited.emit()

    @Slot(int)
    def _order_changed(self, index: int) -> None:
        del index
        self._describe()
        self.edited.emit()

    @Slot(int)
    def _changes_changed(self, index: int) -> None:
        del index
        self.interval_edit.setVisible(not self.manual())
        self._describe()
        self.edited.emit()

    @Slot()
    def _delete(self) -> None:
        self.delete_requested.emit(self.playlist_id)

    @Slot()
    def _browse(self) -> None:
        self.browse_requested.emit(self.playlist_id)

    @Slot()
    def _toggle(self) -> None:
        self.toggle_requested.emit(self.playlist_id)
