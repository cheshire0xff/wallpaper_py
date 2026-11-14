"""
The Settings page's rows for one monitor's look (core.model.Look), under
its Monitor and Playlist rows: *Mode*; for Fit, *Background* (a colour,
with a swatch that opens a colour dialog, or one from the image; or the
image itself, blurred); for the Blurred image, *Blur* and *Darken* sliders.

`LookEditor` shows the look of the monitor the page selected, as edited
(`show_look`), and reports each change as that monitor's new look
(`edited`): the page puts it in the draft and shows it back. Changes come
while the colour dialog moves and while a slider is dragged, so the
preview follows them.
"""

from __future__ import annotations

from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QVBoxLayout, QWidget

from ...core.model import Background, Colour, ImageMode, Look, MonitorId
from .settings_dialogs import Dialogs
from .settings_draft import (
    BACKGROUND_HELP,
    BACKGROUND_NAMES,
    BLUR_STEPS,
    COLOUR,
    DARKEN_STEPS,
    MODE_HELP,
    MODE_NAMES,
    BackgroundKind,
    background_kind,
    background_look,
    blur_look,
    blur_step,
    colour_of,
    darken_look,
    darken_step,
    mode_look,
)
from .settings_form import (
    ColourButton,
    SettingRow,
    combo,
    control_row,
    select,
    set_step,
    slider,
    slider_row,
)


class LookEditor(QWidget):
    """
    See the module docstring. `edited(MonitorId, Look)`: the monitor shown
    gets this look.
    """

    # pylint: disable=too-many-instance-attributes  # its widgets

    edited = Signal(object, object)

    def __init__(self, dialogs: Dialogs, parent: QWidget, *, indent: int) -> None:
        super().__init__(parent)
        self._dialogs = dialogs
        self._monitor: MonitorId | None = None  # the one shown
        self._look = Look()  # its look, as edited
        self._saved_colour: Colour = colour_of(Look())  # its saved fixed colour
        # The fixed colours left for another background in this edit, to go
        # back to; and the monitor the colour dialog is open for.
        self._colours: dict[MonitorId, Colour] = {}
        self._colour_for: MonitorId | None = None

        self.mode_combo = combo(self, "Mode")
        for mode, name in MODE_NAMES.items():
            self.mode_combo.addItem(name, mode)
        self.mode_row = SettingRow(
            "Mode", "", self.mode_combo, parent=self, indent=indent
        )
        self.background_combo = combo(self, "Background")
        for kind, name in BACKGROUND_NAMES.items():
            self.background_combo.addItem(name, kind)
        self.colour_button = ColourButton("Background colour", self)
        self.background_row = SettingRow(
            "Background",
            "",
            control_row(self.background_combo, self.colour_button),
            parent=self,
            indent=indent,
        )
        self.blur_slider = slider(self, "Blur", BLUR_STEPS)
        self.blur_row = SettingRow(
            "Blur",
            "How soft the image in the bars is",
            slider_row(self.blur_slider, "Less", "More"),
            parent=self,
            indent=indent,
        )
        self.darken_slider = slider(self, "Darken", DARKEN_STEPS)
        self.darken_row = SettingRow(
            "Darken",
            "Darker bars make the image stand out",
            slider_row(self.darken_slider, "None", "More"),
            parent=self,
            indent=indent,
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        for row in (self.mode_row, self.background_row, self.blur_row, self.darken_row):
            layout.addWidget(row)

        self.mode_combo.currentIndexChanged.connect(self._mode_chosen)
        self.background_combo.currentIndexChanged.connect(self._background_chosen)
        self.colour_button.clicked.connect(self._choose_colour)
        self.blur_slider.valueChanged.connect(self._blur_moved)
        self.darken_slider.valueChanged.connect(self._darken_moved)

    def show_look(self, monitor_id: MonitorId, look: Look, saved: Look) -> None:
        """The monitor's look as edited (`saved`: as saved), without reporting it."""
        self._monitor, self._look = monitor_id, look
        self._saved_colour = colour_of(saved)
        select(self.mode_combo, look.mode)
        self.mode_row.set_description(MODE_HELP[look.mode])
        fit = look.mode is ImageMode.FIT
        self.background_row.setVisible(fit)
        kind = background_kind(look.background)
        select(self.background_combo, kind)
        self.background_row.set_description(BACKGROUND_HELP[kind])
        self.colour_button.setVisible(kind == COLOUR)
        self.colour_button.set_colour(colour_of(look))
        blurred = fit and kind is Background.BLUR
        self.blur_row.setVisible(blurred)
        self.darken_row.setVisible(blurred)
        set_step(self.blur_slider, blur_step(look))
        set_step(self.darken_slider, darken_step(look))

    def forget_colours(self) -> None:
        """A new edit (the settings loaded again): no colours to go back to."""
        self._colours = {}

    def _edit(self, look: Look) -> None:
        if self._monitor is not None:
            self.edited.emit(self._monitor, look)

    @Slot(int)
    def _mode_chosen(self, index: int) -> None:
        del index
        mode: ImageMode = self.mode_combo.currentData()
        self._edit(mode_look(self._look, mode))

    @Slot(int)
    def _background_chosen(self, index: int) -> None:
        del index
        if self._monitor is None:
            return
        kind: BackgroundKind = self.background_combo.currentData()
        look = self._look
        if not isinstance(look.background, Background):
            self._colours[self._monitor] = look.background
        background: Background | Colour
        if isinstance(kind, Background):
            background = kind
        else:  # a fixed colour: the last one in this edit, else the saved one
            background = self._colours.get(self._monitor, self._saved_colour)
        self._edit(background_look(look, background))

    @Slot(int)
    def _blur_moved(self, step: int) -> None:
        """Also while dragged: the preview follows."""
        self._edit(blur_look(self._look, step))

    @Slot(int)
    def _darken_moved(self, step: int) -> None:
        """Also while dragged: the preview follows."""
        self._edit(darken_look(self._look, step))

    @Slot()
    def _choose_colour(self) -> None:
        """The colour dialog: the preview follows it, Cancel goes back."""
        if self._monitor is None:
            return
        before = QColor(self.colour_button.colour())
        dialog = QColorDialog(before, self)
        dialog.setWindowTitle("Background colour")
        dialog.currentColorChanged.connect(self._colour_previewed)
        # Its event loop may show another monitor, which keeps its colour.
        self._colour_for = self._monitor
        try:
            chosen = self._dialogs.colour(dialog)
            colour = dialog.currentColor() if chosen else before
            self._colour_previewed(colour)
        finally:
            self._colour_for = None
            dialog.currentColorChanged.disconnect(self._colour_previewed)
            dialog.deleteLater()

    @Slot(QColor)
    def _colour_previewed(self, colour: QColor) -> None:
        """The background colour of the monitor the dialog is open for."""
        if self._colour_for is None or not colour.isValid():
            return
        if self._colour_for == self._monitor:  # else another one is shown now
            self._edit(background_look(self._look, colour.name()))
