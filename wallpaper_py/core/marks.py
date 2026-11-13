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
- The files are UTF-8 without a byte order mark, nothing else
  (config.decode_text). One that isn't is a ConfigError: the
  service stops, as for a broken config, and the file is never rewritten.
  Unless it changed in the last few seconds: a script that writes it in
  place (not atomically) may be half-way, cutting a character in two. That
  is MarkFileBusyError (below) until it settles.
- A file that can't be opened (locked by another program, or not allowed)
  is not an error for reads once it has been read: the last list read
  stays until it opens again, and a warning is logged. Before the first
  successful read the list is *unknown*, not empty: reads raise
  MarkFileBusyError until it opens (an empty to-remove list would let
  images listed for removal be shown). A change fails with
  MarkFileBusyError and writes nothing.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from pathlib import Path

from .config import ConfigError, decode_text
from .library import path_key
from .model import WallpaperError

log = logging.getLogger(__name__)

# A file changed this recently that won't decode may still be being written.
SETTLE_NS = 3_000_000_000


class MarkFileBusyError(WallpaperError):
    """The list file can't be read or replaced; nothing was changed."""


class MarkFile:
    def __init__(self, file: Path) -> None:
        self.file = file
        self._signature: tuple[int, int] | None = None
        self._paths: list[Path] | None = None  # None: never read (unknown)
        self._busy = False  # the last read failed; logged once

    def paths(self) -> list[Path]:
        """
        Entries in file order (reloaded if the file changed). While the file
        can't be opened, the last list read.

        Raises:
            ConfigError: the file is not UTF-8.
            MarkFileBusyError: it can't be opened and was never read, so the
                list is unknown (logged once; the next call tries again).
        """
        try:
            signature = _signature(self.file)
            if signature is None:
                self._signature, self._paths, self._busy = None, [], False
                return []
            if signature == self._signature and self._paths is not None:
                return list(self._paths)
            paths = parse_lines(_read_lines(self.file))
        except MarkFileBusyError as err:
            # Keep the last list; the next call tries again.
            if not self._busy:
                log.warning("%s", err)
            self._busy = True
            if self._paths is None:
                raise MarkFileBusyError(
                    f"{err}; the list is unknown until it can be read"
                ) from err
            return list(self._paths)
        self._signature, self._paths, self._busy = signature, paths, False
        return list(paths)

    def keys(self) -> set[str]:
        """
        path_key of every entry, for membership checks.

        Raises:
            ConfigError, MarkFileBusyError: as `paths`.
        """
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
        """
        Raises:
            MarkFileBusyError: the file can't be replaced (another program
                has it open, or not allowed); it is left as it was.
        """
        # Written beside it, then swapped in: os.replace is all or nothing,
        # so a reader (the app, an editor, a script) sees the old list or
        # the new one, never half a file, and a crash midway loses nothing.
        # On Windows the swap fails while another program has the file
        # open without allowing it to be replaced (Excel, a script reading
        # it, a virus scan): PermissionError.
        temporary = self.file.with_name(self.file.name + ".tmp")
        text = "".join(f"{line}\n" for line in lines)
        try:
            # Inside the try: a folder that can't be made is the same
            # failure for the caller (nothing written).
            self.file.parent.mkdir(parents=True, exist_ok=True)
            # "\n" also on Windows: the same file on both systems (dual boot).
            temporary.write_text(text, encoding="utf-8", newline="\n")
            os.replace(temporary, self.file)
        except OSError as err:
            # Don't leave the .tmp beside the list. If even that delete is
            # refused, the error that matters is still the one below.
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
            raise _busy("write", self.file, err) from err
        # Reload on the next read; until it can be read, what was written
        # (not the list before it: a busy file must not undo a remove).
        self._signature, self._paths = None, parse_lines(lines)


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
    """
    Raises:
        ConfigError: the file is not UTF-8, or has a byte order mark.
        MarkFileBusyError: it can't be opened (e.g. locked by an editor), or
            it won't decode but just changed (SETTLE_NS).
    """
    # Bytes first, decoded after: "can't open it now" (keep the last list)
    # and "it is not UTF-8" (a config error) must not look alike.
    try:
        data = file.read_bytes()
    except FileNotFoundError:
        return []
    except OSError as err:
        raise _busy("read", file, err) from err
    try:
        return decode_text(data, file).splitlines()
    except ConfigError as err:
        if not _settled(file):
            raise MarkFileBusyError(
                f"{err} (it just changed: maybe still being written)"
            ) from err
        raise


def _settled(file: Path) -> bool:
    """Unchanged for SETTLE_NS: what it holds now is what was meant."""
    try:
        changed = file.stat().st_mtime_ns
    except OSError:  # gone, or can't be looked at: it is still changing
        return False
    return time.time_ns() - changed >= SETTLE_NS


def _busy(verb: str, file: Path, err: OSError) -> MarkFileBusyError:
    """Any OSError but a missing file: the file can't be used now."""
    return MarkFileBusyError(f"Cannot {verb} {file}{_why(err)}: {err}")


def _why(err: OSError) -> str:
    # On Windows, another program holding the file open looks like this; it
    # may pass. Other errors (a folder, a full disk) won't, so no guess.
    if isinstance(err, PermissionError):
        return " (open in another program, or not allowed)"
    return ""


def _signature(file: Path) -> tuple[int, int] | None:
    """
    None if there is no file.

    Raises:
        MarkFileBusyError: it can't be looked at (e.g. a folder not allowed).
    """
    try:
        stat = file.stat()
    except FileNotFoundError:
        return None
    except OSError as err:
        raise _busy("read", file, err) from err
    return stat.st_mtime_ns, stat.st_size
