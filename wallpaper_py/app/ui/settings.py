"""
The settings window: config.toml as a form (docs/plan.md, Phase 4, step 3).

The file stays the source of truth. The window edits the file the app runs
(the overview says which) like an editor would: it reads it, writes back
only what changed through core.config_edit (comments and formatting stay),
and then asks the app to `reload` it. The app stays the only writer of
state; it only reads the file again. Hand edits keep working.

- *Playlists*: the monitors as in the layout window (click one to choose
  its playlist, or "New playlist…"), then one editor per playlist: name,
  folder, order, interval, mode, monitors. A monitor is in one playlist at
  most: choosing another playlist moves it. A monitor in the config but
  not connected now stays listed (and in the file) until removed.
- *General*: pausing, and where the favourites / to-remove lists are.
- *About*: the version and the data locations (about.py).

Save and Revert work only while something changed. Problems show next to
their field and keep Save disabled; the line beside it says why. Before
writing, the new text must pass the core's own parser, and the file must
still be what the window read: if it changed on disk (a hand edit), nothing
is written and the window offers to load it again. Closing with unsaved
changes asks Save / Discard / Cancel.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QCursor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import Shiboken

from ...core import paths
from ...core.config import Config, ConfigError, loads_config
from ...core.config_edit import (
    FAVOURITES,
    IDLE,
    TO_REMOVE,
    ConfigConflictError,
    ConfigEditError,
    Field,
    PlaylistSettings,
    Settings,
    assign_monitor,
    choose_selector,
    new_playlist,
    owner,
    read_file,
    read_settings,
    remove_selector,
    render,
    resolve,
    validate,
    write_file,
)
from ...protocol import (
    DesktopMonitor,
    ErrorReply,
    Overview,
    ReloadRequest,
    ReloadResult,
    Reply,
)
from . import theme
from .about import AboutPage, OpenUrl, data_locations, version_text
from .arrangement import MonitorsView
from .client import Client
from .settings_form import (
    UID,
    PlaylistEditor,
    Problem,
    drawing,
    hint,
    monitors_text,
    problem_summary,
    row,
    stacked,
    unlisted_text,
)
from .window import Window

TITLE = "wallpaper_py · Settings[*]"  # [*]: Qt shows "*" while modified
_KEY = "key"  # Qt property: the general setting a button is about

# -- what the menus ask for (QAction data) -------------------------------------------

NEW: Literal["new"] = "new"


@dataclass(frozen=True)
class Assign:
    """Make `target` drive a monitor: a playlist (uid), None, or a NEW one."""

    monitor: str  # id
    target: int | None | Literal["new"]


@dataclass(frozen=True)
class Unlist:
    """Take a selector (a monitor not connected now) out of a playlist."""

    playlist: int
    selector: str


# -- the questions (modal; tests replace them) ---------------------------------------

Choice = Literal["save", "discard", "cancel"]


def ask_unsaved(parent: QWidget) -> Choice:
    buttons = QMessageBox.StandardButton
    box = QMessageBox(
        QMessageBox.Icon.Question,
        "Unsaved changes",
        "Save the changes to the config file before closing?",
        buttons.Save | buttons.Discard | buttons.Cancel,
        parent,
    )
    box.setDefaultButton(buttons.Save)
    answer = box.exec()
    if answer == buttons.Save:
        return "save"
    return "discard" if answer == buttons.Discard else "cancel"


def choose_folder(parent: QWidget, start: str) -> str | None:
    chosen = QFileDialog.getExistingDirectory(parent, "Choose the folder", start)
    return chosen or None


def choose_list_file(parent: QWidget, start: str) -> str | None:
    chosen, _ = QFileDialog.getSaveFileName(
        parent,
        "Choose the list file",
        start,
        "Text files (*.txt);;All files (*)",
        options=QFileDialog.Option.DontConfirmOverwrite,
    )
    return chosen or None


@dataclass(frozen=True)
class Dialogs:
    unsaved: Callable[[QWidget], Choice] = field(default=ask_unsaved)
    folder: Callable[[QWidget, str], str | None] = field(default=choose_folder)
    list_file: Callable[[QWidget, str], str | None] = field(default=choose_list_file)


# -- the window ----------------------------------------------------------------------


class _ReplyTo:
    """`Client.run`'s callback, holding the window weakly (it may close first)."""

    def __init__(self, window: SettingsWindow) -> None:
        self._window = weakref.ref(window)

    def __call__(self, reply: Reply) -> None:
        window = self._window()
        if window is not None and Shiboken.isValid(window):
            window.reloaded(reply)


class SettingsWindow(Window):
    """See the module docstring."""

    # pylint: disable=too-many-instance-attributes,too-many-public-methods

    def __init__(
        self,
        client: Client,
        dialogs: Dialogs | None = None,
        *,
        version: str | None = None,
        open_url: OpenUrl = QDesktopServices.openUrl,
    ) -> None:
        super().__init__()
        self.setWindowTitle(TITLE)
        self.resize(920, 860)
        self._client = client
        self._dialogs = dialogs or Dialogs()
        self.path: Path | None = None  # the config file (the app's)
        self._text = ""  # the file as read, or as last saved
        self._base: Settings | None = None  # None: nothing loaded
        self._config: Config | None = None  # the file as the core reads it
        self._listed: tuple[DesktopMonitor, ...] = ()
        self.problems: dict[Field, str] = {}
        self._blocker: str | None = "Nothing loaded"  # why Save is off
        self._rendered: str | None = None  # the text Save would write
        self._next_uid = 0
        self._banner_kind: str | None = None
        self._app_error: str | None = None
        self.menu: QMenu | None = None  # the open monitor menu
        self.editors: dict[int, PlaylistEditor] = {}  # by uid, in order

        self.banner = QLabel(self)
        self.banner.setWordWrap(True)
        self.banner.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.banner_button = QPushButton("Load the file again", self)
        self.banner_button.clicked.connect(self.load_file)
        self.tabs = QTabWidget(self)
        self.monitors_view = MonitorsView(self)
        self.monitors_view.set_clickable(True)
        self.monitors_view.clicked.connect(self._monitor_clicked)
        self.unlisted = hint("", self)
        self._editors_box = QVBoxLayout()
        self.add_button = QPushButton("Add playlist", self)
        self.add_button.clicked.connect(self._add_playlist)
        self._build_general()
        self._general = self._general_tab()
        self.about = AboutPage(version or version_text(), open_url, self)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.revert_button = QPushButton("Revert", self)
        self.revert_button.setToolTip("Back to what the file says")
        self.revert_button.clicked.connect(self.revert)
        self.save_button = QPushButton("Save", self)
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._save_clicked)
        self._build()
        self._refresh()

        self._set_banner("Connecting to wallpaper_py…", "connecting")
        client.overview_changed.connect(self.show_overview)
        client.failed.connect(self.show_error)
        if client.overview is not None:  # else it comes
            self.show_overview(client.overview)

    # -- building ----------------------------------------------------------------

    def _build_general(self) -> None:
        self.locked_check = QCheckBox("Pause while the session is locked", self)
        self.idle_check = QCheckBox("Pause after no keyboard or mouse input for", self)
        self.idle_edit = QLineEdit(self)
        self.idle_edit.setAccessibleName("Idle time before pausing")
        self.idle_edit.setPlaceholderText("e.g. 5m")
        self.idle_edit.setMaximumWidth(theme.space(14))
        self.list_edits: dict[str, QLineEdit] = {}
        self.list_buttons: dict[str, QPushButton] = {}
        for key, label in ((FAVOURITES, "Favourites"), (TO_REMOVE, "To remove")):
            edit = QLineEdit(self)
            edit.setAccessibleName(f"{label} file")
            name = "favourites.txt" if key == FAVOURITES else "to-remove.txt"
            edit.setPlaceholderText(f"Default: {paths.data_dir() / name}")
            button = QPushButton("Browse…", self)
            button.setProperty(_KEY, key)
            button.clicked.connect(self._browse_list)
            edit.textChanged.connect(self._edited)
            self.list_edits[key] = edit
            self.list_buttons[key] = button
        self.general_problems = {
            key: Problem(self) for key in (IDLE, FAVOURITES, TO_REMOVE)
        }
        self.locked_check.toggled.connect(self._edited)
        self.idle_check.toggled.connect(self._idle_toggled)
        self.idle_edit.textChanged.connect(self._edited)

    def _build(self) -> None:
        self.tabs.addTab(self._playlists_tab(), "Playlists")
        self.tabs.addTab(self._general, "General")
        self.tabs.addTab(self.about, "About")
        banner = QHBoxLayout()
        banner.setSpacing(theme.space())
        banner.addWidget(self.banner, 1)
        banner.addWidget(self.banner_button, 0, Qt.AlignmentFlag.AlignTop)
        buttons = QHBoxLayout()
        buttons.setSpacing(theme.space())
        buttons.addWidget(self.status, 1)
        buttons.addWidget(self.revert_button)
        buttons.addWidget(self.save_button)
        outer = QVBoxLayout(self)
        margin = theme.space(2)
        outer.setContentsMargins(margin, margin, margin, margin)
        outer.setSpacing(theme.space(1.5))
        outer.addLayout(banner)
        outer.addWidget(self.tabs, 1)
        outer.addLayout(buttons)

    def _playlists_tab(self) -> QWidget:
        content, layout = theme.page(self)
        layout.addWidget(theme.heading("Monitors", content))
        layout.addWidget(
            hint(
                "Click a monitor to choose its playlist. Monitors on one "
                "playlist show the same image (a mirrored group).",
                content,
            )
        )
        layout.addWidget(self.monitors_view)
        layout.addWidget(self.unlisted)
        layout.addSpacing(theme.space())
        layout.addWidget(theme.heading("Playlists", content))
        self._editors_box.setSpacing(theme.space(2))
        layout.addLayout(self._editors_box)
        layout.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        return scroll

    def _general_tab(self) -> QWidget:
        general, layout = theme.page(self, spacing=1)
        layout.addWidget(theme.heading("Pausing", general))
        layout.addWidget(
            hint(
                "While paused, no playlist changes, so no image goes by unseen; "
                "intervals count only active time.",
                general,
            )
        )
        layout.addWidget(self.locked_check)
        layout.addLayout(row(self.idle_check, self.idle_edit, stretch=True))
        layout.addWidget(self.general_problems[IDLE])
        layout.addSpacing(theme.space(2))
        layout.addWidget(theme.heading("Lists", general))
        layout.addWidget(
            hint(
                "Plain text, one full path per line; you can edit them by hand. "
                "Empty: the default, next to the state database.",
                general,
            )
        )
        form = QFormLayout()
        form.setHorizontalSpacing(theme.space(1.5))
        for key, label in ((FAVOURITES, "Favourites"), (TO_REMOVE, "To remove")):
            line = row(self.list_edits[key], self.list_buttons[key])
            form.addRow(label, stacked(general, line, self.general_problems[key]))
        layout.addLayout(form)
        layout.addStretch(1)
        return general

    # -- from the app ------------------------------------------------------------

    @Slot(object)
    def show_overview(self, overview: Overview) -> None:
        self._listed = overview.monitors
        if overview.error != self._app_error:
            self._app_error = overview.error
            if self._banner_kind not in ("file", "conflict"):  # those say more
                self._show_app_error()
        if self.path is None and overview.config is not None:
            self.path = overview.config  # the file the app runs: edit that one
            self.load_file()
        self._refresh()

    def _show_app_error(self) -> None:
        if self._app_error:
            self._set_banner(f"The app can't use the config: {self._app_error}", "app")
        elif self._banner_kind in ("app", "connecting"):
            self._set_banner(None)

    @Slot(str)
    def show_error(self, message: str) -> None:
        self._set_banner(f"Error: {message}", "app")

    def reloaded(self, reply: Reply) -> None:
        """The app's answer to the `reload` after a save."""
        if isinstance(reply, ReloadResult):
            lines = ["Saved; the app is using it."]
            lines += [f"Warning: {w}" for w in reply.warnings]
            self._set_banner("\n".join(lines), "saved")
        elif isinstance(reply, ErrorReply):
            self._set_banner(
                f"Saved, but the app reports: {reply.error.message}", "saved"
            )

    # -- the file ----------------------------------------------------------------

    @Slot()
    def load_file(self) -> None:
        """Read the file (again), dropping unsaved edits."""
        if self.path is None:
            return
        try:
            text = read_file(self.path)
            base = read_settings(text)
        except FileNotFoundError:
            self._unload()
            self._set_banner(
                f"There is no config file at {self.path} yet. Create one (see "
                f"docs/config.example.toml), then load it here.",
                "file",
                button=True,
            )
        except (OSError, ConfigEditError) as err:
            self._unload()
            self._set_banner(
                f"Can't edit {self.path} here: {err}. Fix it by hand, then load "
                f"it again.",
                "file",
                button=True,
            )
        else:
            if self._banner_kind in ("file", "conflict", "connecting"):
                self._set_banner(None)
                self._show_app_error()
            self._use(text, base)
        self._refresh()

    def _use(self, text: str, base: Settings) -> None:
        self._text, self._base = text, base
        try:
            assert self.path is not None
            self._config = loads_config(text, self.path, paths.data_dir())
        except ConfigError:
            self._config = None
        self._next_uid = len(base.playlists)
        self._fill(base)

    def _unload(self) -> None:
        self._base = None
        self._config = None
        self._fill(Settings())

    def _fill(self, settings: Settings) -> None:
        """The form from `settings` (without reporting each field as edited)."""
        for editor in self.editors.values():
            self._editors_box.removeWidget(editor)
            editor.deleteLater()
        self.editors = {}
        for playlist in settings.playlists:
            self._add_editor(playlist)
        widgets = [
            self.locked_check,
            self.idle_check,
            self.idle_edit,
            *self.list_edits.values(),
        ]
        for widget in widgets:
            widget.blockSignals(True)
        self.locked_check.setChecked(settings.pause_when_locked)
        idle = settings.pause_when_idle
        self.idle_check.setChecked(idle is not None)
        self.idle_edit.setText(idle if idle is not None else "5m")
        self.idle_edit.setEnabled(idle is not None)
        self.list_edits[FAVOURITES].setText(settings.favourites_file)
        self.list_edits[TO_REMOVE].setText(settings.to_remove_file)
        for widget in widgets:
            widget.blockSignals(False)

    def _add_editor(self, playlist: PlaylistSettings) -> PlaylistEditor:
        editor = PlaylistEditor(playlist, self)
        editor.edited.connect(self._edited)
        editor.delete_requested.connect(self._delete_playlist)
        editor.browse_requested.connect(self._browse_folder)
        editor.monitors_menu.aboutToShow.connect(self._fill_monitors_menu)
        editor.monitors_menu.triggered.connect(self._menu_triggered)
        self._editors_box.addWidget(editor)
        self.editors[playlist.uid] = editor
        return editor

    # -- state -------------------------------------------------------------------

    def edited(self) -> Settings | None:
        """The settings as the form has them now; None if nothing is loaded."""
        if self._base is None:
            return None
        idle = self.idle_edit.text().strip() if self.idle_check.isChecked() else None
        return Settings(
            playlists=tuple(e.settings() for e in self.editors.values()),
            pause_when_locked=self.locked_check.isChecked(),
            pause_when_idle=idle,
            favourites_file=self.list_edits[FAVOURITES].text().strip(),
            to_remove_file=self.list_edits[TO_REMOVE].text().strip(),
        )

    def dirty(self) -> bool:
        edited = self.edited()
        return edited is not None and edited != self._base

    def blocker(self) -> str | None:
        """Why Save is disabled, or None."""
        return self._blocker

    @Slot()
    def _edited(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        """Problems, buttons, the drawing: everything that follows the form."""
        edited = self.edited()
        loaded = edited is not None and self.path is not None
        self.add_button.setEnabled(loaded)
        self._general.setEnabled(loaded)
        if edited is None or self.path is None:
            self.problems = {}
            self._blocker, self._rendered = "Nothing loaded", None
            self._show_buttons(False)
            self.monitors_view.show_overview(Overview(monitors=self._listed))
            self.unlisted.setVisible(False)
            self._show_locations()
            return
        self.problems = validate(edited, self.path.parent, self._listed, self._base)
        for uid, editor in self.editors.items():
            mine = {
                key: m
                for (owner_uid, key), m in self.problems.items()
                if owner_uid == uid
            }
            editor.show_problems(mine)
            settings = edited.playlist(uid)
            assert settings is not None
            editor.set_monitors(
                settings.monitors, monitors_text(settings.monitors, self._listed)
            )
        for key, problem in self.general_problems.items():
            problem.set_problem(self.problems.get((None, key)))
        dirty = edited != self._base
        self._blocker, self._rendered = self._check(edited, dirty)
        self._show_buttons(dirty)
        self.monitors_view.show_overview(drawing(edited, self._listed))
        unlisted = unlisted_text(edited, self._listed)
        self.unlisted.setText(unlisted)
        self.unlisted.setVisible(bool(unlisted))
        self._show_locations()

    def _check(self, edited: Settings, dirty: bool) -> tuple[str | None, str | None]:
        """(why Save is off, or None; the text it would write)."""
        if not dirty:
            return "Nothing changed", None
        if self.problems:
            assert self._base is not None
            return problem_summary(self.problems, edited), None
        assert self._base is not None and self.path is not None
        try:
            text = render(self._text, self._base, edited)
            loads_config(text, self.path, paths.data_dir())
        except (ConfigEditError, ConfigError) as err:
            return f"Can't save: {err}", None
        return None, text

    def _show_buttons(self, dirty: bool) -> None:
        self.setWindowModified(dirty)
        self.revert_button.setEnabled(dirty)
        blocker = self._blocker
        self.save_button.setEnabled(blocker is None)
        self.save_button.setToolTip(
            blocker or "Write the changes to the config file, then reload it"
        )
        self.status.setText(blocker if dirty and blocker else "")

    def _show_locations(self) -> None:
        self.about.set_locations(data_locations(self.path, self._config))

    def _set_banner(
        self, text: str | None, kind: str | None = None, *, button: bool = False
    ) -> None:
        self._banner_kind = kind if text else None
        self.banner.setText(text or "")
        self.banner.setVisible(bool(text))
        self.banner_button.setVisible(bool(text) and button)

    # -- saving --------------------------------------------------------------------

    @Slot()
    def _save_clicked(self) -> None:
        self.save()

    def save(self) -> bool:
        """Write the changes and ask the app to reload; False if not saved."""
        if self.path is None or self._rendered is None:
            return False
        text = self._rendered
        try:
            write_file(self.path, text, expected=self._text)
        except ConfigConflictError:
            self._set_banner(
                f"{self.path.name} changed on disk since this window read it (a "
                f"hand edit?), so nothing was saved. Load the file again to "
                f"see it; your changes here are then lost.",
                "conflict",
                button=True,
            )
            return False
        except OSError as err:
            self._set_banner(f"Can't write {self.path}: {err}", "file")
            return False
        self._use(text, read_settings(text))
        self._set_banner("Saved; asking the app to reload it…", "saved")
        self._client.run(ReloadRequest(), _ReplyTo(self))
        self._refresh()
        return True

    @Slot()
    def revert(self) -> None:
        if self._base is not None:
            self._fill(self._base)
            self._refresh()

    def closeEvent(self, event: QCloseEvent) -> None:  # pylint: disable=invalid-name
        if self.dirty():
            choice = self._dialogs.unsaved(self)
            if choice == "cancel" or (choice == "save" and not self.save()):
                event.ignore()
                if choice == "save" and self._blocker is not None:
                    self.status.setText(self._blocker)
                return
        super().closeEvent(event)

    # -- playlists and monitors ----------------------------------------------------

    def _take_uid(self) -> int:
        uid = self._next_uid
        self._next_uid += 1
        return uid

    @Slot()
    def _add_playlist(self) -> None:
        edited = self.edited()
        if edited is None:
            return
        self._start_playlist(new_playlist(edited, self._take_uid()))

    def _start_playlist(self, playlist: PlaylistSettings) -> None:
        editor = self._add_editor(playlist)
        self._refresh()
        self.tabs.setCurrentIndex(0)
        editor.name_edit.setFocus()
        editor.name_edit.selectAll()

    @Slot(int)
    def _delete_playlist(self, uid: int) -> None:
        editor = self.editors.pop(uid, None)
        if editor is not None:
            self._editors_box.removeWidget(editor)
            editor.deleteLater()
            self._refresh()

    @Slot(int)
    def _browse_folder(self, uid: int) -> None:
        editor = self.editors.get(uid)
        if editor is None or self.path is None:
            return
        current = editor.directory_edit.text().strip()
        start = str(self.path.parent / current) if current else ""
        chosen = self._dialogs.folder(self, start)
        if chosen:
            editor.directory_edit.setText(str(Path(chosen)))

    @Slot()
    def _browse_list(self) -> None:
        key = str(self.sender().property(_KEY))
        edit = self.list_edits[key]
        default = "favourites.txt" if key == FAVOURITES else "to-remove.txt"
        start = edit.text().strip() or str(paths.data_dir() / default)
        chosen = self._dialogs.list_file(self, start)
        if chosen:
            edit.setText(str(Path(chosen)))

    @Slot(bool)
    def _idle_toggled(self, on: bool) -> None:
        self.idle_edit.setEnabled(on)
        self._refresh()

    def _monitor(self, monitor_id: str) -> DesktopMonitor | None:
        return next((m for m in self._listed if m.id == monitor_id), None)

    @Slot(str)
    def _monitor_clicked(self, monitor_id: str) -> None:
        menu = self.monitor_menu(monitor_id)
        if menu is not None:
            menu.popup(QCursor.pos())

    def monitor_menu(self, monitor_id: str) -> QMenu | None:
        """A monitor's choices: each playlist, no playlist, a new one."""
        edited = self.edited()
        monitor = self._monitor(monitor_id)
        if edited is None or monitor is None:
            return None
        if self.menu is not None:
            self.menu.deleteLater()
        current = owner(edited, monitor, self._listed)
        menu = QMenu(self)
        title = menu.addAction(monitor.name)
        title.setEnabled(False)
        group = QActionGroup(menu)
        choices: list[tuple[str, int | None]] = [
            (p.name or "(no name)", p.uid) for p in edited.playlists
        ]
        choices.append(("No playlist", None))
        for text, uid in choices:
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked((current.uid if current else None) == uid)
            action.setData(Assign(monitor.id, uid))
            group.addAction(action)
        menu.addSeparator()
        menu.addAction("New playlist…").setData(Assign(monitor.id, NEW))
        menu.triggered.connect(self._menu_triggered)
        self.menu = menu
        return menu

    @Slot()
    def _fill_monitors_menu(self) -> None:
        """A playlist's Monitors menu, as it opens: every monitor, ticked if in it."""
        menu = self.sender()
        assert isinstance(menu, QMenu)
        self.fill_monitors_menu(menu, int(menu.property(UID)))

    def fill_monitors_menu(self, menu: QMenu, uid: int) -> None:
        menu.clear()
        edited = self.edited()
        playlist = edited.playlist(uid) if edited is not None else None
        if edited is None or playlist is None:
            return
        for monitor in self._listed:
            current = owner(edited, monitor, self._listed)
            mine = current is not None and current.uid == uid
            text = monitor.name + (" (off)" if monitor.geometry is None else "")
            if current is not None and not mine:
                text += f" — now in {current.name}"
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(mine)
            action.setData(Assign(monitor.id, None if mine else uid))
        for selector in playlist.monitors:
            if resolve(selector, self._listed) is None:
                action = menu.addAction(f"{selector} (not connected)")
                action.setCheckable(True)
                action.setChecked(True)
                action.setData(Unlist(uid, selector))
        if menu.isEmpty():
            menu.addAction("No monitors found").setEnabled(False)

    @Slot(QAction)
    def _menu_triggered(self, action: QAction) -> None:
        self.choose(action.data())

    def choose(self, choice: Assign | Unlist | None) -> None:
        """Do what a menu item asks for."""
        edited = self.edited()
        if edited is None or choice is None:
            return
        listed = self._listed
        if isinstance(choice, Unlist):
            self._set_monitors(
                remove_selector(edited, choice.playlist, choice.selector)
            )
            return
        monitor = self._monitor(choice.monitor)
        if monitor is None:
            return
        if choice.target == NEW:
            freed = assign_monitor(edited, monitor, listed, None)
            self._set_monitors(freed)
            selector = choose_selector(monitor, listed)
            self._start_playlist(new_playlist(freed, self._take_uid(), (selector,)))
            return
        self._set_monitors(assign_monitor(edited, monitor, listed, choice.target))

    def _set_monitors(self, settings: Settings) -> None:
        for playlist in settings.playlists:
            editor = self.editors.get(playlist.uid)
            if editor is not None:
                editor.set_monitors(
                    playlist.monitors, monitors_text(playlist.monitors, self._listed)
                )
        self._refresh()
