import ctypes
from ctypes import HRESULT, POINTER, Structure, pointer
from ctypes.wintypes import LPCWSTR, UINT, LPWSTR, DWORD
from enum import Enum
from typing import cast
from comtypes import IUnknown, GUID, COMMETHOD, CoCreateInstance
from wallpaper_py.desktop_protocol import Rectangle


class RECT(Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class Position(Enum):
    CENTER = 0
    TILE = 1
    STRETCH = 2
    FIT = 3
    FILL = 4
    SPAN = 5


class IDesktopWallpaper(IUnknown):  # type: ignore[misc]
    _iid_ = GUID("{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}")

    @classmethod
    def co_create_instance(cls) -> "IDesktopWallpaper":
        # Search `Desktop Wallpaper` in `\HKEY_CLASSES_ROOT\CLSID` to obtain the magic string
        class_id = GUID("{C2CF3110-460E-4fc1-B9D0-8A1C0C9CC4BD}")
        instance = CoCreateInstance(class_id, interface=cls)
        return cast(IDesktopWallpaper, instance)

    _methods_ = [
        COMMETHOD(
            [],
            HRESULT,
            "SetWallpaper",
            (["in"], LPCWSTR, "monitorID"),
            (["in"], LPCWSTR, "wallpaper"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "GetWallpaper",
            (["in"], LPCWSTR, "monitorID"),
            (["out"], POINTER(LPWSTR), "wallpaper"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "GetMonitorDevicePathAt",
            (["in"], UINT, "monitorIndex"),
            (["out"], POINTER(LPWSTR), "monitorID"),
        ),
        COMMETHOD(
            [], HRESULT, "GetMonitorDevicePathCount", (["out"], POINTER(UINT), "count")
        ),
        COMMETHOD(
            [],
            HRESULT,
            "GetMonitorRECT",
            (["in"], LPCWSTR, "monitorID"),
            (["out"], POINTER(RECT), "pRect"),
        ),
        COMMETHOD([], HRESULT, "SetBackgroundColor", (["in"], DWORD, "color")),
        COMMETHOD(
            [], HRESULT, "GetBackgroundColor", (["out"], POINTER(DWORD), "color")
        ),
        COMMETHOD([], HRESULT, "SetPosition", (["in"], DWORD, "position")),
        COMMETHOD([], HRESULT, "GetPosition", (["out"], POINTER(DWORD), "position")),
    ]

    def get_monitor_device_path_count(self) -> int:
        count = UINT()
        self.__com_GetMonitorDevicePathCount(pointer(count))
        return count.value

    def set_wallpaper(self, monitor_id: str, wallpaper: str) -> None:
        self.__com_SetWallpaper(LPCWSTR(monitor_id), LPCWSTR(wallpaper))

    def get_monitor_rect(self, monitor_id: str) -> Rectangle:
        rect = RECT()
        self.__com_GetMonitorRECT(LPCWSTR(monitor_id), pointer(rect))
        return Rectangle(rect.left, rect.top, rect.right, rect.bottom)

    def get_wallpaper(self, monitor_id: str) -> str:
        wallpaper = LPWSTR()
        self.__com_GetWallpaper(LPCWSTR(monitor_id), pointer(wallpaper))
        assert wallpaper.value is not None
        return wallpaper.value

    def get_monitor_device_path_at(self, monitor_index: int) -> str:
        monitor_id = LPWSTR()
        self.__com_GetMonitorDevicePathAt(UINT(monitor_index), pointer(monitor_id))
        assert monitor_id.value is not None
        return monitor_id.value

    def get_position(self) -> Position:
        dword = DWORD()
        self.__com_GetPosition(pointer(dword))
        assert dword.value is not None
        return Position(dword.value)

    def set_position(self, position: Position) -> None:
        result = self.__com_SetPosition(DWORD(position.value))
        print(result)

    def get_background_color(self) -> int:
        dword = DWORD()
        self.__com_GetBackgroundColor(pointer(dword))
        assert dword.value is not None
        return dword.value
