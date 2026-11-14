"""
The Settings page, monitor-first (docs/plan.md, Phase 4b;
docs/design/settings-monitors.dc.html): settings as rows, sub-tabs, and
Save / Revert with the status line in a footer bar.

The app keeps the settings (in its state database); the page reads them
with the `settings` command and saves the whole edit with
`save_settings`, with the revision it read: the app refuses the save if
they changed since (`stale_settings`), and checks what the values mean
again. The page never reads the database itself.

- *Playlists*: the monitors as on Home (click one to set it up; it is
  outlined), each with a preview of its playlist's current image in its
  look as edited, so an unsaved change shows at once (no image for a new
  playlist, or one given another folder). Under the drawing, the selected
  monitor's rows: *Monitor* (a drop-down that also lists switched-off and
  not-connected monitors, which aren't drawn; those get *Forget
  monitor*), *Playlist* (None, each playlist, *New playlist…*; several
  monitors on one playlist mirror it), *Mode*. Then one editor per
  playlist (settings_form.PlaylistEditor): at most one is open, the others
  show a summary.
- *General*: pausing, and where the favourites / to-remove lists are.
- *About*: the version and the data locations (about.py).

Save and Revert work only while something changed. Problems show next to
their field and keep Save disabled; the line beside it says why (else what
changed). Unsaved edits stay while other pages are shown; closing the
window with them asks Save / Discard / Cancel (`confirm_close`): Save
closes the window once the app has saved.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, override

from PySide6.QtCore import QEvent, Qt, Signal, Slot
from PySide6.QtGui import QDesktopServices, QFont, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import Shiboken

from ...core import paths
from ...core.model import ImageMode, MonitorId
from ...core.settings import MonitorSettings, Settings, SettingsError, check_settings
from ...protocol import (
    DesktopMonitor,
    ErrorReply,
    Overview,
    PlaylistView,
    Reply,
    SaveSettingsRequest,
    SaveSettingsResult,
    SettingsRequest,
    SettingsResult,
)
from . import theme
from .about import AboutPage, OpenUrl, data_locations, version_text
from .arrangement import MonitorsView
from .client import Client
from .settings_draft import (
    DEFAULT_IDLE,
    FAVOURITES,
    IDLE,
    MODE_HELP,
    MODE_NAMES,
    TO_REMOVE,
    Draft,
    Field,
    MonitorEntry,
    change_summary,
    current_images,
    drawing,
    entry_names,
    mode_look,
    monitor_entries,
    playlist_help,
    problem_summary,
    problems,
    shown_on,
    summary_text,
)
from .thumbnails import ThumbnailLoader
from .settings_form import (
    PlaylistEditor,
    Problem,
    SettingRow,
    combo,
    control_row,
    hint,
    line_edit,
    select,
)

TABS = ("Playlists", "General", "About")
_KEY = "key"  # Qt property: the general setting a button is about
NEW = "new"  # the Playlist drop-down's "New playlist…"
NO_PLAYLIST = ""  # its "None"

# -- the questions (modal; tests replace them) ---------------------------------------

Choice = Literal["save", "discard", "cancel"]


def ask_unsaved(parent: QWidget) -> Choice:
    buttons = QMessageBox.StandardButton
    box = QMessageBox(
        QMessageBox.Icon.Question,
        "Unsaved changes",
        "Save the changes to the settings before closing?",
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


# -- the page ------------------------------------------------------------------------


class _ReplyTo:
    """`Client.run`'s callback, holding the page weakly (it may close first)."""

    def __init__(
        self, page: SettingsPage, method: Callable[[SettingsPage, Reply], None]
    ):
        self._page = weakref.ref(page)
        self._method = method

    def __call__(self, reply: Reply) -> None:
        page = self._page()
        if page is not None and Shiboken.isValid(page):
            self._method(page, reply)


BannerKind = Literal["connecting", "app", "load", "stale", "saved"]


class SettingsPage(QWidget):
    """See the module docstring. `modified(bool)` when unsaved edits come or go."""

    # pylint: disable=too-many-instance-attributes,too-many-public-methods

    modified = Signal(bool)

    def __init__(  # pylint: disable=too-many-arguments
        self,
        client: Client,
        dialogs: Dialogs | None = None,
        *,
        loader: ThumbnailLoader,
        version: str | None = None,
        open_url: OpenUrl = QDesktopServices.openUrl,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._dialogs = dialogs or Dialogs()
        self._base: Settings | None = None  # as saved; None: nothing loaded
        self._base_draft = Draft()
        self._revision = 0
        # The monitors' rows as edited; None: nothing loaded. The rest of the
        # edit is in the widgets (the playlists in their editors): `edited`.
        self._monitors: tuple[MonitorSettings, ...] | None = None
        self._loading = False
        self._saving: Settings | None = None  # sent, not answered yet
        self._close_when_saved = False
        self._listed: tuple[DesktopMonitor, ...] = ()
        self._shown: tuple[PlaylistView, ...] = ()  # the app's, for their images
        self._drawn: frozenset[Path] = frozenset()  # the images drawn now
        self.entries: list[MonitorEntry] = []
        self.selected: MonitorId | None = None
        self.problems: dict[Field, str] = {}
        self._blocker: str | None = "Nothing loaded"  # why Save is off
        self._banner_kind: BannerKind | None = None
        self._app_error: str | None = None
        self.editors: dict[str, PlaylistEditor] = {}  # by playlist id, in order
        self.open_id: str | None = None  # the one playlist shown open
        self._modified = False

        self.banner, self.banner_button = self._build_banner()
        self.tabs = theme.Tabs(self)
        self.stack = QStackedWidget(self)
        self.tabs.current_changed.connect(self.stack.setCurrentIndex)
        self.monitors_view = MonitorsView(self)
        self.loader = loader  # the window's, shared with Home; it closes it
        self._build_monitor_rows()
        self._editors_box = QVBoxLayout()
        self._editors_box.setSpacing(0)
        self.add_button = theme.TextButton("+ Add playlist", self)
        self.add_button.clicked.connect(self._add_playlist)
        self._build_general()
        self._playlists = self._playlists_tab()
        self._general = self._general_tab()
        self.about = AboutPage(version or version_text(), open_url, self)
        self.status = theme.muted("", self, wrap=True)
        self.revert_button = QPushButton("Revert", self)
        self.save_button = QPushButton("Save", self)
        self._set_up_widgets()
        self._build()
        self._color_save()
        self._refresh()

        self._set_banner("Connecting to wallpaper_py…", "connecting")
        client.overview_changed.connect(self.show_overview)
        client.failed.connect(self.show_error)
        if client.overview is not None:  # else it comes
            self.show_overview(client.overview)

    # -- building ----------------------------------------------------------------

    def _build_banner(self) -> tuple[QLabel, theme.TextButton]:
        banner = QLabel(self)
        banner.setWordWrap(True)
        banner.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        button = theme.TextButton("Load the settings again", self)
        button.clicked.connect(self.load)
        return banner, button

    def _build_monitor_rows(self) -> None:
        indent = theme.space(3)
        self.monitor_combo = combo(self, "Monitor")
        self.monitor_row = SettingRow(
            "Monitor",
            "Also lists monitors that are switched off or not connected",
            self.monitor_combo,
            parent=self,
        )
        bold = QFont(self.monitor_row.label.font())
        bold.setWeight(QFont.Weight.DemiBold)
        self.monitor_row.label.setFont(bold)
        self.forget_button = theme.TextButton("Forget monitor", self)
        self.away_row = SettingRow(
            "",
            "Not drawn; it keeps its settings until it is back",
            control_row(self.forget_button, QWidget(self)),
            parent=self,
            indent=indent,
        )
        self.playlist_combo = combo(self, "Playlist")
        self.playlist_row = SettingRow(
            "Playlist", "", self.playlist_combo, parent=self, indent=indent
        )
        self.mode_combo = combo(self, "Mode")
        for mode, name in MODE_NAMES.items():
            self.mode_combo.addItem(name, mode)
        self.mode_row = SettingRow(
            "Mode", "", self.mode_combo, parent=self, indent=indent
        )
        self.monitor_combo.currentIndexChanged.connect(self._monitor_chosen)
        self.playlist_combo.currentIndexChanged.connect(self._playlist_chosen)
        self.mode_combo.currentIndexChanged.connect(self._mode_chosen)
        self.forget_button.clicked.connect(self._forget)

    def _build_general(self) -> None:
        self.locked_check = QCheckBox("Pause", self)
        self.locked_check.setAccessibleName("Pause while the session is locked")
        self.idle_check = QCheckBox("Pause after", self)
        self.idle_check.setAccessibleName("Pause when idle")
        self.idle_edit = line_edit("", "Idle time before pausing", self)
        self.idle_edit.setPlaceholderText("e.g. 5m")
        self.idle_edit.setFixedWidth(theme.space(14))
        self.list_edits: dict[str, QLineEdit] = {}
        self.list_buttons: dict[str, theme.TextButton] = {}
        for key, label in ((FAVOURITES, "Favourites"), (TO_REMOVE, "To remove")):
            edit = line_edit("", f"{label} file", self)
            name = "favourites.txt" if key == FAVOURITES else "to-remove.txt"
            edit.setPlaceholderText(f"Default: {name}")
            edit.setToolTip(f"Empty: {paths.data_dir() / name}")
            button = theme.TextButton("Browse…", self)
            button.setAccessibleName(f"Browse for the {label.lower()} file")
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

    def _set_up_widgets(self) -> None:
        self.monitors_view.set_heights(220, 320, 0.25)
        self.monitors_view.set_clickable(True)
        self.monitors_view.clicked.connect(self.select_monitor)
        self.loader.loaded.connect(self._image_loaded)
        self.revert_button.setToolTip("Back to the saved settings")
        self.revert_button.setFixedHeight(theme.FIELD_HEIGHT)
        self.revert_button.clicked.connect(self.revert)
        self.save_button.setDefault(True)
        self.save_button.setFixedHeight(theme.FIELD_HEIGHT)
        self.save_button.clicked.connect(self._save_clicked)

    def _build(self) -> None:
        tabs = (self._playlists, self._general, self._scrolling(self.about))
        for name, tab in zip(TABS, tabs):
            self.tabs.add(name)
            self.stack.addWidget(tab)

        header = QWidget(self)
        header_layout = theme.page_layout(header)
        header_layout.setSpacing(theme.space(1.5))
        header_layout.addWidget(theme.title("Settings", header))
        header_layout.addWidget(self.tabs)
        banner = QHBoxLayout()
        margin = theme.PAGE_MARGIN
        banner.setContentsMargins(margin, theme.space(1.5), margin, 0)
        banner.setSpacing(theme.space())
        banner.addWidget(self.banner, 1)
        banner.addWidget(self.banner_button, 0, Qt.AlignmentFlag.AlignTop)

        footer = QHBoxLayout()
        footer.setContentsMargins(margin, theme.space(1.5), margin, theme.space(1.5))
        footer.setSpacing(theme.space(1.5))
        footer.addWidget(self.status, 1)
        footer.addWidget(self.revert_button)
        footer.addWidget(self.save_button)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(header)
        outer.addLayout(banner)
        outer.addWidget(self.stack, 1)
        outer.addWidget(theme.Divider(self))
        outer.addLayout(footer)

    def _scrolling(self, content: QWidget) -> QScrollArea:
        """A tab's content, scrolling, within the page's margins."""
        box = QWidget(self)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(theme.PAGE_MARGIN, 0, theme.PAGE_MARGIN, 0)
        layout.addWidget(content)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(box)
        return scroll

    def _section(self, title: str, note: str, parent: QWidget) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, theme.space(1.25))
        header.addWidget(theme.heading(title, parent), 1)
        header.addWidget(theme.muted(note, parent))
        return header

    def _playlists_tab(self) -> QWidget:
        content = QWidget(self)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, theme.space(2), 0, theme.space(2))
        layout.setSpacing(0)
        layout.addLayout(
            self._section("Monitors", "Click a monitor to set it up", content)
        )
        layout.addWidget(self.monitors_view)
        layout.addSpacing(theme.space())
        for row in (self.monitor_row, self.away_row, self.playlist_row, self.mode_row):
            layout.addWidget(row)
        layout.addSpacing(theme.space(2))
        layout.addWidget(theme.Divider(content))
        layout.addSpacing(theme.space(2))
        layout.addLayout(self._section("Playlists", "Which images, and when", content))
        layout.addLayout(self._editors_box)
        layout.addWidget(theme.Divider(content))
        layout.addSpacing(theme.space())
        layout.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        return self._scrolling(content)

    def _general_tab(self) -> QWidget:
        general = QWidget(self)
        layout = QVBoxLayout(general)
        layout.setContentsMargins(0, theme.space(2), 0, theme.space(2))
        layout.setSpacing(0)
        layout.addWidget(theme.heading("Pausing", general))
        layout.addWidget(
            SettingRow(
                "While locked",
                "Nothing changes while the session is locked",
                self.locked_check,
                parent=general,
            )
        )
        layout.addWidget(
            SettingRow(
                "When idle",
                "After no keyboard or mouse input for this long",
                self._idle_row(),
                self.general_problems[IDLE],
                parent=general,
            )
        )
        layout.addWidget(
            hint(
                "While paused, no image goes by unseen: intervals count only "
                "active time.",
                general,
            )
        )
        layout.addSpacing(theme.space(2))
        layout.addWidget(theme.Divider(general))
        lists = theme.heading("Lists", general)
        lists.setContentsMargins(0, theme.space(2), 0, 0)
        layout.addWidget(lists)
        for key, label in ((FAVOURITES, "Favourites"), (TO_REMOVE, "To remove")):
            layout.addWidget(
                SettingRow(
                    label,
                    "One full path per line; empty: the default",
                    control_row(self.list_edits[key], self.list_buttons[key]),
                    self.general_problems[key],
                    parent=general,
                )
            )
        layout.addWidget(
            hint(
                "Plain text files: you can edit them by hand. The default is "
                "next to the state database.",
                general,
            )
        )
        layout.addStretch(1)
        return self._scrolling(general)

    def _idle_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.space(1.5))
        row.addWidget(self.idle_check)
        row.addWidget(self.idle_edit)
        row.addStretch(1)
        return row

    def _color_save(self) -> None:
        """Save is the one solid accent button (palette roles, not a stylesheet)."""
        tones = theme.tones()
        palette = QPalette()  # only these roles; the rest is inherited
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
            palette.setColor(group, QPalette.ColorRole.Button, tones.accent)
            palette.setColor(group, QPalette.ColorRole.ButtonText, tones.on_accent)
        self.save_button.setPalette(palette)

    @override
    def changeEvent(self, event: QEvent) -> None:  # pylint: disable=invalid-name
        super().changeEvent(event)
        if theme.palette_changed(event):
            self._color_save()

    # -- from the app ------------------------------------------------------------

    @Slot(object)
    def show_overview(self, overview: Overview) -> None:
        self._listed = overview.monitors
        self._shown = overview.playlists
        self.loader.want(p.current for p in overview.playlists if p.current)
        if overview.error != self._app_error:
            self._app_error = overview.error
            if self._banner_kind in (None, "app", "connecting"):
                self._show_app_error()
        if overview.error is None and self._base is None and not self._loading:
            self.load()
        self._refresh()

    def _show_app_error(self) -> None:
        if self._app_error:
            self._set_banner(f"The app has stopped: {self._app_error}", "app")
        else:
            self._set_banner(None)

    @Slot(str)
    def show_error(self, message: str) -> None:
        self._set_banner(f"Error: {message}", "app")

    @Slot()
    def load(self) -> None:
        """Read the saved settings (again), dropping unsaved edits."""
        self._loading = True
        self._client.run(SettingsRequest(), _ReplyTo(self, SettingsPage.loaded))

    def loaded(self, reply: Reply) -> None:
        """The answer to `load`."""
        self._loading = False
        if isinstance(reply, ErrorReply):
            self._set_banner(
                f"Can't read the settings: {reply.error.message}", "load", button=True
            )
            return
        if not isinstance(reply, SettingsResult):
            return
        if self._banner_kind in ("load", "stale", "connecting"):
            self._set_banner(None)
            self._show_app_error()
        self._use(reply.settings, reply.revision)
        self._fill(self._base_draft)
        self._refresh()

    def _use(self, settings: Settings, revision: int) -> None:
        """The saved settings: what the edit is compared with."""
        self._base, self._revision = settings, revision
        self._base_draft = Draft.of(settings)

    # -- the form ----------------------------------------------------------------

    def _fill(self, draft: Draft) -> None:
        """The form from `draft` (without reporting each field as edited)."""
        self._monitors = draft.monitors
        for editor in self.editors.values():
            self._editors_box.removeWidget(editor)
            editor.hide()
            editor.deleteLater()
        self.editors = {}
        if draft.playlist(self.open_id or "") is None:
            self.open_id = None
        for playlist in draft.playlists:
            self._add_editor(PlaylistEditor(playlist, self))
        widgets = [
            self.locked_check,
            self.idle_check,
            self.idle_edit,
            *self.list_edits.values(),
        ]
        for widget in widgets:
            widget.blockSignals(True)
        self.locked_check.setChecked(draft.pause_when_locked)
        idle = draft.pause_when_idle
        self.idle_check.setChecked(idle is not None)
        self.idle_edit.setText(idle if idle is not None else DEFAULT_IDLE)
        self.idle_edit.setEnabled(idle is not None)
        self.list_edits[FAVOURITES].setText(draft.favourites_file)
        self.list_edits[TO_REMOVE].setText(draft.to_remove_file)
        for widget in widgets:
            widget.blockSignals(False)

    def _add_editor(self, editor: PlaylistEditor) -> None:
        editor.set_expanded(editor.playlist_id == self.open_id)
        editor.edited.connect(self._edited)
        editor.delete_requested.connect(self._delete_playlist)
        editor.browse_requested.connect(self._browse_folder)
        editor.toggle_requested.connect(self._toggle_playlist)
        self._editors_box.addWidget(editor)
        self.editors[editor.playlist_id] = editor

    def edited(self) -> Draft | None:
        """The settings as the form has them now; None if nothing is loaded."""
        if self._monitors is None:
            return None
        idle = self.idle_edit.text().strip() if self.idle_check.isChecked() else None
        return Draft(
            monitors=self._monitors,
            playlists=tuple(e.draft() for e in self.editors.values()),
            pause_when_locked=self.locked_check.isChecked(),
            pause_when_idle=idle,
            favourites_file=self.list_edits[FAVOURITES].text().strip(),
            to_remove_file=self.list_edits[TO_REMOVE].text().strip(),
        )

    def dirty(self) -> bool:
        edited = self.edited()
        if edited is None or edited == self._base_draft:
            return False
        settings = edited.settings_or_none()
        return settings is None or settings != self._base_draft.settings()

    def blocker(self) -> str | None:
        """Why Save is disabled, or None."""
        return self._blocker

    @Slot()
    def _edited(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        """Problems, buttons, monitor rows, the drawing: all that follows the form."""
        edited = self.edited()
        loaded = edited is not None and self._base is not None
        for widget in (self._playlists, self._general):
            widget.setEnabled(loaded)
        self._show_locations()
        if edited is None or self._base is None:
            self.problems = {}
            self._blocker = "Nothing loaded"
            self._show_buttons(False)
            self.entries = []
            self.monitors_view.show_overview(Overview(monitors=self._listed))
            self._drawn = frozenset()
            return
        self.entries = monitor_entries(self._listed, edited)
        self._show_monitor(edited)
        self.problems = problems(edited, self._base, paths.data_dir())
        for playlist in edited.playlists:
            editor = self.editors[playlist.id]
            mine = {
                key: m
                for (owner, key), m in self.problems.items()
                if owner == playlist.id
            }
            editor.show_problems(mine)
            editor.set_summary(summary_text(edited, playlist, self.entries), bool(mine))
            on = shown_on(edited, playlist.id, self.entries)
            editor.set_shown_on(
                " + ".join(on) or "No monitor: it doesn't rotate and keeps its history",
                muted=not on,
            )
        for key, problem in self.general_problems.items():
            problem.set_problem(self.problems.get((None, key)))
        dirty = self.dirty()
        self._blocker = self._check(edited, dirty)
        self._show_buttons(dirty)
        if dirty and self._blocker is None:
            settings = edited.settings()
            listed = {m.id for m in self._listed}
            names = entry_names(self.entries)
            changed = change_summary(self._base, settings, names, listed)
            self.status.setText(f"Changes: {changed}. Not saved yet.")
        self._draw(self._base, edited)

    def _draw(self, base: Settings, edited: Draft) -> None:
        """The drawing as edited: each monitor's playlist, image and look."""
        current = current_images(base, edited, self._shown)
        self.monitors_view.show_overview(drawing(edited, self._listed, current))
        self._drawn = frozenset(current.values())
        for image in current.values():
            self._give_image(image)
        self.monitors_view.set_selected(self.selected)

    @Slot(object)
    def _image_loaded(self, path: Path) -> None:
        if path in self._drawn:  # not one only Home shows
            self._give_image(path)

    def _give_image(self, path: Path) -> None:
        thumbnail = self.loader.get(path)
        if thumbnail is not None:
            self.monitors_view.set_image(path, thumbnail.small)

    def _check(self, edited: Draft, dirty: bool) -> str | None:
        """Why Save is off, or None."""
        if self._saving is not None:
            return "Saving…"
        if not dirty:
            return "Nothing changed"
        if self.problems:
            return problem_summary(self.problems, edited)
        try:
            check_settings(edited.settings())
        except SettingsError as err:
            return f"Can't save: {err}"
        return None

    def _show_buttons(self, dirty: bool) -> None:
        if dirty != self._modified:
            self._modified = dirty
            self.modified.emit(dirty)
        self.revert_button.setEnabled(dirty and self._saving is None)
        blocker = self._blocker
        self.save_button.setEnabled(blocker is None)
        self.save_button.setToolTip(
            blocker or "Save the changes; the app uses them at once"
        )
        if self._saving is not None:
            self.status.setText("Saving…")
        elif dirty:
            self.status.setText(blocker or "")
        else:
            loaded = self._base is not None
            self.status.setText("No unsaved changes." if loaded else "")

    def _show_locations(self) -> None:
        self.about.set_locations(data_locations(self._base))

    def _set_banner(
        self, text: str | None, kind: BannerKind | None = None, *, button: bool = False
    ) -> None:
        self._banner_kind = kind if text else None
        self.banner.setText(text or "")
        self.banner.setVisible(bool(text))
        self.banner_button.setVisible(bool(text) and button)

    # -- the selected monitor ----------------------------------------------------

    def _show_monitor(self, edited: Draft) -> None:
        """The Monitor, Playlist and Mode rows, for the selected monitor."""
        ids = [e.id for e in self.entries]
        if self.selected not in ids:
            self.selected = ids[0] if ids else None
        for row in (self.monitor_row, self.playlist_row, self.mode_row):
            row.setVisible(self.selected is not None)
        boxes = (self.monitor_combo, self.playlist_combo, self.mode_combo)
        for box in boxes:
            box.blockSignals(True)
        self.monitor_combo.clear()
        for entry in self.entries:
            self.monitor_combo.addItem(entry.label)
        self.playlist_combo.clear()
        self.playlist_combo.addItem("None", NO_PLAYLIST)
        for playlist in edited.playlists:
            self.playlist_combo.addItem(
                playlist.name.strip() or "(no name)", playlist.id
            )
        self.playlist_combo.addItem("New playlist…", NEW)
        for box in boxes:
            box.blockSignals(False)
        if self.selected is None:
            self.away_row.setVisible(False)
            return
        entry = self.entries[ids.index(self.selected)]
        self.monitor_combo.blockSignals(True)
        self.monitor_combo.setCurrentIndex(ids.index(self.selected))
        self.monitor_combo.blockSignals(False)
        mine = edited.monitor(self.selected)
        select(self.playlist_combo, mine.playlist or NO_PLAYLIST)
        self.playlist_row.set_description(
            playlist_help(edited, self.selected, self.entries)
        )
        select(self.mode_combo, mine.look.mode)
        self.mode_row.set_description(MODE_HELP[mine.look.mode])
        away = entry.state != "drawn"
        self.away_row.setVisible(away)
        if away:
            state = "Switched off" if entry.state == "off" else "Not connected"
            self.away_row.label.setText(state)
            self.forget_button.setVisible(edited.has_row(self.selected))

    @Slot(object)
    def select_monitor(self, monitor_id: MonitorId) -> None:
        """Set up this monitor (a click on the drawing, or the drop-down)."""
        self.selected = monitor_id
        self._refresh()

    @Slot(int)
    def _monitor_chosen(self, index: int) -> None:
        if 0 <= index < len(self.entries):
            self.select_monitor(self.entries[index].id)

    @Slot(int)
    def _playlist_chosen(self, index: int) -> None:
        del index
        edited = self.edited()
        if edited is None or self.selected is None:
            return
        chosen = self.playlist_combo.currentData()
        if chosen == NEW:
            playlist = edited.new_playlist()
            self._set_row(edited, playlist=playlist.id)
            self._start_playlist(PlaylistEditor(playlist, self))
            return
        self._set_row(edited, playlist=chosen or None)
        self._refresh()

    def _set_row(self, edited: Draft, **update: object) -> None:
        """Change the selected monitor's row."""
        assert self.selected is not None
        row = edited.monitor(self.selected)
        self._monitors = edited.with_monitor(row.model_copy(update=update)).monitors

    @Slot(int)
    def _mode_chosen(self, index: int) -> None:
        del index
        edited = self.edited()
        if edited is None or self.selected is None:
            return
        mode: ImageMode = self.mode_combo.currentData()
        look = edited.monitor(self.selected).look
        self._set_row(edited, look=mode_look(look, mode))
        self._refresh()

    @Slot()
    def _forget(self) -> None:
        edited = self.edited()
        if edited is not None and self.selected is not None:
            self._monitors = edited.without_monitor(self.selected).monitors
            self.selected = None  # the first one, as it is no longer listed
            self._refresh()

    # -- saving ------------------------------------------------------------------

    @Slot()
    def _save_clicked(self) -> None:
        self.save()

    def save(self) -> bool:
        """Send the changes to the app; False if they can't be saved now."""
        edited = self.edited()
        if edited is None or self._blocker is not None:
            return False
        settings = edited.settings()
        self._saving = settings
        request = SaveSettingsRequest(settings=settings, revision=self._revision)
        self._client.run(request, _ReplyTo(self, SettingsPage.saved))
        self._refresh()
        return True

    def saved(self, reply: Reply) -> None:
        """The app's answer to a save."""
        sent, self._saving = self._saving, None
        close, self._close_when_saved = self._close_when_saved, False
        if isinstance(reply, SaveSettingsResult) and sent is not None:
            self._use(sent, reply.revision)
            warnings = "\n".join(f"Warning: {w}" for w in reply.warnings)
            self._set_banner(warnings or None, "saved")
            self._refresh()
            if close:
                self.window().close()
            return
        if isinstance(reply, ErrorReply):
            if reply.error.kind == "stale_settings":
                self._set_banner(
                    "The settings changed since they were read here, so nothing "
                    "was saved. Load them again to see them; your changes here "
                    "are then lost.",
                    "stale",
                    button=True,
                )
            else:
                self._set_banner(f"Not saved: {reply.error.message}", "saved")
        self._refresh()

    @Slot()
    def revert(self) -> None:
        if self._base is not None:
            self._fill(self._base_draft)
            self._refresh()

    def confirm_close(self) -> bool:
        """
        The window is closing: with unsaved changes, ask Save / Discard /
        Cancel. False keeps the window open: Cancel, a Save that can't
        happen (the status line says why), or a Save sent to the app (the
        window closes once it has saved).
        """
        if not self.dirty():
            return True
        choice = self._dialogs.unsaved(self)
        if choice == "cancel":
            return False
        if choice == "save":
            if self.save():
                self._close_when_saved = True
            elif self._blocker is not None:
                self.status.setText(self._blocker)
            return False
        return True

    # -- playlists ---------------------------------------------------------------

    @Slot()
    def _add_playlist(self) -> None:
        edited = self.edited()
        if edited is not None:
            self._start_playlist(PlaylistEditor(edited.new_playlist(), self))

    def _start_playlist(self, editor: PlaylistEditor) -> None:
        self._add_editor(editor)
        self.open_playlist(editor.playlist_id)
        self._refresh()
        self.tabs.set_current_index(0)
        editor.name_edit.setFocus()
        editor.name_edit.selectAll()

    @Slot(str)
    def _delete_playlist(self, playlist_id: str) -> None:
        edited = self.edited()
        editor = self.editors.pop(playlist_id, None)
        if editor is None or edited is None:
            return
        self._editors_box.removeWidget(editor)
        editor.hide()
        editor.deleteLater()
        self._monitors = edited.without_playlist(playlist_id).monitors
        if playlist_id == self.open_id:
            self.open_id = None
        self._refresh()

    @Slot(str)
    def _toggle_playlist(self, playlist_id: str) -> None:
        self.open_playlist(None if playlist_id == self.open_id else playlist_id)
        self._refresh()

    def open_playlist(self, playlist_id: str | None) -> None:
        """Show this playlist open (None: none), the others as their summary."""
        self.open_id = playlist_id
        for editor_id, editor in self.editors.items():
            editor.set_expanded(editor_id == playlist_id)

    @Slot(str)
    def _browse_folder(self, playlist_id: str) -> None:
        editor = self.editors.get(playlist_id)
        if editor is None:
            return
        chosen = self._dialogs.folder(self, editor.directory_edit.text().strip())
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
