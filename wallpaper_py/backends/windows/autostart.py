"""Start at login on Windows: a value under HKCU\\...\\CurrentVersion\\Run."""

import sys

assert sys.platform == "win32"  # pylint: disable=wrong-import-position

# pylint: disable=wrong-import-position,wrong-import-order
import subprocess
import winreg
from collections.abc import Sequence

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "wallpaper_py"


class RunKeyAutostart:
    """
    The per-user Run key, so no admin rights are needed. `key` is
    injectable so tests never touch the real Run key.

    Task Manager's Startup tab can disable the entry without removing it;
    `enabled()` then still reports True.
    """

    def __init__(self, key: str = RUN_KEY, value_name: str = VALUE_NAME) -> None:
        self._key = key
        self._value_name = value_name

    def enabled(self) -> bool:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._key) as key:
                winreg.QueryValueEx(key, self._value_name)
        except FileNotFoundError:
            return False
        return True

    def enable(self, command: Sequence[str]) -> None:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, self._key, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(
                key,
                self._value_name,
                0,
                winreg.REG_SZ,
                subprocess.list2cmdline(command),
            )

    def disable(self) -> None:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, self._key, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, self._value_name)
        except FileNotFoundError:
            pass
