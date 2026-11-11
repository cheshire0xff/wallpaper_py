"""Find the images in a playlist directory, and compare paths."""

from __future__ import annotations

import os
import re
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
    symlinked directories are not followed. A missing or unreadable
    directory gives an empty list: the drive may simply not be mounted yet.
    """
    found: list[Path] = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if os.path.splitext(name)[1].lower() in IMAGE_SUFFIXES:
                found.append(Path(root, name))
    found.sort(key=natural_key)
    return found


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
