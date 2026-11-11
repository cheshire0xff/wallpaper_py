"""
Friendly monitor names (from EDID) keyed by monitor device path.

QueryDisplayConfig + DisplayConfigGetDeviceInfo(GET_TARGET_NAME) return the
same device path that IDesktopWallpaper uses as monitor id, so names map to
ids directly, without matching on positions (which change when a monitor
detaches).
"""

import sys

assert sys.platform == "win32"  # pylint: disable=wrong-import-position

# pylint: disable=wrong-import-position,wrong-import-order,invalid-name
import ctypes
from ctypes import wintypes

QDC_ONLY_ACTIVE_PATHS = 0x2
DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME = 2
ERROR_SUCCESS = 0
ERROR_INSUFFICIENT_BUFFER = 122


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class DISPLAYCONFIG_PATH_SOURCE_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", LUID),
        ("id", wintypes.UINT),
        ("modeInfoIdx", wintypes.UINT),
        ("statusFlags", wintypes.UINT),
    ]


class DISPLAYCONFIG_RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", wintypes.UINT), ("Denominator", wintypes.UINT)]


class DISPLAYCONFIG_PATH_TARGET_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", LUID),
        ("id", wintypes.UINT),
        ("modeInfoIdx", wintypes.UINT),
        ("outputTechnology", wintypes.UINT),
        ("rotation", wintypes.UINT),
        ("scaling", wintypes.UINT),
        ("refreshRate", DISPLAYCONFIG_RATIONAL),
        ("scanLineOrdering", wintypes.UINT),
        ("targetAvailable", wintypes.BOOL),
        ("statusFlags", wintypes.UINT),
    ]


class DISPLAYCONFIG_PATH_INFO(ctypes.Structure):
    _fields_ = [
        ("sourceInfo", DISPLAYCONFIG_PATH_SOURCE_INFO),
        ("targetInfo", DISPLAYCONFIG_PATH_TARGET_INFO),
        ("flags", wintypes.UINT),
    ]


class DISPLAYCONFIG_MODE_INFO(ctypes.Structure):
    # Only passed through; the 48-byte union is kept opaque.
    _fields_ = [
        ("infoType", wintypes.UINT),
        ("id", wintypes.UINT),
        ("adapterId", LUID),
        ("_union", ctypes.c_byte * 48),
    ]


class DISPLAYCONFIG_DEVICE_INFO_HEADER(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.UINT),
        ("size", wintypes.UINT),
        ("adapterId", LUID),
        ("id", wintypes.UINT),
    ]


class DISPLAYCONFIG_TARGET_DEVICE_NAME(ctypes.Structure):
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("flags", wintypes.UINT),
        ("outputTechnology", wintypes.UINT),
        ("edidManufactureId", wintypes.USHORT),
        ("edidProductCodeId", wintypes.USHORT),
        ("connectorInstance", wintypes.UINT),
        ("monitorFriendlyDeviceName", wintypes.WCHAR * 64),
        ("monitorDevicePath", wintypes.WCHAR * 128),
    ]


assert ctypes.sizeof(DISPLAYCONFIG_PATH_INFO) == 72
assert ctypes.sizeof(DISPLAYCONFIG_MODE_INFO) == 64
assert ctypes.sizeof(DISPLAYCONFIG_TARGET_DEVICE_NAME) == 420

_user32 = ctypes.WinDLL("user32")
_user32.GetDisplayConfigBufferSizes.argtypes = [
    wintypes.UINT,
    ctypes.POINTER(wintypes.UINT),
    ctypes.POINTER(wintypes.UINT),
]
_user32.GetDisplayConfigBufferSizes.restype = wintypes.LONG
_user32.QueryDisplayConfig.argtypes = [
    wintypes.UINT,
    ctypes.POINTER(wintypes.UINT),
    ctypes.POINTER(DISPLAYCONFIG_PATH_INFO),
    ctypes.POINTER(wintypes.UINT),
    ctypes.POINTER(DISPLAYCONFIG_MODE_INFO),
    ctypes.c_void_p,
]
_user32.QueryDisplayConfig.restype = wintypes.LONG
_user32.DisplayConfigGetDeviceInfo.argtypes = [
    ctypes.POINTER(DISPLAYCONFIG_DEVICE_INFO_HEADER)
]
_user32.DisplayConfigGetDeviceInfo.restype = wintypes.LONG


def _active_paths() -> list[DISPLAYCONFIG_PATH_INFO]:
    for _ in range(3):  # the configuration can change between the two calls
        n_paths, n_modes = wintypes.UINT(0), wintypes.UINT(0)
        if (
            _user32.GetDisplayConfigBufferSizes(
                QDC_ONLY_ACTIVE_PATHS, ctypes.byref(n_paths), ctypes.byref(n_modes)
            )
            != ERROR_SUCCESS
        ):
            return []
        paths = (DISPLAYCONFIG_PATH_INFO * n_paths.value)()
        modes = (DISPLAYCONFIG_MODE_INFO * n_modes.value)()
        status = _user32.QueryDisplayConfig(
            QDC_ONLY_ACTIVE_PATHS,
            ctypes.byref(n_paths),
            paths,
            ctypes.byref(n_modes),
            modes,
            None,
        )
        if status == ERROR_SUCCESS:
            return list(paths[: n_paths.value])
        if status != ERROR_INSUFFICIENT_BUFFER:
            return []
    return []


def monitor_names() -> dict[str, str]:
    """{device path (lower-case): friendly name} for active monitors."""
    names: dict[str, str] = {}
    for path in _active_paths():
        target = DISPLAYCONFIG_TARGET_DEVICE_NAME()
        target.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME
        target.header.size = ctypes.sizeof(target)
        target.header.adapterId = path.targetInfo.adapterId
        target.header.id = path.targetInfo.id
        if _user32.DisplayConfigGetDeviceInfo(ctypes.byref(target.header)) != 0:
            continue
        device_path = target.monitorDevicePath
        if device_path and target.monitorFriendlyDeviceName:
            names[device_path.lower()] = target.monitorFriendlyDeviceName
    return names
