"""
The painted controls (app/ui/controls.py, docs/design/controls.dc.html),
offscreen: what they paint at rest, on, pressed, disabled and with the
keyboard focus, in the tones of the application's palette; what they do
with the mouse and the keys; their accessible names; and that they paint
at a device pixel ratio of 2.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPalette,
    QPixmap,
)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget
from pytestqt.qtbot import QtBot

from wallpaper_py.app.ui import theme
from wallpaper_py.app.ui.controls import (
    CHIP_LABEL,
    SEGMENT_PADDING,
    Button,
    FilterChip,
    FieldCombo,
    Segments,
    Slider,
    Switch,
    knob_off,
    switch_on,
)

from .test_theme import DARK, LIGHT


@pytest.fixture(name="dark", autouse=True)
def fixture_dark() -> Iterator[theme.Tones]:
    """The system in dark mode (as the mockup); restored after."""
    original = QPalette(QApplication.palette())
    QApplication.setPalette(DARK)
    yield theme.tones()
    QApplication.setPalette(original)


@pytest.fixture(name="host")
def fixture_service_thread(qtbot: QtBot) -> Callable[[QWidget], QWidget]:
    """Puts a control in a shown, active window (for focus and the pointer)."""
    windows: list[QWidget] = []  # kept alive until the test ends

    def place(control: QWidget) -> QWidget:
        window = QWidget()
        windows.append(window)
        qtbot.addWidget(window)
        layout = QHBoxLayout(window)
        layout.addWidget(control)
        window.resize(400, 80)
        window.show()
        window.activateWindow()
        qtbot.waitExposed(window)
        qtbot.waitUntil(window.isActiveWindow)
        return window

    return place


def shot(widget: QWidget) -> QImage:
    return widget.grab().toImage()


def same(a: QColor, b: QColor, tolerance: int = 3) -> bool:
    """The same colour, give or take antialiasing's rounding."""
    return all(
        abs(x - y) <= tolerance
        for x, y in zip((a.red(), a.green(), a.blue()), (b.red(), b.green(), b.blue()))
    )


def tab_into(widget: QWidget) -> None:
    """The focus comes with Tab (not when the window opened, as at first)."""
    widget.clearFocus()
    widget.setFocus(Qt.FocusReason.TabFocusReason)
    assert widget.hasFocus()


def at_ratio_2(widget: QWidget) -> QImage:
    """The widget painted at a device pixel ratio of 2."""
    pixmap = QPixmap(widget.size() * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(theme.tones().page)
    painter = QPainter(pixmap)
    widget.render(painter, QPoint())
    painter.end()
    return pixmap.toImage()


# -- tones -----------------------------------------------------------------------


def test_the_controls_tones_are_the_mockups(dark: theme.Tones) -> None:
    """docs/design/controls.dc.html's dark tones, from Windows 11's palette."""
    assert same(dark.outline, QColor("#383838"), 1)  # a segment's edge
    assert same(dark.track, QColor("#484848"), 1)
    assert same(dark.switch_off, QColor("#444444"), 1)
    assert dark.field_hover.lightness() > dark.field.lightness()
    assert dark.popup.lightness() > dark.page.lightness()
    assert dark.accent_hover.lightness() > dark.accent.lightness()
    assert dark.accent_down.lightness() < dark.accent.lightness()
    assert dark.disabled.alphaF() == pytest.approx(0.36, abs=0.01)
    light = theme.derive_tones(LIGHT)
    assert light.popup == LIGHT.color(QPalette.ColorRole.Base)
    assert light.accent_hover.lightness() < light.accent.lightness()


def test_the_accent_comes_from_the_platforms_role() -> None:
    """KDE's accent is only in Highlight; Windows' is in Accent."""
    palette = QPalette(DARK)
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        palette.setColor(group, QPalette.ColorRole.Accent, QColor("#c42b1c"))  # red
        palette.setColor(group, QPalette.ColorRole.Highlight, QColor("#9c27b0"))
    accent = theme.derive_tones(palette).accent
    purple = theme.ACCENT_ROLE == QPalette.ColorRole.Highlight
    assert accent.hue() == QColor("#9c27b0" if purple else "#c42b1c").hue()


# -- the focus ring --------------------------------------------------------------


def test_the_focus_ring_is_2_px_and_only_from_the_keyboard(
    host: Callable[[QWidget], QWidget], dark: theme.Tones
) -> None:
    button = Button("Revert")
    host(button)
    middle = button.height() // 2
    button.clearFocus()
    button.setFocus(Qt.FocusReason.MouseFocusReason)
    assert same(shot(button).pixelColor(0, middle), dark.field)  # no ring
    tab_into(button)
    image = shot(button)
    assert same(image.pixelColor(0, middle), dark.accent)
    assert same(image.pixelColor(1, middle), dark.accent)
    assert not same(image.pixelColor(2, middle), dark.accent)  # the gap
    assert same(image.pixelColor(4, middle), dark.field)  # the fill inside
    # The window activated again keeps it; a click takes it away.
    button.clearFocus()
    button.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
    assert same(shot(button).pixelColor(0, middle), dark.accent)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert same(shot(button).pixelColor(0, middle), dark.field)


def test_text_buttons_and_icon_buttons_get_the_ring(
    host: Callable[[QWidget], QWidget], dark: theme.Tones
) -> None:
    for button in (theme.TextButton("Browse…"), theme.IconButton("star", "Fav")):
        host(button)
        middle = button.height() // 2
        assert not same(shot(button).pixelColor(1, middle), dark.accent)
        tab_into(button)
        assert same(shot(button).pixelColor(1, middle), dark.accent)


# -- drop-down -------------------------------------------------------------------------


def combo_with(*texts: str) -> FieldCombo:
    combo = FieldCombo()
    combo.setAccessibleName("Background")
    for text in texts:
        combo.addItem(text, text.lower())
    return combo


def test_drop_down_paints_a_filled_box_and_a_solid_arrow(
    host: Callable[[QWidget], QWidget], dark: theme.Tones
) -> None:
    combo = combo_with("Colour", "Blurred image")
    host(combo)
    combo.setFixedWidth(240)
    image = shot(combo)
    middle = combo.height() // 2
    assert same(image.pixelColor(2, middle), dark.field)  # no border
    # The ▾: 10 x 6, 12 px from the right; its top row is wider than its tip.
    arrow_top = (combo.height() - 6) // 2
    assert same(image.pixelColor(combo.width() - 17, arrow_top + 1), dark.muted)
    assert same(image.pixelColor(combo.width() - 21, arrow_top + 5), dark.field)
    combo.setEnabled(False)
    assert not same(
        shot(combo).pixelColor(combo.width() - 17, arrow_top + 1), dark.muted
    )


def test_drop_down_list_opens_under_it_and_chooses(
    host: Callable[[QWidget], QWidget], dark: theme.Tones
) -> None:
    combo = combo_with("Colour", "Average", "Blurred image")
    host(combo)
    chosen: list[int] = []
    combo.currentIndexChanged.connect(chosen.append)
    entries = combo.entry_list

    QTest.mouseClick(combo, Qt.MouseButton.LeftButton)
    assert entries.isVisible() and combo.is_open()
    below = combo.mapToGlobal(QPoint(0, combo.height()))
    assert entries.geometry().top() > below.y()
    assert entries.width() >= combo.width()
    assert entries.accessibleName() == "Background"
    # The current entry has the sidebar's mark: the accent bar, at its left.
    image = shot(entries)
    first = entries.entry_rect(0)
    assert same(
        image.pixelColor(round(first.left()) + 1, round(first.center().y())),
        dark.accent,
    )
    # A click on an entry chooses it and closes the list.
    third = entries.entry_rect(2).center().toPoint()
    QTest.mouseClick(
        entries, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, third
    )
    assert chosen == [2] and combo.currentText() == "Blurred image"
    assert not entries.isVisible() and not combo.is_open()


def test_drop_down_by_keyboard(host: Callable[[QWidget], QWidget]) -> None:
    combo = combo_with("Colour", "Average", "Blurred image")
    host(combo)
    chosen: list[int] = []
    combo.currentIndexChanged.connect(chosen.append)
    entries = combo.entry_list
    tab_into(combo)
    QTest.keyClick(combo, Qt.Key.Key_Down)  # closed: the arrows change it
    assert chosen == [1]
    QTest.keyClick(combo, Qt.Key.Key_Space)
    assert entries.isVisible() and entries.pointed == 1
    QTest.keyClick(entries, Qt.Key.Key_Down)
    QTest.keyClick(entries, Qt.Key.Key_Down)  # stays on the last
    assert entries.pointed == 2
    QTest.keyClick(entries, Qt.Key.Key_Escape)  # closes, nothing chosen
    assert not entries.isVisible() and chosen == [1]
    QTest.keyClick(combo, Qt.Key.Key_F4)
    QTest.keyClick(entries, Qt.Key.Key_Home)
    QTest.keyClick(entries, Qt.Key.Key_Return)
    assert chosen == [1, 0] and not combo.is_open()


def test_an_empty_drop_down_doesnt_open(host: Callable[[QWidget], QWidget]) -> None:
    combo = FieldCombo()
    host(combo)
    QTest.mouseClick(combo, Qt.MouseButton.LeftButton)
    assert not combo.entry_list.isVisible()


# -- segmented choice ------------------------------------------------------------


def modes() -> Segments:
    choice = Segments("Mode")
    for text, value in (("Fill", 1), ("Fit", 2), ("Stretch", 3)):
        choice.add(text, value, f"mode-{text.lower()}", f"{text} help")
    return choice


def test_segments_choose_one(host: Callable[[QWidget], QWidget]) -> None:
    choice = modes()
    host(choice)
    assert choice.accessibleName() == "Mode"
    assert choice.button(1).accessibleName() == "Fit"
    assert choice.button(1).toolTip() == "Fit help"
    assert (choice.current_index(), choice.current_value()) == (0, 1)
    changed: list[int] = []
    choice.current_changed.connect(changed.append)
    QTest.mouseClick(choice.button(1), Qt.MouseButton.LeftButton)
    QTest.mouseClick(choice.button(1), Qt.MouseButton.LeftButton)  # no change
    assert changed == [1] and choice.current_text() == "Fit"
    assert [choice.button(i).isChecked() for i in range(3)] == [False, True, False]
    choice.select(3)  # as loaded: not reported
    assert changed == [1] and choice.current_index() == 2
    choice.select("unknown")  # the first
    assert choice.current_index() == 0
    assert choice.find_value(2) == 1 and choice.find_value(9) == -1
    # The arrow keys move to the next choice and choose it.
    tab_into(choice.button(0))
    QTest.keyClick(choice.button(0), Qt.Key.Key_Right)
    assert changed == [1, 1] and choice.button(1).hasFocus()


def test_the_chosen_segment_is_outlined_in_the_accent(
    host: Callable[[QWidget], QWidget], dark: theme.Tones
) -> None:
    choice = modes()
    host(choice)
    chosen, other = choice.button(0), choice.button(1)
    middle = chosen.height() // 2
    assert same(shot(chosen).pixelColor(0, middle), dark.accent)
    assert same(shot(other).pixelColor(0, middle), dark.outline)
    # Each as wide as its bold label: choosing doesn't move the others.
    width = other.width()
    choice.set_current_index(1)
    assert other.width() == width
    assert same(shot(other).pixelColor(0, middle), dark.accent)


# -- switch ----------------------------------------------------------------------


def test_switch(host: Callable[[QWidget], QWidget], dark: theme.Tones) -> None:
    switch = Switch("Pause while the session is locked")
    host(switch)
    assert switch.accessibleName() == "Pause while the session is locked"
    toggled: list[bool] = []
    switch.toggled.connect(toggled.append)
    track = switch.track()
    middle = round(track.center().y())
    left, right = QPoint(round(track.left()) + 3, middle), QPoint(
        round(track.right()) - 3, middle
    )

    def knob() -> QPoint:
        return switch.knob().center().toPoint()

    assert switch.knob().height() > track.height()  # the knob stands out
    image = shot(switch)
    assert same(image.pixelColor(knob()), knob_off(dark))  # off: at the left
    assert same(image.pixelColor(right), dark.switch_off)
    QTest.mouseClick(switch, Qt.MouseButton.LeftButton)
    assert toggled == [True]
    image = shot(switch)
    assert switch.knob().center().x() > track.center().x()  # on: at the right
    assert same(image.pixelColor(knob()), dark.accent)
    assert same(image.pixelColor(left), switch_on(dark))  # the accent, dimmed
    tab_into(switch)
    QTest.keyClick(switch, Qt.Key.Key_Space)
    assert toggled == [True, False]
    ring = QPoint(1, middle)  # 2 px off the knob's path
    assert same(shot(switch).pixelColor(ring), dark.accent)
    switch.setChecked(True)
    switch.setEnabled(False)
    assert not same(shot(switch).pixelColor(knob()), dark.accent)  # faded


# -- chip ------------------------------------------------------------------------


def test_chip(host: Callable[[QWidget], QWidget], dark: theme.Tones) -> None:
    chip = FilterChip("Hide removed")
    host(chip)
    assert chip.accessibleName() == "Hide removed"
    toggled: list[bool] = []
    chip.toggled.connect(toggled.append)
    middle = chip.height() // 2
    assert same(shot(chip).pixelColor(0, middle), dark.outline)  # edge
    width = chip.width()
    QTest.mouseClick(chip, Qt.MouseButton.LeftButton)
    assert toggled == [True]
    assert chip.width() > width  # room for the ✓
    image = shot(chip)
    xs = range(SEGMENT_PADDING, SEGMENT_PADDING + 16)  # the ✓, in the accent
    ys = range(middle - 8, middle + 8)
    assert any(same(image.pixelColor(x, y), dark.accent, 40) for x in xs for y in ys)
    assert same(image.pixelColor(0, middle), dark.accent)  # as a chosen segment
    tab_into(chip)
    QTest.keyClick(chip, Qt.Key.Key_Space)
    assert toggled == [True, False]
    assert chip.width() == width
    assert same(shot(chip).pixelColor(chip.width() // 2, 1), dark.accent)  # ring
    chip.set_label("Only a long playlist")  # fits: no tooltip
    assert chip.accessibleName() == "Only a long playlist"
    assert chip.width() > width and not chip.toolTip()
    long = "Only " + "a very long playlist name " * 4
    chip.set_label(long)  # elided: the whole of it in the tooltip
    assert chip.label().endswith("…") and chip.toolTip() == long
    most = CHIP_LABEL * QFontMetrics(chip.label_font()).averageCharWidth()
    assert chip.width() <= most + 2 * SEGMENT_PADDING
    font = QFont(chip.font())
    font.setPointSizeF(font.pointSizeF() * 2)
    chip.set_label("Hide removed")
    width = chip.width()
    chip.setFont(font)  # a font change: measured again
    assert chip.width() > width


# -- buttons ---------------------------------------------------------------------


def test_buttons(host: Callable[[QWidget], QWidget], dark: theme.Tones) -> None:
    save, revert = Button("Save", primary=True), Button("Revert")
    host(save)
    host(revert)
    assert save.accessibleName() == "Save"
    assert save.fill() == dark.accent and revert.fill() == dark.field
    middle = save.height() // 2
    assert same(shot(save).pixelColor(2, middle), dark.accent)
    save.setDown(True)
    assert save.fill() == dark.accent_down
    save.setDown(False)
    save.setEnabled(False)  # disabled: the field tone, dim text
    assert save.fill() == dark.field


def test_the_main_button_follows_the_system(
    host: Callable[[QWidget], QWidget],
) -> None:
    save = Button("Save", primary=True)
    host(save)
    QApplication.setPalette(LIGHT)
    light = theme.tones()
    assert not light.dark and save.fill() == light.accent
    assert same(shot(save).pixelColor(2, save.height() // 2), light.accent)


# -- slider ----------------------------------------------------------------------


def blur() -> Slider:
    slider = Slider("Blur")
    slider.setRange(0, 10)
    slider.setPageStep(2)
    slider.setFixedWidth(200)
    return slider


def test_slider_follows_a_click_and_a_drag(host: Callable[[QWidget], QWidget]) -> None:
    slider = blur()
    host(slider)
    assert slider.accessibleName() == "Blur"
    moved: list[int] = []
    slider.valueChanged.connect(moved.append)
    span = slider.width() - 2 * Slider.ROOM
    middle = slider.height() // 2

    def x_of(value: int) -> int:
        return Slider.ROOM + round(span * value / 10)

    QTest.mouseClick(
        slider,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(x_of(7), middle),
    )
    assert slider.value() == 7 and slider.knob_x() == pytest.approx(x_of(7), abs=1)
    QTest.mousePress(
        slider,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(x_of(2), middle),
    )
    assert slider.isSliderDown()
    QTest.mouseMove(slider, QPoint(x_of(4), middle))
    QTest.mouseRelease(
        slider,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(x_of(4), middle),
    )
    assert moved == [7, 2, 4] and not slider.isSliderDown()
    # Past the ends: the ends.
    QTest.mouseClick(
        slider,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPoint(0, middle),
    )
    assert slider.value() == 0 and slider.knob_x() == Slider.ROOM


def test_slider_keys_and_paint(
    host: Callable[[QWidget], QWidget], dark: theme.Tones
) -> None:
    slider = blur()
    host(slider)
    slider.setValue(5)
    tab_into(slider)
    QTest.keyClick(slider, Qt.Key.Key_Right)
    QTest.keyClick(slider, Qt.Key.Key_PageUp)
    assert slider.value() == 8
    QTest.keyClick(slider, Qt.Key.Key_Home)
    assert slider.value() == 0
    slider.setValue(5)
    image = shot(slider)
    middle = slider.height() // 2
    knob = round(slider.knob_x())
    assert same(image.pixelColor(knob, middle), dark.accent)  # the knob
    assert same(image.pixelColor(knob - 30, middle), dark.accent)  # filled up to it
    assert same(image.pixelColor(knob + 30, middle), dark.track)  # the rest
    # The focus: an accent ring around the knob, the page's tone between.
    assert same(image.pixelColor(knob, middle - 11), dark.accent)
    slider.setEnabled(False)
    image = shot(slider)
    assert same(image.pixelColor(knob, middle), dark.knob_disabled)


# -- all of them -----------------------------------------------------------------


def test_every_control_paints_at_a_ratio_of_2(
    host: Callable[[QWidget], QWidget], dark: theme.Tones
) -> None:
    combo = combo_with("Colour")
    on = Switch("On")
    on.setChecked(True)
    slider = blur()
    chip = FilterChip("Hide removed")
    chip.setChecked(True)
    controls = (combo, modes(), on, Button("Save", primary=True), slider, chip)
    for control in controls:
        host(control)
        image = at_ratio_2(control)
        assert image.size() == control.size() * 2
        # The whole image: on Windows, text may come without antialiasing,
        # so one row of a button can hold only its fill and its text.
        colours = {
            image.pixelColor(x, y).name()
            for x in range(image.width())
            for y in range(image.height())
        }
        assert len(colours) > 2, type(control).__name__
    # The chosen segment's icon, tinted in the accent at 2x.
    choice = modes()
    host(choice)
    image = at_ratio_2(choice.button(0))
    icon = range(2 * 12, 2 * (12 + 16))  # after the padding, 16 px square
    assert any(same(image.pixelColor(x, y), dark.accent) for x in icon for y in icon)
