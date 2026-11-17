"""
A grid tab of the Library page (docs/plan.md, Phase 5, "Grid tabs";
docs/design/library.png): its toolbar, then an `ImageGrid` with its
`FastScroll`, or a line in place of the grid (nothing to show, or why not).

    [🔍 Filter by name      ]  [Name A–Z ▾]  (✓ Hide removed)    Badges  (●)

The toolbar has what the tab needs: the filter always; the sort where the
order can change (not Upcoming); a chip that narrows the list where the tab
has one (*Hide removed*, *Only <playlist>*); a *Badges* switch always, at
the end: it changes what the tiles show, not which (off: no star, bin or
*On screen*; History's "2 h ago" stays).
The page gives the tab its entries, already narrowed by the chip
(`set_entries`), and reads the chips and the sort back; the tab narrows
them by name as you type (debounced) and sorts them; the grid then shows
under each tile where its name matches. Back to the top when the sort or
the filter changes (and when the page says so: the chip, another
playlist); where it was when the same list comes again, read anew.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import override

from PySide6.QtCore import QEvent, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .controls import FilterChip, Switch
from .entries import Entry, Sort, arrange
from .grid import FastScroll, ImageGrid
from .settings_form import combo, line_edit
from .tiles import TileLoader
from .ui_state import GridName

FILTER_DELAY_MS = 150  # typing pauses this long before the grid narrows
FILTER_WIDTH = 320
SORT_WIDTH = 180
FILTER_ICON = 16

ADDED_SORTS: Mapping[Sort, str] = {
    "name_az": "Name A–Z",
    "name_za": "Name Z–A",
    "newest": "Newest added",
    "oldest": "Oldest added",
}
SHOWN_SORTS: Mapping[Sort, str] = {
    **ADDED_SORTS,
    "newest": "Newest shown",
    "oldest": "Oldest shown",
}


class GridTab(QWidget):
    """See the module docstring."""

    # pylint: disable=too-many-instance-attributes,too-many-arguments

    sort_changed = Signal(object)  # this GridTab: remember its sort
    switched = Signal(object)  # this GridTab: narrow again, and remember
    badges_toggled = Signal(object)  # this GridTab: remember
    counted = Signal()  # count() changed

    def __init__(
        self,
        name: GridName,
        loader: TileLoader,
        *,
        sorts: Mapping[Sort, str],
        sort: Sort | None,
        switch: bool | None,
        badges: bool,
        empty: str,
        parent: QWidget | None = None,
    ) -> None:
        """
        `name`: the page's for it (in the signals); `sorts`: the choices and
        their texts, none for no sort; `switch`: its chip's state, None for
        no chip; `badges`: shown; `empty`: the line shown when it has no
        entries.
        """
        super().__init__(parent)
        self.name = name
        self._empty = empty
        self._sorts = bool(sorts)
        self._entries: list[Entry] | None = None  # None: not read yet
        self._shown = 0
        self._needle = ""  # the filter text the grid is narrowed by now

        self.filter = line_edit("", "Filter by name", self)
        self.filter.setPlaceholderText("Filter by name")
        self.filter.setClearButtonEnabled(True)
        self.filter.setFixedWidth(FILTER_WIDTH)
        self._filter_icon = QAction(self)
        self.filter.addAction(
            self._filter_icon, QLineEdit.ActionPosition.LeadingPosition
        )
        self.sort_box = combo(self, "Sort")
        self.sort_box.setFixedWidth(SORT_WIDTH)
        for value, text in sorts.items():
            self.sort_box.addItem(text, value)
        if sort is not None:
            self.sort_box.setCurrentIndex(max(0, self.sort_box.findData(sort)))
        self.sort_box.setVisible(bool(sorts))
        self.switch = FilterChip("", self)
        self.switch.setChecked(bool(switch))
        self.switch.setVisible(switch is not None)
        self.badges_label = QLabel("Badges", self)
        self.badges = Switch("Badges", self)
        self.badges.setChecked(badges)

        self.grid = ImageGrid(loader, self)
        self.grid.set_badges(badges)
        self.fast_scroll = FastScroll(self.grid.verticalScrollBar(), self)
        self.message = theme.muted("", self, wrap=True)
        self.message.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.message.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.body = QStackedWidget(self)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(FILTER_DELAY_MS)
        self._build()
        self._tint()

        self.filter.textChanged.connect(self._typed)
        self._filter_timer.timeout.connect(self._filtered)
        self.sort_box.currentIndexChanged.connect(self._sorted)
        self.switch.toggled.connect(self._switched)
        self.badges.toggled.connect(self._badges_toggled)

    def _build(self) -> None:
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(
            theme.PAGE_MARGIN, theme.space(2), theme.PAGE_MARGIN, theme.space()
        )
        toolbar.setSpacing(theme.space(1.5))
        toolbar.addWidget(self.filter)
        toolbar.addWidget(self.sort_box)
        toolbar.addWidget(self.switch)
        toolbar.addStretch(1)
        toolbar.addWidget(self.badges_label)
        toolbar.addWidget(self.badges)

        grid_area = QWidget(self)
        grid_layout = QHBoxLayout(grid_area)
        # The bar sits in the page's right margin, as in the mockup.
        grid_layout.setContentsMargins(theme.PAGE_MARGIN, 0, theme.space(), 0)
        grid_layout.setSpacing(theme.space())
        grid_layout.addWidget(self.grid, 1)
        grid_layout.addWidget(self.fast_scroll)
        message_area = QWidget(self)
        message_layout = QVBoxLayout(message_area)
        message_layout.setContentsMargins(
            theme.PAGE_MARGIN, theme.space(), theme.PAGE_MARGIN, 0
        )
        message_layout.addWidget(self.message)
        self.body.addWidget(grid_area)
        self.body.addWidget(message_area)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(toolbar)
        layout.addWidget(self.body, 1)

    # -- what the page sets and reads ------------------------------------------------

    def set_entries(self, entries: Sequence[Entry] | None, *, top: bool) -> None:
        """
        The tab's entries, narrowed by its switch (None: not read yet, so
        nothing and no count). `top`: from the top, else where it was.
        """
        self._entries = None if entries is None else list(entries)
        self._show(top)

    def show_message(self, text: str) -> None:
        """A line in place of the grid (why there are no entries)."""
        self._entries = None
        self.grid.set_entries(())
        self._show_line(text)
        self._count(0)

    def sort(self) -> Sort | None:
        data = self.sort_box.currentData()
        return data if self._sorts else None

    def switched_on(self) -> bool:
        return self.switch.isChecked()

    def badges_shown(self) -> bool:
        return self.badges.isChecked()

    def set_switch_text(self, text: str) -> None:
        self.switch.set_label(text)

    def filtered(self) -> bool:
        """Narrowed by name now (not only typed: the grid follows after a pause)."""
        return bool(self._needle)

    def count(self) -> tuple[int, int] | None:
        """(shown, of): the images shown and those before narrowing by name."""
        if self._entries is None:
            return None
        return self._shown, len(self._entries)

    # -- narrowing and sorting -----------------------------------------------------

    def _show(self, top: bool) -> None:
        if self._entries is None:
            self.grid.set_entries(())
            self._show_line("")
            self._count(0)
            return
        self._needle = self.filter.text().strip()
        shown = arrange(self._entries, self.sort(), self._needle)
        self.grid.set_needle(self._needle)
        self.grid.set_entries(shown, top=top)
        if shown:
            self.body.setCurrentIndex(0)
        elif self._entries:
            self._show_line(f"No file name has “{self._needle}” in it.")
        else:
            self._show_line(self._empty)
        self._count(len(shown))

    def _show_line(self, text: str) -> None:
        self.message.setText(text)
        self.body.setCurrentIndex(1)

    def _count(self, shown: int) -> None:
        self._shown = shown
        self.counted.emit()

    @Slot(str)
    def _typed(self, text: str) -> None:
        del text
        self._filter_timer.start()

    @Slot()
    def _filtered(self) -> None:
        self._show(top=True)

    @Slot(int)
    def _sorted(self, index: int) -> None:
        del index
        self._show(top=True)
        self.sort_changed.emit(self)

    @Slot(bool)
    def _switched(self, on: bool) -> None:
        del on
        self.switched.emit(self)

    @Slot(bool)
    def _badges_toggled(self, on: bool) -> None:
        self.grid.set_badges(on)
        self.badges_toggled.emit(self)

    # -- the look ------------------------------------------------------------------

    def _tint(self) -> None:
        tones = theme.tones()
        pixmap = theme.tinted_pixmap(
            "search", tones.muted, FILTER_ICON, self.devicePixelRatioF()
        )
        self._filter_icon.setIcon(QIcon(pixmap))

    @override
    def changeEvent(self, event: QEvent) -> None:  # pylint: disable=invalid-name
        super().changeEvent(event)
        if theme.palette_changed(event):
            self._tint()
