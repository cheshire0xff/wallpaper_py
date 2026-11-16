"""
The library window's thumbnails on disk (`paths.thumbnail_dir()`): one JPEG
per image, filling a tile's size in device pixels (cropped to the middle,
`imaging.load_filled`), so reopening the window doesn't decode every
image again.

A file is named by a hash of the image's path, mtime and size, the tile
size and how the image is made (`STYLE`): an edited image, a new tile
size or another style gets a new file, and the old one is left for
`prune`. Files are written whole (to a temporary name,
then renamed), so a reader never sees half a thumbnail; two threads making
the same one at once is harmless (the second rename loses, or wins with
the same content).

Written and read only by the UI process (the grid's threads and the
pre-generation thread); the app never makes thumbnails. Qt-free.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from collections.abc import Collection
from pathlib import Path

from PIL import Image

from .imaging import load_filled
from .model import ImageLoadError

log = logging.getLogger(__name__)

SUFFIX = ".jpg"
QUALITY = 88  # JPEG: no visible loss at tile size, ~15 KB a tile
_TEMPORARY = ".tmp"
# How a thumbnail is made, in its name: thumbnails made another way (the
# image fitted, before) are never taken for these, and get pruned.
STYLE = "fill"


class ThumbnailCache:
    """Thumbnails of `size` (device pixels) in `directory`."""

    def __init__(self, directory: Path, size: tuple[int, int]) -> None:
        self.directory = directory
        self.size = size

    def file_for(self, image: Path) -> Path:
        """
        Where `image`'s thumbnail is (or would be) kept.

        Raises:
            OSError: `image` can't be looked at (gone, or not allowed).
        """
        stat = os.stat(image)  # of what a symlink points to: its content
        width, height = self.size
        key = f"{image}\0{stat.st_mtime_ns}\0{stat.st_size}\0{width}x{height}\0{STYLE}"
        digest = hashlib.sha1(key.encode("utf-8", "surrogatepass")).hexdigest()
        return self.directory / digest[:2] / f"{digest}{SUFFIX}"

    def thumbnail(self, image: Path) -> Image.Image:
        """
        `image`'s thumbnail: from the disk if it is there, else decoded
        and saved.

        Raises:
            OSError: `image` can't be looked at (FileNotFoundError: gone).
            ImageLoadError: it can't be decoded.
        """
        file = self.file_for(image)
        cached = _read(file)
        if cached is not None:
            return cached
        made = load_filled(image, self.size)
        self._save(made, file)
        return made

    def ensure(self, image: Path) -> Path | None:
        """
        Make `image`'s thumbnail unless the disk has it (it isn't read).
        The file it is kept in, or None if `image` can't be looked at. An
        image that can't be decoded gets no file (and is logged).
        """
        try:
            file = self.file_for(image)
        except OSError:
            return None
        if not file.exists():
            try:
                self._save(load_filled(image, self.size), file)
            except ImageLoadError as err:
                log.info("No thumbnail: %s", err)
        return file

    def prune(self, keep: Collection[Path], older_than: float) -> int:
        """
        Delete the files not in `keep` that were last written before
        `older_than` (a timestamp: what was made since may be for an image
        that appeared meanwhile). The number deleted.
        """
        names = {file.name for file in keep}
        deleted = 0
        try:
            folders = [f for f in self.directory.iterdir() if f.is_dir()]
        except OSError:
            return 0  # no cache yet
        for folder in folders:
            try:
                files = list(folder.iterdir())
            except OSError:
                continue
            for file in files:
                if file.name in names:
                    continue
                try:
                    if file.stat().st_mtime < older_than:
                        file.unlink()
                        deleted += 1
                except OSError:
                    continue  # gone meanwhile, or open (Windows): next time
        return deleted

    def _save(self, image: Image.Image, file: Path) -> None:
        """Written whole; a failure is logged (the thumbnail is still shown)."""
        try:
            file.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(
                suffix=_TEMPORARY, prefix=file.stem, dir=file.parent
            )
        except OSError as err:
            log.warning("Can't save a thumbnail in %s: %s", file.parent, err)
            return
        try:
            with os.fdopen(handle, "wb") as out:
                image.save(out, "JPEG", quality=QUALITY)
            os.replace(temporary, file)
        except OSError as err:  # e.g. the file is being read (Windows)
            log.debug("Thumbnail %s not saved: %s", file, err)
            Path(temporary).unlink(missing_ok=True)


def _read(file: Path) -> Image.Image | None:
    """A saved thumbnail, or None if there is none (or it doesn't read)."""
    try:
        with Image.open(file) as img:
            return img.convert("RGB")
    except FileNotFoundError:
        return None
    except (OSError, SyntaxError, ValueError) as err:
        log.info("Thumbnail %s doesn't read, made again: %s", file, err)
        return None
