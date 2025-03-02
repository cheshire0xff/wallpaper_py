import ctypes
from ctypes import HRESULT, POINTER, Structure, pointer
from ctypes.wintypes import LPCWSTR, UINT, LPWSTR, DWORD
from typing import cast

import comtypes.client
from comtypes import IUnknown, GUID, COMMETHOD

class RECT(Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]



class IDesktopWallpaper(IUnknown):
    _iid_ = GUID("{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}")

    @classmethod
    def CoCreateInstance(cls) -> "IDesktopWallpaper":
        # Search `Desktop Wallpaper` in `\HKEY_CLASSES_ROOT\CLSID` to obtain the magic string
        class_id = GUID('{C2CF3110-460E-4fc1-B9D0-8A1C0C9CC4BD}')
        instance = comtypes.CoCreateInstance(class_id, interface=cls)
        return cast(IDesktopWallpaper, instance)

    _methods_ = [
        COMMETHOD([], HRESULT, "SetWallpaper",
            (["in"], LPCWSTR, "monitorID"),
            (["in"], LPCWSTR, "wallpaper")),
        COMMETHOD([], HRESULT, "GetWallpaper",
            (["in"], LPCWSTR, "monitorID"),
            (["out"], POINTER(LPWSTR), "wallpaper")),
        COMMETHOD([], HRESULT, "GetMonitorDevicePathAt",
            (["in"], UINT, "monitorIndex"),
            (["out"], POINTER(LPWSTR), "monitorID")),
        COMMETHOD([], HRESULT, "GetMonitorDevicePathCount",
            (["out"], POINTER(UINT), "count")),
        COMMETHOD([], HRESULT, "GetMonitorRECT",
            (["in"], LPCWSTR, "monitorID"),
            (["out"], POINTER(RECT), "pRect")),
        COMMETHOD([], HRESULT, "SetPosition",
            (["in"], DWORD, "position")),
    ]

    def GetMonitorDevicePathCount(self) -> int:
        count = UINT()
        self.__com_GetMonitorDevicePathCount(pointer(count))
        return count.value

    def SetWallpaper(self, monitorId: str, wallpaper: str) -> None:
        self.__com_SetWallpaper(LPCWSTR(monitorId), LPCWSTR(wallpaper))
    
    def GetMonitorRECT(self, monitorId: str) -> RECT:
        rect = RECT()
        self.__com_GetMonitorRECT(LPCWSTR(monitorId), pointer(rect))
        return rect

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