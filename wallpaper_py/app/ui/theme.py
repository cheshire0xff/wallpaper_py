"""
The look every page shares (docs/design/README.md, "The look"; the
mockups in docs/design/): the system's palette, a few tones derived from
it, constants and small painted widgets. Not a framework. The controls
(drop-down, segmented choice, switch, buttons, slider) are controls.py's.

- **Tones** (`derive_tones`, `tones`): derived from the application's
  `QPalette`, never hard-coded per OS. In dark mode the page is a shade
  darker than the palette's Window colour; the sidebar is the same colour,
  set apart only by a thin lighter `line`; image panels are a little
  lighter than the page. Light mode keeps the palette's Window colour; the
  panel behind images is a silver grey a step darker than the page, with
  the palette's (dark) text on it (wallpapers look washed out on white;
  the old near-black panel was too stark; chosen from the mockup). The
  accent is the palette's Accent on Windows and its Highlight elsewhere
  (`ACCENT_ROLE`): Qt puts KDE's accent only in Highlight.
  `window_palette` puts the tones into the palette roles the native
  style draws with (Window, Base and Button for the borderless filled
  fields, PlaceholderText for muted text); the main window sets it, and
  everything painted here reads `tones()` when it paints, so a change of
  the system's light / dark mode re-derives everything.
- Spacing in 8 px steps (`space`), radii, the control column, field heights.
- Icons: bundled Lucide SVGs (`icons/`, ISC licence kept with them), tinted
  when drawn (`tinted_pixmap`, `icon`); a toggle that is on is filled.
- Widgets: `ValueLabel` (a read-only value lined up with the fields),
  `IconButton` (borderless; on: accent icon on a faint accent fill),
  `TextButton` (accent text, for secondary actions), `Tabs` (sub-tabs
  with an accent underline), `Divider` (the thin line), `Panel`
  (the lighter panel behind images), `Banner` (a page's message), `Chip`,
  `ImageView` (an image letterboxed on the panel tone).
- The keyboard focus: a 2 px accent ring (`draw_focus_ring`), if by key.

No stylesheet is set anywhere. The style draws only what is still native
(text fields, scroll bars, dialogs): Windows 11 style on Windows, Fusion on
Linux (PySide6's own Qt can't load the system's Breeze).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import override

from PySide6.QtCore import QByteArray, QEvent, QRectF, QSize, Qt, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QFocusEvent,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QImage,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOptionFrame,
    QStyleOptionToolButton,
    QStylePainter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

SPACING = 8  # px; everything is spaced in steps of this
RADIUS = 6  # panels, hovered rows
RADIUS_SMALL = 4  # buttons, fields, images
ICON_SIZE = 20
SMALL_ICON = 18  # in icon buttons
BODY_POINTS = 10.5  # Windows 11's body text (14 px); a smaller system font grows
FIELD_HEIGHT = 34
FIELD_PADDING = 4  # px before a borderless field's text (its text margin)
# QLineEdit's own space before its text, inside its text margins (Qt's
# QLineEditPrivate::horizontalMargin; the same since Qt 4).
LINE_EDIT_MARGIN = 2
CONTROL_WIDTH = 360  # the settings' control column
PAGE_MARGIN = 32  # left / right of a page's content
PAGE_TOP = 20
RING = 2  # px: the keyboard focus ring
RING_GAP = 1  # px between the ring and a filled control's fill

# Where the system's accent is: Qt reads KDE's colours but puts the accent
# only in Highlight (Accent stays Fusion's built-in blue); Windows sets
# Accent.
ACCENT_ROLE = (
    QPalette.ColorRole.Accent
    if sys.platform == "win32"
    else QPalette.ColorRole.Highlight
)

# The status dot (the palette has no such roles): dark mode, light mode.
OK = (QColor("#6ccb5f"), QColor("#0f7b0f"))
ERROR = (QColor("#ff99a4"), QColor("#c42b1c"))

# The bundled icons: Lucide names (https://lucide.dev), files in icons/.
ICON_NAMES = (
    "arrow-down-a-z",
    "arrow-left",
    "check",
    "chevron-down",
    "chevron-left",
    "chevron-right",
    "circle-alert",
    "house",
    "images",
    "link",
    "pin",
    "search",
    "shuffle",
    "skip-back",
    "skip-forward",
    "sliders-horizontal",
    "star",
    "trash-2",
    # Ours, drawn like Lucide's: the modes, for their segmented choice.
    "mode-fill",
    "mode-fit",
    "mode-stretch",
)

_CHANGES = (
    QEvent.Type.PaletteChange,
    QEvent.Type.ApplicationPaletteChange,
    QEvent.Type.StyleChange,
)


def space(steps: float = 1) -> int:
    return round(SPACING * steps)


def palette_changed(event: QEvent) -> bool:
    """A palette (or the style) changed: what the tones come from."""
    return event.type() in _CHANGES


# -- tones -----------------------------------------------------------------------


def mix(a: QColor, b: QColor, amount: float) -> QColor:
    """`a` moved `amount` (0..1) of the way to `b`, opaque."""
    return QColor(
        round(a.red() + (b.red() - a.red()) * amount),
        round(a.green() + (b.green() - a.green()) * amount),
        round(a.blue() + (b.blue() - a.blue()) * amount),
    )


def with_alpha(color: QColor, alpha: float) -> QColor:
    result = QColor(color)
    result.setAlphaF(alpha)
    return result


def readable_accent(accent: QColor, dark: bool) -> QColor:
    """The accent, made light enough to read on dark, or dark enough on light."""
    color = QColor(accent)
    for _ in range(12):
        lightness = color.lightnessF()
        if (dark and lightness >= 0.6) or (not dark and lightness <= 0.45):
            break
        color = color.lighter(115) if dark else color.darker(115)
    return color


@dataclass(frozen=True)
class Tones:
    """The colours of the page, derived from a palette (`derive_tones`)."""

    # pylint: disable=too-many-instance-attributes
    dark: bool
    page: QColor  # the page and the sidebar
    line: QColor  # the sidebar's edge, under the title area, dividers
    text: QColor
    muted: QColor  # descriptions, details
    field: QColor  # borderless filled fields and plain buttons
    field_hover: QColor  # a field under the pointer
    outline: QColor  # the edge of a segmented choice
    hover: QColor  # a hovered row or item (translucent)
    disabled: QColor  # text and marks of a disabled control (translucent)
    accent: QColor  # readable on the page
    accent_hover: QColor  # a solid accent fill under the pointer
    accent_down: QColor  # a solid accent fill pressed
    accent_fill: QColor  # a faint accent fill (translucent)
    on_accent: QColor  # text on a solid accent fill
    popup: QColor  # an open drop-down's list
    track: QColor  # a slider's groove
    knob_disabled: QColor  # a disabled slider's knob
    switch_off: QColor  # a switch that is off
    switch_off_hover: QColor
    panel: QColor  # behind images: the monitor drawing, thumbnails
    panel_raised: QColor  # an empty image area on the panel
    panel_text: QColor
    panel_muted: QColor
    panel_accent: QColor  # readable on the panel
    panel_line: QColor  # a divider on the panel
    ok: QColor
    error: QColor


def derive_tones(palette: QPalette) -> Tones:
    """The tones for a palette: see the module docstring."""
    group = QPalette.ColorGroup.Active
    window = palette.color(group, QPalette.ColorRole.Window)
    text = palette.color(group, QPalette.ColorRole.WindowText)
    dark = window.lightnessF() < 0.5
    page = window.darker(122) if dark else QColor(window)
    accent = readable_accent(palette.color(group, ACCENT_ROLE), dark)
    soft = mix(text, page, 0.24)
    if dark:
        panel, panel_text, panel_muted = mix(page, text, 0.04), text, soft
    else:
        # Silver: about #d4d4d4 on Windows' #f3f3f3.
        panel = mix(page, text, 0.13)
        panel_text, panel_muted = QColor(text), mix(text, panel, 0.3)
    on_accent = QColor("#000000" if accent.lightnessF() > 0.55 else "#ffffff")
    field = mix(page, text, 0.07)
    if dark:
        accent_hover, popup = mix(accent, QColor("#ffffff"), 0.14), mix(page, text, 0.1)
    else:  # the palette's Base: white for lists and fields in light mode
        accent_hover = mix(accent, QColor("#000000"), 0.12)
        popup = palette.color(group, QPalette.ColorRole.Base)
    return Tones(
        dark=dark,
        page=page,
        line=mix(page, text, 0.09),
        text=QColor(text),
        muted=soft,
        field=field,
        field_hover=mix(field, text, 0.06),
        outline=mix(page, text, 0.13),
        hover=with_alpha(text, 0.06),
        disabled=with_alpha(text, 0.36),
        accent=accent,
        accent_hover=accent_hover,
        accent_down=mix(accent, page, 0.25),
        accent_fill=with_alpha(accent, 0.14),
        on_accent=on_accent,
        popup=popup,
        track=mix(page, text, 0.2 if dark else 0.22),
        knob_disabled=mix(page, text, 0.4),
        switch_off=mix(page, text, 0.18 if dark else 0.2),
        switch_off_hover=mix(page, text, 0.26 if dark else 0.28),
        panel=panel,
        panel_raised=mix(panel, QColor("#ffffff"), 0.07),
        panel_text=panel_text,
        panel_muted=panel_muted,
        panel_accent=accent,
        panel_line=mix(panel, panel_text, 0.09),
        ok=OK[0] if dark else OK[1],
        error=ERROR[0] if dark else ERROR[1],
    )


_CACHE: dict[int, Tones] = {}  # by the palette's cacheKey: the latest only


def tones() -> Tones:
    """The tones of the application's palette now (cached until it changes)."""
    palette = QGuiApplication.palette()
    key = palette.cacheKey()
    if key not in _CACHE:
        _CACHE.clear()
        _CACHE[key] = derive_tones(palette)
    return _CACHE[key]


def window_palette(derived: Tones) -> QPalette:
    """
    The tones in the roles the native style draws with, and only those: the
    other roles stay inherited from the application's palette, so a change
    of it still reaches the window (and the window derives its tones again).
    """
    palette = QPalette()
    groups = (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    )
    for group in groups:
        palette.setColor(group, QPalette.ColorRole.Window, derived.page)
        palette.setColor(group, QPalette.ColorRole.Base, derived.field)
        palette.setColor(group, QPalette.ColorRole.Button, derived.field)
    for group in groups[:2]:
        palette.setColor(group, QPalette.ColorRole.PlaceholderText, derived.muted)
    return palette


# -- text ------------------------------------------------------------------------


def body_font(system: QFont) -> QFont:
    """
    The window process's font: the system's, at least BODY_POINTS (Qt takes
    9 pt on Windows, smaller than Windows 11's own body text).
    """
    font = QFont(system)
    if 0 < font.pointSizeF() < BODY_POINTS:
        font.setPointSizeF(BODY_POINTS)
    return font


def scaled_font(base: QFont, scale: float = 1.0, bold: bool = False) -> QFont:
    font = QFont(base)
    font.setPointSizeF(font.pointSizeF() * scale)
    if bold:
        font.setWeight(QFont.Weight.DemiBold)
    return font


def title(text: str, parent: QWidget | None = None) -> QLabel:
    """A page's title ("Home", "Settings")."""
    label = QLabel(text, parent)
    label.setFont(scaled_font(label.font(), 1.55, bold=True))
    label.setAccessibleName(text)
    return label


def heading(text: str, parent: QWidget | None = None) -> QLabel:
    """A section heading: semi-bold, a little larger."""
    label = QLabel(text, parent)
    label.setFont(scaled_font(label.font(), 1.15, bold=True))
    label.setAccessibleName(text)
    return label


def muted(text: str, parent: QWidget | None = None, *, wrap: bool = False) -> QLabel:
    """Secondary text (descriptions, hints), in the muted tone."""
    label = QLabel(text, parent)
    label.setForegroundRole(QPalette.ColorRole.PlaceholderText)
    label.setFont(scaled_font(label.font(), 0.93))
    label.setWordWrap(wrap)
    return label


def page_layout(widget: QWidget, top: int = PAGE_TOP, bottom: int = 0) -> QVBoxLayout:
    """A page's column: its margins, its spacing."""
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(PAGE_MARGIN, top, PAGE_MARGIN, bottom)
    layout.setSpacing(space(2))
    return layout


def scrolling(content: QWidget, parent: QWidget) -> QScrollArea:
    """A tab's content, scrolling, within the page's margins."""
    box = QWidget(parent)
    layout = QVBoxLayout(box)
    layout.setContentsMargins(PAGE_MARGIN, 0, PAGE_MARGIN, 0)
    layout.addWidget(content)
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(box)
    return scroll


# -- the keyboard focus ------------------------------------------------------------

_KEYBOARD = (
    Qt.FocusReason.TabFocusReason,
    Qt.FocusReason.BacktabFocusReason,
    Qt.FocusReason.ShortcutFocusReason,
)
# The focus coming back, not moving: an open list closing, the window
# activated again.
_RETURNING = (
    Qt.FocusReason.PopupFocusReason,
    Qt.FocusReason.ActiveWindowFocusReason,
)


def keyboard_focus(event: QFocusEvent, before: bool) -> bool:
    """
    Whether a widget's focus is from the keyboard once `event` came in
    (`before`: whether it was): only then is the ring shown, until a click.
    """
    reason = event.reason()
    if reason in _RETURNING:
        return before
    return reason in _KEYBOARD


def draw_focus_ring(
    painter: QPainter, rect: QRectF, radius: float = RADIUS_SMALL
) -> None:
    """The keyboard focus: a RING px accent ring just inside `rect`."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(tones().accent, RING))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    half = RING / 2
    painter.drawRoundedRect(
        rect.adjusted(half, half, -half, -half), radius - half, radius - half
    )
    painter.restore()


def inside_ring(rect: QRectF) -> QRectF:
    """
    A filled control's fill while it shows the ring: inside it, after a
    gap (so the ring shows on an accent fill too).
    """
    inset = RING + RING_GAP
    return rect.adjusted(inset, inset, -inset, -inset)


# -- icons -----------------------------------------------------------------------


def icon_svg(name: str) -> bytes:
    """An icon's SVG, from wherever the package is installed."""
    return resources.files(__package__).joinpath("icons", f"{name}.svg").read_bytes()


@lru_cache(maxsize=256)
def _tinted(name: str, rgb: str, alpha: float, pixels: int, filled: bool) -> QImage:
    data = icon_svg(name)
    if filled:
        data = data.replace(b'fill="none"', b'fill="currentColor"', 1)
    data = data.replace(b"currentColor", rgb.encode("ascii"))
    renderer = QSvgRenderer(QByteArray(data))
    image = QImage(pixels, pixels, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setOpacity(alpha)
    renderer.render(painter)
    painter.end()
    return image


def tinted_pixmap(
    name: str, color: QColor, size: int, ratio: float, filled: bool = False
) -> QPixmap:
    """The icon `name` in `color`, `size` logical px at device pixel `ratio`."""
    image = _tinted(name, color.name(), color.alphaF(), round(size * ratio), filled)
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def icon(
    name: str,
    palette: QPalette,
    role: QPalette.ColorRole = QPalette.ColorRole.WindowText,
    size: int = ICON_SIZE,
    ratio: float = 1.0,
) -> QIcon:
    """The icon tinted from `palette`: normal and disabled."""
    normal = palette.color(QPalette.ColorGroup.Active, role)
    disabled = palette.color(QPalette.ColorGroup.Disabled, role)
    return two_tone_icon(name, normal, disabled, size, ratio)


def two_tone_icon(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    name: str,
    normal: QColor,
    disabled: QColor,
    size: int = ICON_SIZE,
    ratio: float = 1.0,
    filled: bool = False,
) -> QIcon:
    result = QIcon()
    result.addPixmap(
        tinted_pixmap(name, normal, size, ratio, filled), QIcon.Mode.Normal
    )
    result.addPixmap(
        tinted_pixmap(name, disabled, size, ratio, filled), QIcon.Mode.Disabled
    )
    return result


# -- widgets ---------------------------------------------------------------------


class IconButton(QToolButton):
    """
    A borderless button showing one icon, with a tooltip and an accessible
    name. It may be *on* (a toggle that is on): its icon then turns accent
    and filled, on a faint accent fill. The owner sets that from the daemon's
    state, never the click.
    """

    def __init__(self, icon_name: str, label: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._icon_name = icon_name
        self._on = False
        self._keyboard_focus = False
        self.setAutoRaise(True)
        self.setIconSize(QSize(SMALL_ICON, SMALL_ICON))
        self.setFixedSize(38, 34)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.set_label(label)
        self._tint()

    def set_label(self, label: str) -> None:
        self.setToolTip(label)
        self.setAccessibleName(label)

    def set_on(self, on: bool) -> None:
        if on != self._on:
            self._on = on
            self._tint()
            self.update()

    def is_on(self) -> bool:
        return self._on

    def icon_color(self) -> QColor:
        """The colour the icon is drawn in now."""
        if self._on:
            return tones().accent
        return self.palette().color(
            QPalette.ColorGroup.Active, QPalette.ColorRole.ButtonText
        )

    def _tint(self) -> None:
        disabled = self.palette().color(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText
        )
        self.setIcon(
            two_tone_icon(
                self._icon_name,
                self.icon_color(),
                disabled,
                SMALL_ICON,
                self.devicePixelRatioF(),
                filled=self._on,
            )
        )

    @override
    def changeEvent(self, event: QEvent) -> None:  # pylint: disable=invalid-name
        super().changeEvent(event)
        if palette_changed(event):
            self._tint()

    @override
    def focusInEvent(self, event: QFocusEvent) -> None:  # pylint: disable=invalid-name
        super().focusInEvent(event)
        self._keyboard_focus = keyboard_focus(event, self._keyboard_focus)

    @override
    def mousePressEvent(
        self, event: QMouseEvent
    ) -> None:  # pylint: disable=invalid-name
        self._keyboard_focus = False  # the pointer took over: no ring
        super().mousePressEvent(event)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        painter = QStylePainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._on:
            painter.setPen(Qt.PenStyle.NoPen)
            color = tones().accent_fill
            if not self.isEnabled():
                color.setAlphaF(color.alphaF() / 2)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(self.rect()), RADIUS_SMALL, RADIUS_SMALL)
        # The style draws the button and its icon, but not its focus: ours.
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        option.state &= ~QStyle.StateFlag.State_HasFocus
        painter.drawComplexControl(QStyle.ComplexControl.CC_ToolButton, option)
        if self.hasFocus() and self._keyboard_focus:
            draw_focus_ring(painter, QRectF(self.rect()))
        painter.end()


class PaintedButton(QAbstractButton):
    """
    A button drawn here rather than by the style (subclasses paint):
    focusable, a pointing hand, repainted as the pointer comes and goes.
    """

    def __init__(
        self, text: str, parent: QWidget | None = None, *, checkable: bool = False
    ) -> None:
        super().__init__(parent)
        self.setText(text)
        self.setAccessibleName(text)
        self.setCheckable(checkable)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._keyboard_focus = False

    @override
    def focusInEvent(self, event: QFocusEvent) -> None:  # pylint: disable=invalid-name
        super().focusInEvent(event)
        self._keyboard_focus = keyboard_focus(event, self._keyboard_focus)

    @override
    def mousePressEvent(
        self, event: QMouseEvent
    ) -> None:  # pylint: disable=invalid-name
        self._keyboard_focus = False  # the pointer took over: no ring
        super().mousePressEvent(event)

    def shows_focus(self) -> bool:
        """It has the focus, from the keyboard: it shows the ring."""
        return self.hasFocus() and self._keyboard_focus

    def focus_ring(
        self,
        painter: QPainter,
        rect: QRectF | None = None,
        radius: float = RADIUS_SMALL,
    ) -> None:
        """The accent ring while it has the focus from the keyboard."""
        if self.shows_focus():
            draw_focus_ring(
                painter, QRectF(self.rect()) if rect is None else rect, radius
            )

    @override
    def enterEvent(self, event: QEnterEvent) -> None:  # pylint: disable=invalid-name
        super().enterEvent(event)
        self.update()

    @override
    def leaveEvent(self, event: QEvent) -> None:  # pylint: disable=invalid-name
        super().leaveEvent(event)
        self.update()


class TextButton(PaintedButton):
    """
    A secondary action as accent-coloured text (*Browse…*, *Delete
    playlist*, *+ Add playlist*). Painted here: no frame, no fill.
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setFont(scaled_font(self.font(), 0.93, bold=True))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.set_label(text)

    def set_label(self, text: str) -> None:
        """The text; the button is as wide as it."""
        self.setText(text)
        self.setAccessibleName(text.removeprefix("+ "))
        metrics = QFontMetrics(self.font())
        self.setFixedSize(metrics.horizontalAdvance(text) + space(), space(3.5))

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        derived = tones()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = derived.accent if self.isEnabled() else derived.muted
        if self.isDown():
            color = mix(color, derived.page, 0.3)
        painter.setPen(color)
        font = QFont(self.font())
        font.setUnderline(self.underMouse() and self.isEnabled())
        painter.setFont(font)
        painter.drawText(
            QRectF(self.rect()), int(Qt.AlignmentFlag.AlignCenter), self.text()
        )
        self.focus_ring(painter)
        painter.end()


class Divider(QWidget):
    """
    The thin line between sections, rows and areas (`Tones.line`, or
    `Tones.panel_line` on a panel).
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        orientation: Qt.Orientation = Qt.Orientation.Horizontal,
        *,
        on_panel: bool = False,
    ) -> None:
        super().__init__(parent)
        self._on_panel = on_panel
        if orientation == Qt.Orientation.Horizontal:
            self.setFixedHeight(1)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            self.setFixedWidth(1)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        painter = QPainter(self)
        derived = tones()
        line = derived.panel_line if self._on_panel else derived.line
        painter.fillRect(self.rect(), line)
        painter.end()


class _Tab(PaintedButton):
    """One sub-tab: muted text; the current one bold, with the accent underline."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent, checkable=True)
        self.setFont(scaled_font(self.font(), 1.07))
        bold = scaled_font(self.font(), 1.0, bold=True)
        self.setFixedSize(
            QFontMetrics(bold).horizontalAdvance(text) + space(4.5), space(5.5)
        )

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        derived = tones()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        current = self.isChecked()
        if self.underMouse() and not current:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(derived.hover)
            rect = QRectF(self.rect()).adjusted(0, 2, 0, -4)
            painter.drawRoundedRect(rect, RADIUS_SMALL, RADIUS_SMALL)
        font = scaled_font(self.font(), 1.0, bold=current)
        painter.setFont(font)
        painter.setPen(derived.text if current else derived.muted)
        text = QRectF(self.rect()).adjusted(0, 0, 0, -3)
        painter.drawText(text, int(Qt.AlignmentFlag.AlignCenter), self.text())
        if current:
            painter.fillRect(
                QRectF(0, self.height() - 3, self.width(), 3), derived.accent
            )
        self.focus_ring(painter, QRectF(self.rect()).adjusted(0, 0, 0, -3))
        painter.end()


class Tabs(QWidget):
    """
    Sub-tabs (Playlists, General, About): text with an accent underline under
    the current one, on a thin line. `current_changed(index)`; the owner
    shows the page (e.g. in a QStackedWidget).
    """

    current_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idClicked.connect(self._clicked)
        self._row = QHBoxLayout()
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(space(0.5))
        self._row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(self._row)
        layout.addWidget(Divider(self))
        self.setAccessibleName("Sections")

    def add(self, text: str) -> int:
        index = len(self._group.buttons())
        tab = _Tab(text, self)
        self._group.addButton(tab, index)
        self._row.insertWidget(index, tab)
        if index == 0:
            tab.setChecked(True)
        return index

    def tab(self, index: int) -> QAbstractButton:
        return self._group.button(index)

    def current_index(self) -> int:
        return self._group.checkedId()

    def set_current_index(self, index: int) -> None:
        if index != self.current_index():
            self._group.button(index).setChecked(True)
            self.current_changed.emit(index)

    @Slot(int)
    def _clicked(self, index: int) -> None:
        self.current_changed.emit(index)


class Panel(QWidget):
    """
    The panel behind images (`Tones.panel`), rounded. Its text takes the
    panel's text tones (light text on the dark panel in light mode).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._applied: Tones | None = None
        self._apply()

    def _apply(self) -> None:
        derived = tones()
        if derived == self._applied:
            return
        self._applied = derived
        palette = QPalette()  # only these roles; the rest is inherited
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
            palette.setColor(group, QPalette.ColorRole.WindowText, derived.panel_text)
            palette.setColor(
                group, QPalette.ColorRole.PlaceholderText, derived.panel_muted
            )
        self.setPalette(palette)

    @override
    def changeEvent(self, event: QEvent) -> None:  # pylint: disable=invalid-name
        super().changeEvent(event)
        if palette_changed(event):
            self._apply()

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tones().panel)
        painter.drawRoundedRect(QRectF(self.rect()), RADIUS, RADIUS)
        painter.end()


def field_text_x(widget: QWidget) -> int:
    """
    Where a borderless line edit's text starts, in `widget`'s style: the
    style's line-edit contents, then FIELD_PADDING, then QLineEdit's own
    margin (Fusion: 0 + 4 + 2; Windows 11: 4 + 4 + 2).
    """
    option = QStyleOptionFrame()
    option.initFrom(widget)
    option.lineWidth = 0  # borderless
    option.midLineWidth = 0
    contents = widget.style().subElementRect(
        QStyle.SubElement.SE_LineEditContents, option, None
    )
    return contents.x() + FIELD_PADDING + LINE_EDIT_MARGIN


class ValueLabel(QLabel):
    """A read-only value in a column of fields: its text starts where theirs do."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._align()

    def _align(self) -> None:
        self.setContentsMargins(field_text_x(self), 0, 0, 0)

    @override
    def changeEvent(self, event: QEvent) -> None:  # pylint: disable=invalid-name
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._align()


class Banner(QLabel):
    """A page's message (an error): wrapped, selectable, hidden while empty."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.set_message(None)

    def set_message(self, text: str | None) -> None:
        self.setText(text or "")
        self.setVisible(bool(text))


class Chip(QLabel):
    """A small pill (e.g. *Held*): accent text on a faint accent fill."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFont(scaled_font(self.font(), 0.8, bold=True))
        self.setContentsMargins(space(), 2, space(), 2)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        derived = tones()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(derived.accent_fill)
        rect = QRectF(self.rect())
        radius = rect.height() / 2
        painter.drawRoundedRect(rect, radius, radius)
        painter.setPen(derived.accent)
        painter.setFont(self.font())
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), self.text())
        painter.end()


class ImageView(QWidget):
    """
    An image letterboxed on the panel tone, at a fixed aspect ratio (16:9 by
    default), with rounded corners. Without an image it shows `placeholder`.
    """

    def __init__(self, aspect: float = 16 / 9, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._aspect = aspect
        self._pixmap: QPixmap | None = None
        self._scaled: QPixmap | None = None  # _pixmap at the current size
        self._placeholder = ""
        # The height follows the width (resizeEvent): layouts ignore
        # height-for-width, and overriding Qt's sizing methods is against
        # the rules (AGENTS.md, rule 3).
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(160)
        self.setFixedHeight(self.height_for(320))

    def set_pixmap(self, pixmap: QPixmap | None, placeholder: str = "") -> None:
        self._pixmap = None if pixmap is None or pixmap.isNull() else pixmap
        self._scaled = None
        self._placeholder = placeholder
        self.update()

    def pixmap(self) -> QPixmap | None:
        return self._pixmap

    def placeholder(self) -> str:
        return self._placeholder

    def height_for(self, width: int) -> int:
        return round(width / self._aspect)

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:  # pylint: disable=invalid-name
        super().resizeEvent(event)
        height = self.height_for(event.size().width())
        if height != self.height():  # only the width drives this: no loop
            self.setFixedHeight(height)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        derived = tones()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(area, RADIUS_SMALL, RADIUS_SMALL)
        painter.setClipPath(clip)
        painter.fillRect(area, derived.panel)
        if self._pixmap is not None:
            scaled = self._fitted()
            ratio = scaled.devicePixelRatio()
            width, height = scaled.width() / ratio, scaled.height() / ratio
            x = (area.width() - width) / 2
            y = (area.height() - height) / 2
            painter.drawPixmap(QRectF(x, y, width, height).toRect(), scaled)
        elif self._placeholder:
            painter.setPen(derived.panel_muted)
            painter.drawText(area, int(Qt.AlignmentFlag.AlignCenter), self._placeholder)
        painter.end()

    def _fitted(self) -> QPixmap:
        """The image scaled to fit (letterboxed), cached for the size."""
        assert self._pixmap is not None
        ratio = self.devicePixelRatioF()
        box = QSize(round(self.width() * ratio), round(self.height() * ratio))
        if self._scaled is None or not _fits(self._scaled.size(), box):
            self._scaled = self._pixmap.scaled(
                box,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._scaled.setDevicePixelRatio(ratio)
        return self._scaled


def _fits(size: QSize, box: QSize) -> bool:
    """`size` is the largest that keeps its aspect inside `box`."""
    return (size.width() == box.width() and size.height() <= box.height()) or (
        size.height() == box.height() and size.width() <= box.width()
    )
