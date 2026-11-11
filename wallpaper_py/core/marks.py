"""
Favourites and to-remove lists: plain .txt files, one full path per line.

The file is the only copy (nothing in SQLite), so people and scripts can use
it directly: edit it by hand, or let a library script read the to-remove
list, delete the files properly and clear the list.

- Every change re-reads the file, applies that one add / remove, and writes
  it back atomically (temp file + os.replace). Other lines, including
  comments and blank lines, are kept as they are.
- Reads are cached and reloaded when the file's mtime or size changes, so
  external edits are picked up.
- Entries whose image no longer exists are *never* dropped automatically;
  callers hide them where needed. Clearing is always explicit.
"""

from __future__ import annotations

import os
from pathlib import Path

from .library import path_key


class MarkFile:
    def __init__(self, file: Path) -> None:
        self.file = file
        self._signature: tuple[int, int] | None = None
        self._paths: list[Path] = []

    def paths(self) -> list[Path]:
        """Entries in file order (reloaded if the file changed)."""
        signature = _signature(self.file)
        if signature is None:
            self._signature, self._paths = None, []
        elif signature != self._signature:
            self._paths = parse_lines(_read_lines(self.file))
            self._signature = signature
        return list(self._paths)

    def keys(self) -> set[str]:
        """path_key of every entry, for membership checks."""
        return {path_key(p) for p in self.paths()}

    def __contains__(self, path: Path) -> bool:
        return path_key(path) in self.keys()

    def add(self, path: Path) -> bool:
        """Append `path`; False if it was already listed."""
        lines = _read_lines(self.file)
        if path_key(path) in {path_key(p) for p in parse_lines(lines)}:
            return False
        self._write([*lines, str(path)])
        return True

    def discard(self, path: Path) -> bool:
        """Remove every line naming `path`; False if it wasn't listed."""
        lines = _read_lines(self.file)
        key = path_key(path)
        kept = [line for line in lines if _key_of_line(line) != key]
        if len(kept) == len(lines):
            return False
        self._write(kept)
        return True

    def _write(self, lines: list[str]) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.file.with_name(self.file.name + ".tmp")
        text = "".join(f"{line}\n" for line in lines)
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, self.file)
        self._signature = None  # reload on the next read


def parse_lines(lines: list[str]) -> list[Path]:
    """
    Paths in a list file: blank lines and lines starting with '#' are
    ignored, as are surrounding quotes (Explorer's "Copy as path").
    """
    return [Path(text) for line in lines if (text := _entry(line))]


def _entry(line: str) -> str:
    text = line.strip().strip('"').strip()
    return "" if text.startswith("#") else text


def _key_of_line(line: str) -> str | None:
    text = _entry(line)
    return path_key(text) if text else None


def _read_lines(file: Path) -> list[str]:
    try:
        return file.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return []


def _signature(file: Path) -> tuple[int, int] | None:
    try:
        stat = file.stat()
    except FileNotFoundError:
        return None
    return stat.st_mtime_ns, stat.st_size
