"""
The Library page: a placeholder until Phase 5 (docs/design/library.png
shows where it is going: tabs, a toolbar and the image grid).
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from . import theme


class LibraryPage(QWidget):
    """The page's title and a note that it comes later."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = theme.page_layout(self)
        layout.addWidget(theme.title("Library", self))
        layout.addWidget(
            theme.muted(
                "Browsing the library comes in the next version: every image of a "
                "playlist, favourites, the ones to remove, history and what comes "
                "next.",
                self,
                wrap=True,
            )
        )
        layout.addStretch(1)
