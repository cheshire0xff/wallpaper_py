"""Letting another process bring its window to the front (Win32 via ctypes)."""

import sys

assert sys.platform == "win32"  # pylint: disable=wrong-import-position

# pylint: disable=wrong-import-position,wrong-import-order
import ctypes

ASFW_ANY = -1  # any process


def allow_foreground() -> None:
    """
    Windows only lets the foreground process (and those it allows) take the
    foreground. Call it right after the user's click, before asking another
    process to show a window. Best effort: a refusal is harmless.
    """
    ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)
