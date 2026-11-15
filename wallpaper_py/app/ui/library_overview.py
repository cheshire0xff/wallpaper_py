"""
The Library page's Overview tab: a playlist's library in numbers, named
as in the phone app's Information dialog (docs/plan.md, "Overview tab").

    This cycle                                        6 %  ▓░░░░░
      Images    D:\\pictures\\KONACHAN, scanned 2 min ago  20,431  Rescan
      Seen      shown in this shuffle cycle              1,204  Reset cycle…
      Unseen    still to come before the next reshuffle 19,179
      Skipped   listed to remove, or unreadable             48
    Lists  shared by all playlists
      Favourites  favourites.txt    312 listed · 298 available · 120 here
      To remove   to-remove.txt      60 listed ·  48 available ·  48 here

A sorted playlist has no cycle: "This walk", where it is ("at 1,205 of
20,383"), and no *Reset cycle…*. The tab only shows what it is given and
reports the buttons; the page asks the app (library.py). A button's
failure shows under the numbers (`show_notice`) until the next click or
playlist.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ...core.service import ListStats, PlaylistStats
from ...core.settings import Order, format_ago
from . import theme
from .settings_form import SettingRow

NUMBER_WIDTH = theme.space(12)  # the numbers' column, right-aligned
BAR_WIDTH = theme.space(18)

SHUFFLE_TEXTS = (  # heading; seen; unseen
    "This cycle",
    "shown in this shuffle cycle",
    "still to come before the next reshuffle",
)
SORTED_TEXTS = (
    "This walk",
    "the current image and those before it, in name order",
    "after the current image, in name order",
)


def count(number: int) -> str:
    return f"{number:,}"


def list_text(stats: ListStats, here: int) -> str:
    return (
        f"{count(stats.listed)} listed · {count(stats.available)} available · "
        f"{count(here)} here"
    )


class OverviewTab(QWidget):
    """
    See the module docstring. `show_stats` fills it, `show_message` puts a
    line in place of the numbers (an error, or no playlists).
    """

    # pylint: disable=too-many-instance-attributes

    rescan_requested = Signal()
    reset_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.stats: PlaylistStats | None = None
        self.message = QLabel(self)
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.content = QWidget(self)

        self.heading = theme.heading(SHUFFLE_TEXTS[0], self.content)
        self.progress = QLabel(self.content)
        self.progress_bar = QProgressBar(self.content)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedWidth(BAR_WIDTH)
        self.progress_bar.setAccessibleName("Seen")

        self.images = self._number("Images")
        self.seen = self._number("Seen")
        self.unseen = self._number("Unseen")
        self.skipped = self._number("Skipped")
        self.rescan_button = theme.TextButton("Rescan", self.content)
        self.rescan_button.setToolTip(
            "Scan the folder again (the app's scan is up to 5 min old)"
        )
        self.rescan_button.clicked.connect(self.rescan_requested)
        self.reset_button = theme.TextButton("Reset cycle…", self.content)
        self.reset_button.setAccessibleName("Reset cycle")
        self.reset_button.setToolTip("Every image unseen again")
        self.reset_button.clicked.connect(self.reset_requested)
        self.notice = QLabel(self.content)
        self.notice.setWordWrap(True)
        self.notice.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.notice.setVisible(False)

        self.images_row = self._row("Images", self.images, self.rescan_button)
        self.seen_row = self._row("Seen", self.seen, self.reset_button)
        self.unseen_row = self._row("Unseen", self.unseen)
        self.skipped_row = self._row("Skipped", self.skipped)
        self.skipped_row.set_description("listed to remove, or unreadable")
        self.favourites = QLabel(self.content)
        self.to_remove = QLabel(self.content)
        indent = theme.space(2)
        self.favourites_row = SettingRow(
            "Favourites", "", self.favourites, parent=self.content, indent=indent
        )
        self.to_remove_row = SettingRow(
            "To remove", "", self.to_remove, parent=self.content, indent=indent
        )
        self._build()
        self.show_message(None)
        self.content.setVisible(False)

    def _number(self, name: str) -> QLabel:
        label = QLabel(self.content)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        label.setFixedWidth(NUMBER_WIDTH)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setAccessibleName(name)
        return label

    def _row(
        self, name: str, number: QLabel, action: QWidget | None = None
    ) -> SettingRow:
        control = QHBoxLayout()
        control.setContentsMargins(0, 0, 0, 0)
        control.setSpacing(theme.space(2))
        control.addWidget(number)
        if action is not None:
            control.addWidget(action)
        control.addStretch(1)
        return SettingRow(name, "", control, parent=self.content, indent=theme.space(2))

    def _build(self) -> None:
        cycle = QHBoxLayout()
        cycle.setSpacing(theme.space(1.5))
        cycle.addWidget(self.heading, 1)
        cycle.addWidget(self.progress)
        cycle.addWidget(self.progress_bar)
        self.notice.setContentsMargins(theme.space(2), 0, 0, theme.space())
        lists = QHBoxLayout()
        lists.setSpacing(theme.space(1.5))
        lists.addWidget(theme.heading("Lists", self.content))
        lists.addWidget(theme.muted("shared by all playlists", self.content), 1)

        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(cycle)
        for row in (self.images_row, self.seen_row, self.unseen_row, self.skipped_row):
            layout.addWidget(row)
        layout.addWidget(self.notice)
        layout.addSpacing(theme.space(2))
        layout.addWidget(theme.Divider(self.content))
        layout.addSpacing(theme.space(2))
        layout.addLayout(lists)
        layout.addWidget(self.favourites_row)
        layout.addWidget(self.to_remove_row)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, theme.space(2), 0, theme.space(2))
        outer.setSpacing(theme.space())
        outer.addWidget(self.message)
        outer.addWidget(self.content)
        outer.addStretch(1)

    # -- filling -----------------------------------------------------------------

    def show_message(self, text: str | None) -> None:
        """A line instead of the numbers (None: the numbers, if any)."""
        self.message.setText(text or "")
        self.message.setVisible(bool(text))
        self.content.setVisible(not text and self.stats is not None)

    def clear(self) -> None:
        """Nothing to show (another playlist chosen, its numbers not here yet)."""
        self.stats = None
        self.show_notice(None)
        self.show_message(None)

    def show_notice(self, text: str | None) -> None:
        """What went wrong with a button (Rescan, Reset cycle…); None: nothing."""
        self.notice.setText(text or "")
        self.notice.setVisible(bool(text))

    def show_stats(
        self, stats: PlaylistStats, favourites: ListStats, to_remove: ListStats
    ) -> None:
        self.stats = stats
        shuffle = stats.order is Order.SHUFFLE
        heading, seen, unseen = SHUFFLE_TEXTS if shuffle else SORTED_TEXTS
        total = stats.seen + stats.unseen
        self.heading.setText(heading)
        self.heading.setAccessibleName(heading)
        if shuffle:
            self.progress.setText(f"{stats.percent_seen} %")
        else:
            self.progress.setText(f"at {count(stats.seen)} of {count(total)}")
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(stats.seen)
        self.images.setText(count(stats.images))
        self.images_row.set_description(
            f"{stats.directory}, scanned {format_ago(stats.scan_age)}"
        )
        self.seen.setText(count(stats.seen))
        self.seen_row.set_description(seen)
        self.unseen.setText(count(stats.unseen))
        self.unseen_row.set_description(unseen)
        self.skipped.setText(count(stats.skipped))
        self.reset_button.setVisible(shuffle)
        for row, value, marks, here in (
            (self.favourites_row, self.favourites, favourites, stats.favourites_here),
            (self.to_remove_row, self.to_remove, to_remove, stats.to_remove_here),
        ):
            row.set_description(marks.file.name)
            row.description.setToolTip(str(marks.file))
            value.setText(list_text(marks, here))
        self.show_message(None)

    def set_resetting(self, resetting: bool) -> None:
        """While a reset runs: the button waits."""
        self.reset_button.setEnabled(not resetting)
