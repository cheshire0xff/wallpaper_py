"""
The look every page shares (docs/plan.md, "Look" and "One window"; the
mockups in docs/design/): Qt's native style and the system's palette, plus
a few tones derived from it, constants and small painted widgets. Not a
framework.

- **Tones** (`derive_tones`, `tones`): derived from the application's
  `QPalette`, never hard-coded per OS. In dark mode the page is a shade
  darker than the palette's Window colour; the sidebar is the same colour,
  set apart only by a thin lighter `line`; image panels are a little
  lighter than the page. Light mode keeps the palette's Window colour and a
  dark panel behind images (`BACKDROP`: wallpapers look washed out on
  white). `window_palette` puts the tones into the palette roles the native
  style draws with (Window, Base and Button for the borderless filled
  fields, PlaceholderText for muted text); the main window sets it, and
  everything painted here reads `tones()` when it paints, so a change of
  the system's light / dark mode re-derives everything.
- Spacing in 8 px steps (`space`), corner radii, the control column's
  width, field heights.
- Icons: bundled Lucide SVGs (`icons/`, ISC licence kept with them),
  tinted when drawn (`tinted_pixmap`, `icon`); a toggle that is on shows
  its icon filled.
- Widgets: `IconButton` (borderless; on: accent icon on a faint accent
  fill), `TextButton` (accent text, for secondary actions), `Tabs`
  (sub-tabs with an accent underline), `Divider` (the thin line), `Panel`
  (the lighter panel behind images), `Chip`, `ImageView` (an image
  letterboxed on the panel tone).

No stylesheet is set anywhere: the OS's own style draws the native widgets
(Windows 11 style, Breeze on KDE).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

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
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
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
CONTROL_WIDTH = 360  # the settings' control column
PAGE_MARGIN = 32  # left / right of a page's content
PAGE_TOP = 20

# Behind images in light mode (and its text); dark mode uses `Tones.panel`.
BACKDROP = QColor("#161616")
BACKDROP_TEXT = QColor("#e8e8e8")
BACKDROP_TEXT_DIM = QColor("#a0a0a0")
# The status dot (the palette has no such roles): dark mode, light mode.
OK = (QColor("#6ccb5f"), QColor("#0f7b0f"))
ERROR = (QColor("#ff99a4"), QColor("#c42b1c"))

# The bundled icons: Lucide names (https://lucide.dev), files in icons/.
ICON_NAMES = (
    "chevron-down",
    "circle-alert",
    "house",
    "images",
    "link",
    "pin",
    "skip-back",
    "skip-forward",
    "sliders-horizontal",
    "star",
    "trash-2",
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


def is_dark(palette: QPalette) -> bool:
    return palette.color(QPalette.ColorRole.Window).lightnessF() < 0.5


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
    hover: QColor  # a hovered row or item (translucent)
    accent: QColor  # readable on the page
    accent_fill: QColor  # a faint accent fill (translucent)
    on_accent: QColor  # text on a solid accent fill
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
    dark = is_dark(palette)
    page = window.darker(122) if dark else QColor(window)
    accent = readable_accent(palette.color(group, QPalette.ColorRole.Accent), dark)
    soft = mix(text, page, 0.24)
    if dark:
        panel, panel_text, panel_muted = mix(page, text, 0.04), text, soft
    else:
        panel, panel_text, panel_muted = BACKDROP, BACKDROP_TEXT, BACKDROP_TEXT_DIM
    on_accent = QColor("#000000" if accent.lightnessF() > 0.55 else "#ffffff")
    return Tones(
        dark=dark,
        page=page,
        line=mix(page, text, 0.09),
        text=QColor(text),
        muted=soft,
        field=mix(page, text, 0.07),
        hover=with_alpha(text, 0.06),
        accent=accent,
        accent_fill=with_alpha(accent, 0.14),
        on_accent=on_accent,
        panel=panel,
        panel_raised=mix(panel, QColor("#ffffff"), 0.07),
        panel_text=panel_text,
        panel_muted=panel_muted,
        panel_accent=accent if dark else readable_accent(accent, True),
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
    The UI process's font: the system's, at least BODY_POINTS (Qt takes
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
    and filled, on a faint accent fill. The owner sets that from the app's
    state, never the click.
    """

    def __init__(self, icon_name: str, label: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._icon_name = icon_name
        self._on = False
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

    def changeEvent(self, event: QEvent) -> None:  # pylint: disable=invalid-name
        super().changeEvent(event)
        if palette_changed(event):
            self._tint()

    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        if self._on:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            color = tones().accent_fill
            if not self.isEnabled():
                color.setAlphaF(color.alphaF() / 2)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(self.rect()), RADIUS_SMALL, RADIUS_SMALL)
            painter.end()
        super().paintEvent(event)


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

    def focusInEvent(self, event: QFocusEvent) -> None:  # pylint: disable=invalid-name
        super().focusInEvent(event)
        self._keyboard_focus = event.reason() in (
            Qt.FocusReason.TabFocusReason,
            Qt.FocusReason.BacktabFocusReason,
            Qt.FocusReason.ShortcutFocusReason,
        )

    def focus_ring(self, painter: QPainter, rect: QRectF | None = None) -> None:
        """An accent outline while it has the focus from the keyboard."""
        if not (self.hasFocus() and self._keyboard_focus):
            return
        area = QRectF(self.rect()) if rect is None else rect
        painter.save()
        painter.setPen(with_alpha(tones().accent, 0.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        area = area.adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(area, RADIUS_SMALL, RADIUS_SMALL)
        painter.restore()

    def enterEvent(self, event: QEnterEvent) -> None:  # pylint: disable=invalid-name
        super().enterEvent(event)
        self.update()

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

    def changeEvent(self, event: QEvent) -> None:  # pylint: disable=invalid-name
        super().changeEvent(event)
        if palette_changed(event):
            self._apply()

    def paintEvent(self, event: QPaintEvent) -> None:  # pylint: disable=invalid-name
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tones().panel)
        painter.drawRoundedRect(QRectF(self.rect()), RADIUS, RADIUS)
        painter.end()


class Chip(QLabel):
    """A small pill (e.g. *Held*): accent text on a faint accent fill."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFont(scaled_font(self.font(), 0.8, bold=True))
        self.setContentsMargins(space(), 2, space(), 2)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

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

    def resizeEvent(self, event: QResizeEvent) -> None:  # pylint: disable=invalid-name
        super().resizeEvent(event)
        height = self.height_for(event.size().width())
        if height != self.height():  # only the width drives this: no loop
            self.setFixedHeight(height)

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
