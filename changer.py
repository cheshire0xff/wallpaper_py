import os
import sys
import ctypes
from ctypes import HRESULT, POINTER, byref, Structure, pointer
from ctypes.wintypes import LPCWSTR, UINT, LPWSTR, DWORD
from typing import Literal, cast
import winreg
from pathlib import Path

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


def list_monitors() -> None:
    """List all connected monitors with their properties."""
    comtypes.CoInitialize()
    try:
        dw = IDesktopWallpaper.CoCreateInstance()
        print("Before")
        count = dw.GetMonitorDevicePathCount()
        print(f"Connected monitors ({count}):")
        
        for index in range(count):
            monitor_id = dw.GetMonitorDevicePathAt(index)
            rect = dw.GetMonitorRECT(monitor_id)
            
            print(f"[Monitor {index}]")
            print(f"  Device ID: {monitor_id}")
            print(f"  Position:  {rect.left}, {rect.top} to {rect.right}, {rect.bottom}")
            print(f"  Size:      {rect.right - rect.left}x{rect.bottom - rect.top}\n")
            
    except comtypes.COMError as e:
        print(f"COM Error: {e}")
    finally:
        comtypes.CoUninitialize()

def set_wallpaper(image_path: str, monitor_ix: int = 0) -> None:
    comtypes.CoInitialize()
    try:
        dw = IDesktopWallpaper.CoCreateInstance()

        count = dw.GetMonitorDevicePathCount()
        if monitor_ix >= count:
            raise ValueError(f"Invalid index: {monitor_ix}. Found {count} monitors.")

        abs_path = str(Path(image_path).absolute())
        if not os.path.exists(abs_path):
            raise FileNotFoundError(f"Wallpaper not found: {abs_path}")

        monitor_id = dw.GetMonitorDevicePathAt(monitor_ix)
        dw.SetWallpaper(monitor_id, abs_path)
        
    except comtypes.COMError as e:
        print(f"COM Error: {e}")
    finally:
        comtypes.CoUninitialize()

def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python changer.py [list|set]")
        sys.exit(1)
        
    if sys.argv[1] == "list":
        list_monitors()
    elif sys.argv[1] == "set":
        if len(sys.argv) < 3:
            print("Usage: python changer.py set <image_path> [monitor_index=0] [mode=FILL]")
            sys.exit(1)
        monitor_ix = int(sys.argv[3]) if len(sys.argv) > 2 else 0
        set_wallpaper(sys.argv[2], monitor_ix)
        print(f"Wallpaper set successfully on monitor {monitor_ix}")


if __name__ == "__main__":
    main()