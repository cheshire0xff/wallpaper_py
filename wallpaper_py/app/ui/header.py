"""
A page's header band (docs/design/README.md, "The look"), across the page
under the title bar: the page's title on the left, its own controls on the
right (`add`), on the `header` tone.
"""

from __future__ import annotations

from typing import override

from PySide6.QtGui import QPainter, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from . import theme

HEADER_HEIGHT = theme.space(8)


def title(text: str, parent: QWidget | None = None) -> QLabel:
    """A page's title ("Home", "Settings")."""
    label = QLabel(text, parent)
    label.setFont(theme.scaled_font(label.font(), 1.55, bold=True))
    label.setAccessibleName(text)
    return label


class PageHeader(QWidget):
    """See the module docstring."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(HEADER_HEIGHT)
        self.title = title(text, self)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(theme.PAGE_MARGIN, 0, theme.PAGE_MARGIN, 0)
        self._row.setSpacing(theme.space(2))
        self._row.addWidget(self.title)
        self._row.addStretch(1)

    def add(self, widget: QWidget) -> None:
        """A control on the right, after those added before."""
        self._row.addWidget(widget)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.tones().header)
        painter.end()
