"""
The tray icon and its menu: one flat section per playlist (monitor names,
current image, next / previous / favourite / remove / hold), then app
actions.

    konachan (held) · AW3225QF + PLX2783H
    12345.jpg                 (opens the image)
    Next
    Previous
    [x] Favourite             (toggle)
    Remove (list and skip)
    [x] Hold                  (toggle; not for a manual playlist)
    ───
    fantasy (manual) · ZeroMOD (off)
    ...
    ───
    Paused: idle              (only while paused)
    Open wallpaper_py         (the window on Home, in the UI process,
    Library                    started if needed; on Library)
    Settings…                 (on Settings)
    Reload config             (the `reload` command; errors and warnings notify)
    Open config folder
    [ ] Start at login
    Quit

The menu is drawn from the service's latest `Overview` and never waits on
the service: actions are queued to it, and the menu redraws when the next
overview arrives. Errors from actions show as a notification.

Each item carries plain data saying what it asks for (`QAction.setData`),
and one slot (`Tray._triggered`, on `QMenu.triggered`) does it. No closures
per item: a lambda holding the Tray, connected to an action of the Tray's
own menu, would keep the Tray alive until that action is deleted (see
ipc.LineServer for how that crashes).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from enum import Enum
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QObject, QTimer, QUrl, Slot
from PySide6.QtGui import QAction, QCursor, QDesktopServices, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..core.autostart import Autostart
from ..protocol import (
    Command,
    ErrorReply,
    MonitorView,
    Overview,
    PlaylistView,
    ReloadResult,
    Reply,
    Request,
)
from .icon import IconState, app_icon
from .ui.launcher import PageName
from .worker import ServiceHost

log = logging.getLogger(__name__)

APP_TITLE = "wallpaper_py"
MAX_TEXT = 60  # characters in a menu item before eliding


class MenuActions(Protocol):
    """What the menu's items do."""

    def run(self, request: Request) -> None: ...
    def open_image(self, image: Path) -> None: ...
    def open_window(self, page: PageName) -> None: ...
    def reload(self) -> None: ...
    def open_config_folder(self) -> None: ...
    def set_autostart(self, enabled: bool) -> None: ...
    def quit(self) -> None: ...


class AppItem(Enum):
    """Menu items about the app rather than a playlist."""

    RELOAD = "reload"
    CONFIG_FOLDER = "config folder"
    AUTOSTART = "autostart"
    QUIT = "quit"


# What an item asks for, stored on its QAction: a command, an image to
# open, a page to open the window on, or an app item. Items without data do
# nothing.
MenuItem = Request | Path | PageName | AppItem


def activate(action: QAction, actions: MenuActions) -> None:
    """Do what a triggered item asks for."""
    item: MenuItem | None = action.data()
    match item:
        case Request():
            actions.run(item)
        case Path():
            actions.open_image(item)
        case "home" | "library" | "settings":
            actions.open_window(item)
        case AppItem.RELOAD:
            actions.reload()
        case AppItem.CONFIG_FOLDER:
            actions.open_config_folder()
        case AppItem.AUTOSTART:
            actions.set_autostart(action.isChecked())  # already toggled
        case AppItem.QUIT:
            actions.quit()


def _add(menu: QMenu, text: str, item: MenuItem) -> QAction:
    action = menu.addAction(text)
    action.setData(item)
    return action


def fill_menu(menu: QMenu, overview: Overview | None, autostart: bool) -> QAction:
    """
    (Re)build `menu`; `autostart`: whether start at login is on. Returns the
    "Start at login" action.
    """
    menu.clear()
    if overview is None:
        menu.addAction("Starting...").setEnabled(False)
        menu.addSeparator()
    else:
        for playlist in overview.playlists:
            _add_playlist(menu, playlist)
            menu.addSeparator()
        if overview.error:
            item = menu.addAction(_label(f"Error: {overview.error}", 2 * MAX_TEXT))
            item.setEnabled(False)
        if overview.paused:
            menu.addAction(f"Paused: {overview.paused}").setEnabled(False)
    _add(menu, f"Open {APP_TITLE}", "home")
    _add(menu, "Library", "library")
    _add(menu, "Settings…", "settings")
    _add(menu, "Reload config", AppItem.RELOAD)
    _add(menu, "Open config folder", AppItem.CONFIG_FOLDER)
    start_at_login = _add(menu, "Start at login", AppItem.AUTOSTART)
    start_at_login.setCheckable(True)
    start_at_login.setChecked(autostart)
    menu.addSeparator()
    _add(menu, "Quit", AppItem.QUIT)
    return start_at_login


def _add_playlist(menu: QMenu, playlist: PlaylistView) -> None:
    def run(text: str, command: Command) -> QAction:
        request = Request(command=command, target=playlist.name)
        return _add(menu, text, request)

    monitors = " + ".join(_monitor_label(m) for m in playlist.monitors)
    header = menu.addAction(_label(f"{_playlist_label(playlist)} · {monitors}"))
    header.setEnabled(False)
    font = header.font()
    font.setBold(True)
    header.setFont(font)

    current = playlist.current
    if current is None:
        menu.addAction("Nothing shown yet").setEnabled(False)
    else:
        _add(menu, _label(current.name), current)

    run("Next", "next").setEnabled(playlist.attached)
    run("Previous", "prev").setEnabled(playlist.attached)
    favourite = run("Favourite", "unfav" if playlist.favourite else "fav")
    favourite.setCheckable(True)
    favourite.setChecked(playlist.favourite)
    favourite.setEnabled(current is not None)
    run("Remove (list and skip)", "remove").setEnabled(current is not None)
    if not playlist.manual:  # a manual playlist never rotates: nothing to hold
        hold = run("Hold", "resume" if playlist.held else "hold")
        hold.setCheckable(True)
        hold.setChecked(playlist.held)


def _playlist_label(playlist: PlaylistView) -> str:
    """The name, and whether it rotates by itself."""
    if playlist.manual:
        return f"{playlist.name} (manual)"
    return f"{playlist.name} (held)" if playlist.held else playlist.name


def _monitor_label(monitor: MonitorView) -> str:
    if monitor.attached is None:
        return f"{monitor.name} (not found)"
    return monitor.name if monitor.attached else f"{monitor.name} (off)"


def _elide(text: str, limit: int) -> str:
    """Cut in the middle, so a file's extension stays visible."""
    if len(text) <= limit:
        return text
    half = (limit - 1) // 2
    return f"{text[:half]}…{text[-(limit - 1 - half):]}"


def _label(text: str, limit: int = MAX_TEXT) -> str:
    """Menu text: elided, `&` kept literal (not a shortcut marker)."""
    return _elide(text, limit).replace("&", "&&")


def tooltip(overview: Overview | None) -> str:
    lines = [APP_TITLE]
    if overview is None:
        lines.append("Starting...")
    elif overview.error:
        lines.append("Error: see the menu")
    else:
        for playlist in overview.playlists:
            name = playlist.current.name if playlist.current else "-"
            lines.append(f"{_playlist_label(playlist)}: {name}")
        if overview.paused:
            lines.append(f"Paused: {overview.paused}")
    # Windows cuts tray tooltips at 127 characters.
    return _elide("\n".join(lines), 127)


def icon_state(overview: Overview | None) -> IconState:
    if overview is not None and overview.error:
        return IconState.ERROR
    if overview is not None and overview.paused:
        return IconState.PAUSED
    return IconState.NORMAL


class Tray(QObject):
    # pylint: disable=too-many-instance-attributes,too-many-arguments
    def __init__(
        self,
        host: ServiceHost,
        autostart: Autostart,
        autostart_command: Sequence[str],
        config_folder: Path,
        quit_app: Callable[[], None],
        *,
        open_window: Callable[[PageName], None],
    ) -> None:
        super().__init__()
        self._host = host
        self._open_window = open_window
        self._autostart = autostart
        self._autostart_command = list(autostart_command)
        self._config_folder = config_folder
        self._quit_app = quit_app
        self._icons: dict[IconState, QIcon] = {}
        self._overview: Overview | None = None
        self._error_shown: str | None = None
        self._reloading = False  # the reload's reply tells what happened
        self._stale = False

        self.menu = QMenu()
        self.menu.triggered.connect(self._triggered)
        self.menu.aboutToShow.connect(self._refresh_autostart)
        self.menu.aboutToHide.connect(self._after_hide)
        self._icon = QSystemTrayIcon()
        self._icon.setContextMenu(self.menu)
        self._icon.activated.connect(self._activated)
        self._autostart_action = fill_menu(self.menu, None, self.autostart_enabled())
        self._update_icon()
        host.overview_changed.connect(self.show_overview)

    def show(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.warning("No system tray found; the icon may not appear")
        self._icon.show()

    def hide(self) -> None:
        self._icon.hide()

    @Slot(object)
    def show_overview(self, overview: Overview) -> None:
        self._overview = overview
        self._update_icon()
        if (
            overview.error
            and overview.error != self._error_shown
            and not self._reloading
        ):
            self.notify(overview.error)
        self._error_shown = overview.error
        if self.menu.isVisible():
            self._stale = True  # never rebuild under the cursor
        else:
            self._rebuild()

    # -- MenuActions ---------------------------------------------------------

    def run(self, request: Request) -> None:
        self._host.submit(request, self._replied)

    def open_image(self, image: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(image)))

    def open_window(self, page: PageName) -> None:
        self._open_window(page)

    def reload(self) -> None:
        self._reloading = True
        self._host.reload(self._reloaded)

    def open_config_folder(self) -> None:
        self._config_folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._config_folder)))

    def autostart_enabled(self) -> bool:
        try:
            return self._autostart.enabled()
        except OSError as err:
            log.warning("Cannot read the autostart entry: %s", err)
            return False

    def set_autostart(self, enabled: bool) -> None:
        try:
            if enabled:
                self._autostart.enable(self._autostart_command)
                log.info("Start at login: %s", " ".join(self._autostart_command))
            else:
                self._autostart.disable()
                log.info("Start at login: off")
        except OSError as err:
            self.notify(f"Cannot change start at login: {err}")
        self._refresh_autostart()

    def quit(self) -> None:
        self._quit_app()

    # -- internals -------------------------------------------------------------

    @Slot(QAction)
    def _triggered(self, action: QAction) -> None:
        activate(action, self)

    @Slot()
    def _rebuild(self) -> None:
        self._stale = False
        self._autostart_action = fill_menu(
            self.menu, self._overview, self.autostart_enabled()
        )

    @Slot()
    def _after_hide(self) -> None:
        if self._stale:
            # Later: the clicked action is triggered after the menu hides.
            QTimer.singleShot(0, self._rebuild)

    @Slot()
    def _refresh_autostart(self) -> None:
        self._autostart_action.setChecked(self.autostart_enabled())

    @Slot(QSystemTrayIcon.ActivationReason)
    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # Left click opens the menu too (it has *Open wallpaper_py*).
        if reason is QSystemTrayIcon.ActivationReason.Trigger:
            self.menu.popup(QCursor.pos())

    def _replied(self, reply: Reply) -> None:
        if isinstance(reply, ErrorReply):
            self.notify(reply.error.message)

    def _reloaded(self, reply: Reply) -> None:
        """Reload config: the error (even if it is the old one), or warnings."""
        self._reloading = False
        if isinstance(reply, ErrorReply):
            self.notify(reply.error.message)
        elif isinstance(reply, ReloadResult) and reply.warnings:
            self.notify("\n".join(reply.warnings))

    @Slot(str)
    def notify(self, message: str) -> None:
        """A warning balloon (and the log)."""
        log.warning("%s", message)
        self._icon.showMessage(
            APP_TITLE, message, QSystemTrayIcon.MessageIcon.Warning, 10_000
        )

    def _update_icon(self) -> None:
        state = icon_state(self._overview)
        if state not in self._icons:
            self._icons[state] = app_icon(state)
        self._icon.setIcon(self._icons[state])
        self._icon.setToolTip(tooltip(self._overview))
