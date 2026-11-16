"""
Find the images in a playlist directory, and compare paths. The daemon scans
with `scan` (cached by `Library`); the library window with `scan_files`,
which also says when each image was added, and its thumbnail
pre-generation with `walk` (stopped at any image).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .model import Clock

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".webp"})

_DIGITS = re.compile(r"(\d+)")


def natural_key(path: Path | str) -> tuple[str | int, ...]:
    """Sort key where 'img2' comes before 'img10', ignoring case."""
    parts = _DIGITS.split(str(path).casefold())
    # split() puts the digit runs at odd indices, so tuples compare str with
    # str and int with int.
    return tuple(int(p) if i % 2 else p for i, p in enumerate(parts))


def path_key(path: Path | str) -> str:
    """
    Identity of a path for comparisons: absolute, normalised, and lower-case
    on Windows. Used for paths typed by people (favourites / to-remove files),
    which may not match the scanned spelling exactly.
    """
    return os.path.normcase(os.path.abspath(path))


def scan(directory: Path) -> list[Path]:
    """
    All images under `directory`, recursively, in natural order.

    Symlinked files are included (they may point to upscaled versions);
    symlinked directories are not followed, nor hidden ones (".name").
    A missing or unreadable directory gives an empty list: the drive may
    simply not be mounted yet.
    """
    found = list(walk(directory))
    found.sort(key=natural_key)
    return found


def walk(directory: Path) -> Iterator[Path]:
    """`scan`'s images as they are found (unsorted): one folder listed at a time."""
    for entry in _image_entries(directory):
        yield Path(entry.path)


@dataclass(frozen=True)
class ImageFile:
    path: Path
    # When it came into the library, as a timestamp: its creation time
    # where the system records one (Windows), else its mtime (Linux). Of a
    # symlink itself, not of what it points to: when it was linked in.
    added: float


def scan_files(directory: Path) -> list[ImageFile]:
    """
    `scan`, with each image's `added` time (for sorting by it). On Windows
    the times come with the directory listing; elsewhere it is one `lstat`
    per image. An image that vanishes meanwhile is left out.
    """
    found: list[ImageFile] = []
    for entry in _image_entries(directory):
        try:
            stat = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        added = getattr(stat, "st_birthtime", None)  # not recorded on Linux
        found.append(
            ImageFile(Path(entry.path), stat.st_mtime if added is None else added)
        )
    found.sort(key=lambda f: natural_key(f.path))
    return found


def _image_entries(directory: Path) -> Iterator[os.DirEntry[str]]:
    """The image files under `directory`, as `os.walk` finds them."""
    pending = [os.fspath(directory)]
    while pending:
        try:
            with os.scandir(pending.pop()) as entries:
                listed = list(entries)
        except OSError:  # missing, not a directory, or not allowed
            continue
        for entry in listed:
            if _is_dir(entry):
                if not entry.name.startswith(".") and not _is_symlink(entry):
                    pending.append(entry.path)
            elif os.path.splitext(entry.name)[1].lower() in IMAGE_SUFFIXES:
                yield entry


def _is_dir(entry: os.DirEntry[str]) -> bool:
    """A directory, or a symlink to one (never followed: see `scan`)."""
    try:
        return entry.is_dir()
    except OSError:
        return False


def _is_symlink(entry: os.DirEntry[str]) -> bool:
    try:
        return entry.is_symlink()
    except OSError:
        return False


class Library:
    """
    Cached directory scans. A 20k-image directory takes a noticeable moment to
    walk, and is only needed when a new image is drawn, so a scan is reused
    for `max_age` seconds. New files join the rotation after that.
    """

    def __init__(self, clock: Clock, max_age: float = 300.0) -> None:
        self._clock = clock
        self._max_age = max_age
        self._scans: dict[Path, tuple[float, list[Path]]] = {}

    def images(self, directory: Path, refresh: bool = False) -> list[Path]:
        now = self._clock()
        cached = self._scans.get(directory)
        if refresh or cached is None or not 0 <= now - cached[0] < self._max_age:
            cached = (now, scan(directory))
            self._scans[directory] = cached
        return cached[1]

    def age(self, directory: Path) -> float:
        """Seconds since `directory` was scanned (0: never, or just now)."""
        cached = self._scans.get(directory)
        return 0.0 if cached is None else max(0.0, self._clock() - cached[0])
