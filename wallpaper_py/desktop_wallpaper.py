import ctypes
from ctypes import HRESULT, POINTER, Structure, pointer
from ctypes.wintypes import LPCWSTR, UINT, LPWSTR, DWORD
from dataclasses import dataclass
from enum import Enum
from typing import cast

from comtypes import IUnknown, GUID, COMMETHOD, CoCreateInstance


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


@dataclass
class Rectangle:
    x1: int
    y1: int
    x2: int
    y2: int

    def get_height(self) -> int:
        return self.y2 - self.y1

    def get_width(self) -> int:
        return self.x2 - self.x1


class IDesktopWallpaper(IUnknown):  # type: ignore[misc]
    _iid_ = GUID("{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}")

    @classmethod
    def CoCreateInstance(cls) -> "IDesktopWallpaper":
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

    def GetMonitorDevicePathCount(self) -> int:
        count = UINT()
        self.__com_GetMonitorDevicePathCount(pointer(count))
        return count.value

    def SetWallpaper(self, monitorId: str, wallpaper: str) -> None:
        self.__com_SetWallpaper(LPCWSTR(monitorId), LPCWSTR(wallpaper))

    def GetMonitorRECT(self, monitorId: str) -> Rectangle:
        rect = RECT()
        self.__com_GetMonitorRECT(LPCWSTR(monitorId), pointer(rect))
        return Rectangle(rect.left, rect.top, rect.right, rect.bottom)

    def GetWallpaper(self, monitorId: str) -> str:
        wallpaper = LPWSTR()
        self.__com_GetWallpaper(LPCWSTR(monitorId), pointer(wallpaper))
        assert wallpaper.value is not None
        return wallpaper.value

    def GetMonitorDevicePathAt(self, monitorIndex: int) -> str:
        monitorId = LPWSTR()
        self.__com_GetMonitorDevicePathAt(UINT(monitorIndex), pointer(monitorId))
        assert monitorId.value is not None
        return monitorId.value

    def GetPosition(self) -> Position:
        dword = DWORD()
        self.__com_GetPosition(pointer(dword))
        assert dword.value is not None
        return Position(dword.value)

    def SetPosition(self, position: Position) -> None:
        result = self.__com_SetPosition(DWORD(position.value))
        print(result)

    def GetBackgroundColor(self) -> int:
        dword = DWORD()
        self.__com_GetBackgroundColor(pointer(dword))
        assert dword.value is not None
        return dword.value
