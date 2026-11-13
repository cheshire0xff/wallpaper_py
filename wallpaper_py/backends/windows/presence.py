"""Windows session lock and input idle time (Win32 via ctypes, no extra deps)."""

import sys

assert sys.platform == "win32"  # pylint: disable=wrong-import-position

# pylint: disable=wrong-import-position,wrong-import-order,invalid-name
import ctypes
from ctypes import wintypes

DESKTOP_SWITCHDESKTOP = 0x0100
UOI_NAME = 2
WTS_CURRENT_SESSION = 0xFFFFFFFF
WTSSessionInfoEx = 25
WTS_SESSIONSTATE_LOCK = 0
WTS_SESSIONSTATE_UNLOCK = 1


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class WTSINFOEX_LEVEL1_W(ctypes.Structure):
    _fields_ = [
        ("SessionId", wintypes.ULONG),
        ("SessionState", ctypes.c_int),
        ("SessionFlags", wintypes.LONG),
        ("WinStationName", wintypes.WCHAR * 33),
        ("UserName", wintypes.WCHAR * 21),
        ("DomainName", wintypes.WCHAR * 18),
        ("LogonTime", wintypes.LARGE_INTEGER),
        ("ConnectTime", wintypes.LARGE_INTEGER),
        ("DisconnectTime", wintypes.LARGE_INTEGER),
        ("LastInputTime", wintypes.LARGE_INTEGER),
        ("CurrentTime", wintypes.LARGE_INTEGER),
        ("IncomingBytes", wintypes.DWORD),
        ("OutgoingBytes", wintypes.DWORD),
        ("IncomingFrames", wintypes.DWORD),
        ("OutgoingFrames", wintypes.DWORD),
        ("IncomingCompressedBytes", wintypes.DWORD),
        ("OutgoingCompressedBytes", wintypes.DWORD),
    ]


class WTSINFOEXW(ctypes.Structure):
    # Data is a union with this one member.
    _fields_ = [("Level", wintypes.DWORD), ("Data", WTSINFOEX_LEVEL1_W)]


assert ctypes.sizeof(WTSINFOEXW) == 232

_user32 = ctypes.WinDLL("user32")
_kernel32 = ctypes.WinDLL("kernel32")
_wtsapi32 = ctypes.WinDLL("wtsapi32")
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
_wtsapi32.WTSQuerySessionInformationW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(wintypes.DWORD),
]
_wtsapi32.WTSQuerySessionInformationW.restype = wintypes.BOOL
_wtsapi32.WTSFreeMemory.argtypes = [ctypes.c_void_p]
_wtsapi32.WTSFreeMemory.restype = None


class WindowsPresence:
    def session_locked(self) -> bool | None:
        """
        The session's lock flag (WTS). If that can't be read, fall back to
        whether the input desktop is inaccessible.
        """
        locked = _wts_session_locked()
        return locked if locked is not None else _input_desktop_locked()

    def idle_seconds(self) -> float | None:
        info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
        if not _user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        # Both are 32-bit millisecond tick counts that wrap after 49.7 days.
        idle_ms = (int(_kernel32.GetTickCount()) - int(info.dwTime)) & 0xFFFFFFFF
        return idle_ms / 1000


def _wts_session_locked() -> bool | None:
    """
    WTSINFOEX SessionFlags of the current session; None if unavailable.

    Reliable for the whole lock: verified on Windows 11 to read LOCK from the
    moment Win+L is pressed. (The flag is inverted on Windows 7, which the
    supported Python versions don't run on anyway.)
    """
    buffer, size = ctypes.c_void_p(), wintypes.DWORD(0)
    if not _wtsapi32.WTSQuerySessionInformationW(
        None,  # WTS_CURRENT_SERVER_HANDLE
        WTS_CURRENT_SESSION,
        WTSSessionInfoEx,
        ctypes.byref(buffer),
        ctypes.byref(size),
    ):
        return None
    try:
        info = ctypes.cast(buffer, ctypes.POINTER(WTSINFOEXW)).contents
        if info.Level != 1:
            return None
        flags = info.Data.SessionFlags
    finally:
        _wtsapi32.WTSFreeMemory(buffer)
    if flags == WTS_SESSIONSTATE_LOCK:
        return True
    if flags == WTS_SESSIONSTATE_UNLOCK:
        return False
    return None


def _input_desktop_locked() -> bool | None:
    """
    Fallback: the input desktop can't be opened while Winlogon's secure
    desktop is shown. Note that on Windows 10/11 this only covers the PIN /
    password prompt, not the lock screen picture before it.
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
