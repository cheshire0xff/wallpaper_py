"""A window's title bar in a colour of our own (DWM via ctypes, Windows 11)."""

import sys

assert sys.platform == "win32"  # pylint: disable=wrong-import-position

# pylint: disable=wrong-import-position,wrong-import-order
import ctypes
from ctypes import wintypes

DWMWA_CAPTION_COLOR = 35  # Windows 11 (build 22000) and later

_dwmapi = ctypes.WinDLL("dwmapi")
_dwmapi.DwmSetWindowAttribute.argtypes = [
    wintypes.HWND,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
]
_dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long  # HRESULT, not raised


def set_title_bar_colour(window: int, rgb: tuple[int, int, int]) -> None:
    """
    Paint the title bar of `window` (an HWND) in `rgb`. Best effort: Windows
    10 refuses the attribute and keeps its own.
    """
    red, green, blue = rgb
    colour = wintypes.DWORD(red | green << 8 | blue << 16)  # COLORREF: 0x00BBGGRR
    _dwmapi.DwmSetWindowAttribute(
        window, DWMWA_CAPTION_COLOR, ctypes.byref(colour), ctypes.sizeof(colour)
    )
