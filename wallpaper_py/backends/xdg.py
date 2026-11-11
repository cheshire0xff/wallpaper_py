"""
Start at login on Linux desktops: an XDG autostart entry
(~/.config/autostart/wallpaper_py.desktop), honoured by KDE Plasma, GNOME
and others.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path

FILE_NAME = "wallpaper_py.desktop"

# Characters that never need quoting in an Exec argument.
_PLAIN = re.compile(r"[A-Za-z0-9_@+=:,./-]+")


def autostart_dir() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(config_home) / "autostart"


def exec_line(command: Sequence[str]) -> str:
    """
    argv -> the Exec value, per the Desktop Entry spec: arguments with
    special characters are double-quoted with `"`, `` ` ``, `$` and `\\`
    backslash-escaped; then the value's own escaping doubles backslashes,
    and `%` (field codes) is doubled.
    """

    def quote(arg: str) -> str:
        if _PLAIN.fullmatch(arg):
            return arg
        return '"' + re.sub(r'(["`$\\])', r"\\\1", arg) + '"'

    line = " ".join(quote(arg) for arg in command)
    return line.replace("\\", "\\\\").replace("%", "%%")


class XdgAutostart:
    def __init__(self, directory: Path | None = None) -> None:
        self.file = (directory or autostart_dir()) / FILE_NAME

    def enabled(self) -> bool:
        return self.file.is_file()

    def enable(self, command: Sequence[str]) -> None:
        text = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=wallpaper_py\n"
            "Comment=Per-monitor wallpaper playlists\n"
            f"Exec={exec_line(command)}\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        self.file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.file.with_name(self.file.name + ".tmp")
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, self.file)

    def disable(self) -> None:
        self.file.unlink(missing_ok=True)
