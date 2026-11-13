"""
The main window: a sidebar (Home, Library, Settings, and the app-wide
status at its bottom) and the selected page (docs/plan.md, Phase 4, "One
window"; docs/design/).

`open_page` shows the window on a page and brings it to the front: the
UI process calls it at start and for each "show" request on its socket
(`wallpaper ui <page>`, the tray's entries). Pages stay alive while the
window is open, so switching pages keeps unsaved settings; closing with
unsaved settings asks Save / Discard / Cancel on the Settings page.

The tones come from the application's palette (theme.derive_tones), set
on the window as palette roles, and are derived again when the palette
changes (the system switching light / dark).
"""

from __future__ import annotations

from typing import Literal, override

from PySide6.QtCore import QEvent, QRectF, Qt, Signal, Slot
from PySide6.QtGui import (
    QCloseEvent,
    QFont,
    QPainter,
    QPaintEvent,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...protocol import Overview
from . import theme
from .client import Client
from .home import HomePage
from .launcher import PageName
from .library import LibraryPage
from .settings import SettingsPage

NAVIGATION: tuple[tuple[PageName, str, str], ...] = (  # page, label, icon
    ("home", "Home", "house"),
    ("library", "Library", "images"),
    ("settings", "Settings", "sliders-horizontal"),
)
SIDEBAR_WIDTH = 220
TITLE = "wallpaper_py"

StatusKind = Literal["connecting", "ok", "paused", "error"]


def status_of(overview: Overview | None) -> tuple[str, StatusKind]:
    """The sidebar's status line for an overview, and its kind (the dot)."""
    if overview is None:
        return "Connecting…", "connecting"
    if overview.error:
        return "Config error", "error"
    if overview.paused:
        return f"Rotation paused: {overview.paused}", "paused"
    return "Rotation active", "ok"


class NavButton(theme.PaintedButton):
    """
    A sidebar item: icon and label. The selected one: an accent bar on its
    left edge, a faint accent fill, the icon in the accent, a bold label.
    """

    def __init__(
        self, page: PageName, text: str, icon_name: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(text, parent, checkable=True)
        self.page: PageName = page
        self._icon_name = icon_name
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(theme.space(5))

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        tones = theme.tones()
        selected = self.isChecked()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        rect = QRectF(self.rect())
        if selected or self.underMouse():
            painter.setBrush(tones.accent_fill if selected else tones.hover)
            painter.drawRoundedRect(rect, theme.RADIUS_SMALL, theme.RADIUS_SMALL)
        if selected:
            painter.setBrush(tones.accent)
            edge = QRectF(0, (rect.height() - 20) / 2, 3, 20)
            painter.drawRoundedRect(edge, 1.5, 1.5)
        size = theme.ICON_SIZE
        icon = theme.tinted_pixmap(
            self._icon_name,
            tones.accent if selected else tones.text,
            size,
            self.devicePixelRatioF(),
        )
        left = 14
        painter.drawPixmap(round(left), round((rect.height() - size) / 2), icon)
        font = QFont(self.font())
        if selected:
            font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(tones.text)
        text = rect.adjusted(left + size + 12, 0, 0, 0)
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        painter.drawText(text, int(flags), self.text())
        self.focus_ring(painter)
        painter.end()


class StatusDot(QWidget):
    """The small round mark before the status: ok, error, or muted."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind: StatusKind = "connecting"
        self.setFixedSize(8, 8)

    def set_kind(self, kind: StatusKind) -> None:
        self.kind = kind
        self.update()

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        tones = theme.tones()
        color = {"ok": tones.ok, "error": tones.error}.get(self.kind, tones.muted)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QRectF(self.rect()))
        painter.end()


class Sidebar(QWidget):
    """The app's name, a `NavButton` per page, and the status at the bottom."""

    page_chosen = Signal(str)  # PageName

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(SIDEBAR_WIDTH)
        self.setAccessibleName("Pages")
        name = QLabel(TITLE, self)
        name.setFont(theme.scaled_font(name.font(), 1.25, bold=True))
        name.setContentsMargins(theme.space(1.5), 4, 0, theme.space(2))
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[PageName, NavButton] = {}
        self.dot = StatusDot(self)
        self.status = theme.muted("", self)
        self.status.setFont(theme.scaled_font(self.font(), 0.86))

        layout = QVBoxLayout(self)
        margin = theme.space(1.5)
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(4)
        layout.addWidget(name)
        for page, text, icon_name in NAVIGATION:
            button = NavButton(page, text, icon_name, self)
            button.clicked.connect(self._clicked)
            self._group.addButton(button)
            self.buttons[page] = button
            layout.addWidget(button)
        layout.addStretch(1)
        status = QHBoxLayout()
        status.setContentsMargins(margin, theme.space(), margin, theme.space())
        status.setSpacing(theme.space())
        status.addWidget(self.dot)
        status.addWidget(self.status, 1)
        layout.addLayout(status)

    def set_current(self, page: PageName) -> None:
        self.buttons[page].setChecked(True)

    def current(self) -> PageName:
        return next(p for p, b in self.buttons.items() if b.isChecked())

    def set_status(self, text: str, kind: StatusKind, detail: str = "") -> None:
        self.status.setText(text)
        self.status.setToolTip(detail)
        self.status.setAccessibleName(f"{text}: {detail}" if detail else text)
        self.dot.set_kind(kind)

    @Slot()
    def _clicked(self) -> None:
        button = self.sender()
        assert isinstance(button, NavButton)
        self.page_chosen.emit(button.page)


class MainWindow(QWidget):
    """See the module docstring. `closed` once it has closed."""

    # pylint: disable=too-many-instance-attributes

    closed = Signal()

    def __init__(
        self,
        client: Client,
        *,
        home: HomePage | None = None,
        library: LibraryPage | None = None,
        settings: SettingsPage | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(f"{TITLE}[*]")  # [*]: "*" while settings are unsaved
        self.resize(1200, 800)
        self.setMinimumSize(880, 560)
        self._applied: theme.Tones | None = None
        self.home = home if home is not None else HomePage(client)
        self.library = library if library is not None else LibraryPage()
        self.settings = settings if settings is not None else SettingsPage(client)
        self.pages: dict[PageName, QWidget] = {
            "home": self.home,
            "library": self.library,
            "settings": self.settings,
        }
        self.sidebar = Sidebar(self)
        self.stack = QStackedWidget(self)
        for page in self.pages.values():
            self.stack.addWidget(page)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.sidebar)
        body.addWidget(theme.Divider(self, Qt.Orientation.Vertical))
        body.addWidget(self.stack, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(theme.Divider(self))  # under the title area
        layout.addLayout(body, 1)

        self.setAutoFillBackground(True)
        self._apply_tones()
        self.show_page("home")
        self.sidebar.page_chosen.connect(self.show_page)
        self.home.page_wanted.connect(self.show_page)
        self.settings.modified.connect(self._modified)
        client.overview_changed.connect(self._show_status)
        client.failed.connect(self._show_failure)
        self._show_status(client.overview)

    # -- pages -----------------------------------------------------------------

    @Slot(str)
    def show_page(self, page: str) -> None:
        name: PageName = page if page in self.pages else "home"
        self.stack.setCurrentWidget(self.pages[name])
        self.sidebar.set_current(name)

    @Slot(str)
    def open_page(self, page: str) -> None:
        """Show the window on `page` and bring it to the front."""
        self.show_page(page)
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def current_page(self) -> PageName:
        return self.sidebar.current()

    # -- state -------------------------------------------------------------------

    @Slot(object)
    def _show_status(self, overview: Overview | None) -> None:
        text, kind = status_of(overview)
        detail = overview.error if overview is not None and overview.error else ""
        self.sidebar.set_status(text, kind, detail or "")

    @Slot(str)
    def _show_failure(self, message: str) -> None:
        self.sidebar.set_status("Not connected", "error", message)

    @Slot(bool)
    def _modified(self, modified: bool) -> None:
        self.setWindowModified(modified)

    # -- tones -------------------------------------------------------------------

    def _apply_tones(self) -> None:
        derived = theme.tones()
        if derived == self._applied:  # e.g. the change our own setPalette made
            return
        self._applied = derived
        self.setPalette(theme.window_palette(derived))
        self.update()

    def applied_tones(self) -> theme.Tones | None:
        """The tones the window was last drawn with."""
        return self._applied

    @override
    def changeEvent(self, event: QEvent) -> None:  # pylint: disable=invalid-name
        super().changeEvent(event)
        if theme.palette_changed(event):
            self._apply_tones()

    # -- closing -----------------------------------------------------------------

    @override
    def closeEvent(self, event: QCloseEvent) -> None:  # pylint: disable=invalid-name
        if self.settings.dirty():
            self.show_page("settings")  # the question is about what is shown
            if not self.settings.confirm_close():
                event.ignore()
                return
        super().closeEvent(event)
        if event.isAccepted():
            self.home.shutdown()
            self.closed.emit()
