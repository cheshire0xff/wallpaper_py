"""Windows session lock and input idle time (user32, no extra dependencies)."""

import sys

assert sys.platform == "win32"  # pylint: disable=wrong-import-position

# pylint: disable=wrong-import-position,wrong-import-order,invalid-name
import ctypes
from ctypes import wintypes

DESKTOP_SWITCHDESKTOP = 0x0100
UOI_NAME = 2


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


_user32 = ctypes.WinDLL("user32")
_kernel32 = ctypes.WinDLL("kernel32")
_user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
_user32.GetLastInputInfo.restype = wintypes.BOOL
_kernel32.GetTickCount.argtypes = []
_kernel32.GetTickCount.restype = wintypes.DWORD
_user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_user32.OpenInputDesktop.restype = wintypes.HANDLE
_user32.CloseDesktop.argtypes = [wintypes.HANDLE]
_user32.CloseDesktop.restype = wintypes.BOOL
_user32.GetUserObjectInformationW.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
_user32.GetUserObjectInformationW.restype = wintypes.BOOL


class WindowsPresence:
    def session_locked(self) -> bool | None:
        """
        While locked (or on the secure desktop of a UAC prompt) the input
        desktop is Winlogon's, which a normal process can't open.
        """
        desktop = _user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
        if not desktop:
            return True
        try:
            name = ctypes.create_unicode_buffer(256)
            needed = wintypes.DWORD(0)
            if not _user32.GetUserObjectInformationW(
                desktop, UOI_NAME, name, ctypes.sizeof(name), ctypes.byref(needed)
            ):
                return None
            return name.value.lower() != "default"
        finally:
            _user32.CloseDesktop(desktop)

    def idle_seconds(self) -> float | None:
        info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
        if not _user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        # Both are 32-bit millisecond tick counts that wrap after 49.7 days.
        idle_ms = (int(_kernel32.GetTickCount()) - int(info.dwTime)) & 0xFFFFFFFF
        return idle_ms / 1000
