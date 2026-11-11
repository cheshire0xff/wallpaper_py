"""
The tray icon and its menu: one flat section per playlist (monitor names,
current image, next / previous / favourite / remove), then app actions.

    konachan · AW3225QF + PLX2783H
    12345.jpg                 (opens the image)
    Next
    Previous
    [x] Favourite             (toggle)
    Remove (list and skip)
    ───
    fantasy · ZeroMOD (off)
    ...
    ───
    Paused: idle              (only while paused)
    Reload config
    Open config folder
    [ ] Start at login
    Quit

The menu is drawn from the service's latest `Overview` and never waits on
the service: actions are queued to it, and the menu redraws when the next
overview arrives. Errors from actions show as a notification.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtGui import QAction, QCursor, QDesktopServices, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..commands import (
    Command,
    MonitorView,
    Overview,
    PlaylistView,
    Reply,
    Request,
)
from ..core.autostart import Autostart
from .icon import IconState, app_icon
from .worker import ServiceHost

log = logging.getLogger(__name__)

APP_TITLE = "wallpaper_py"
MAX_TEXT = 60  # characters in a menu item before eliding


class MenuActions(Protocol):
    """What the menu's items do."""

    def run(self, request: Request) -> None: ...
    def open_image(self, image: Path) -> None: ...
    def reload(self) -> None: ...
    def open_config_folder(self) -> None: ...
    def autostart_enabled(self) -> bool: ...
    def set_autostart(self, enabled: bool) -> None: ...
    def quit(self) -> None: ...


def _slot(function: Callable[..., None], *args: object) -> Callable[..., None]:
    """A slot that ignores the signal's arguments (e.g. `checked`)."""
    return lambda *_: function(*args)


def fill_menu(menu: QMenu, overview: Overview | None, actions: MenuActions) -> QAction:
    """(Re)build `menu`; returns the "Start at login" action."""
    menu.clear()
    if overview is None:
        menu.addAction("Starting...").setEnabled(False)
        menu.addSeparator()
    else:
        for playlist in overview.playlists:
            _add_playlist(menu, playlist, actions)
            menu.addSeparator()
        if overview.error:
            item = menu.addAction(_label(f"Error: {overview.error}", 2 * MAX_TEXT))
            item.setEnabled(False)
        if overview.paused:
            menu.addAction(f"Paused: {overview.paused}").setEnabled(False)
    menu.addAction("Reload config", _slot(actions.reload))
    menu.addAction("Open config folder", _slot(actions.open_config_folder))
    autostart = menu.addAction("Start at login")
    autostart.setCheckable(True)
    autostart.setChecked(actions.autostart_enabled())
    autostart.triggered.connect(actions.set_autostart)
    menu.addSeparator()
    menu.addAction("Quit", _slot(actions.quit))
    return autostart


def _add_playlist(menu: QMenu, playlist: PlaylistView, actions: MenuActions) -> None:
    def run(command: Command) -> Callable[..., None]:
        return _slot(actions.run, Request(command, playlist.name))

    monitors = " + ".join(_monitor_label(m) for m in playlist.monitors)
    header = menu.addAction(_label(f"{playlist.name} · {monitors}"))
    header.setEnabled(False)
    font = header.font()
    font.setBold(True)
    header.setFont(font)

    current = playlist.current
    if current is None:
        menu.addAction("Nothing shown yet").setEnabled(False)
    else:
        menu.addAction(_label(current.name), _slot(actions.open_image, current))

    menu.addAction("Next", run("next")).setEnabled(playlist.attached)
    menu.addAction("Previous", run("prev")).setEnabled(playlist.attached)
    favourite = menu.addAction(
        "Favourite", run("unfav" if playlist.favourite else "fav")
    )
    favourite.setCheckable(True)
    favourite.setChecked(playlist.favourite)
    favourite.setEnabled(current is not None)
    remove = menu.addAction("Remove (list and skip)", run("remove"))
    remove.setEnabled(current is not None)


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
            lines.append(f"{playlist.name}: {name}")
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
    # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        host: ServiceHost,
        autostart: Autostart,
        autostart_command: Sequence[str],
        config_folder: Path,
        quit_app: Callable[[], None],
    ) -> None:
        super().__init__()
        self._host = host
        self._autostart = autostart
        self._autostart_command = list(autostart_command)
        self._config_folder = config_folder
        self._quit_app = quit_app
        self._icons: dict[IconState, QIcon] = {}
        self._overview: Overview | None = None
        self._error_shown: str | None = None
        self._stale = False

        self.menu = QMenu()
        self.menu.aboutToShow.connect(self._refresh_autostart)
        self.menu.aboutToHide.connect(self._after_hide)
        self._icon = QSystemTrayIcon()
        self._icon.setContextMenu(self.menu)
        self._icon.activated.connect(self._activated)
        self._autostart_action = fill_menu(self.menu, None, self)
        self._update_icon()
        host.overview_changed.connect(self.show_overview)

    def show(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.warning("No system tray found; the icon may not appear")
        self._icon.show()

    def hide(self) -> None:
        self._icon.hide()

    def show_overview(self, overview: Overview) -> None:
        self._overview = overview
        self._update_icon()
        if overview.error and overview.error != self._error_shown:
            self._notify(overview.error)
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

    def reload(self) -> None:
        self._host.reload()

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
            self._notify(f"Cannot change start at login: {err}")
        self._refresh_autostart()

    def quit(self) -> None:
        self._quit_app()

    # -- internals -------------------------------------------------------------

    def _rebuild(self) -> None:
        self._stale = False
        self._autostart_action = fill_menu(self.menu, self._overview, self)

    def _after_hide(self) -> None:
        if self._stale:
            # Later: the clicked action is triggered after the menu hides.
            QTimer.singleShot(0, self._rebuild)

    def _refresh_autostart(self) -> None:
        self._autostart_action.setChecked(self.autostart_enabled())

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        # Left click opens the menu too (until the layout window exists).
        if reason is QSystemTrayIcon.ActivationReason.Trigger:
            self.menu.popup(QCursor.pos())

    def _replied(self, reply: Reply) -> None:
        if reply.code != 0:
            self._notify(reply.err.removeprefix("error: "))

    def _notify(self, message: str) -> None:
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
