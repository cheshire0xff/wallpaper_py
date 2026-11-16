"""
Images for the windows, read from the files (never over the socket), off
the UI thread: per image one small copy (core.imaging.load_small), which
the monitor drawings render their previews from (arrangement.py), a
playlist row's thumbnail made from it, and the image's details.

`ThumbnailLoader.want(paths)` says which images the window shows now: the
ones not cached yet are decoded on a small thread pool of its own (`Jobs`,
jobs.py), `loaded(path)` fires when one is ready, and `get(path)` returns
it. A queued job for an image that is no longer wanted skips its decode,
and a result that arrives for one anyway is dropped, so a stale image
never lands on a card. The cache
never drops a wanted image, and keeps a few others (the image before,
for *Previous*).

Each job decodes once with Pillow, at a reduced size (JPEGs decode scaled
down directly), so a 4K image never stalls the window: the small copy
covers a square of the preview's longer side (`Sizes.side`), enough for a
preview of any screen shape and look. Workers make a `QImage` of the
thumbnail (safe off the UI thread); the `QPixmap` is made on the UI thread
when the result arrives. The window's pages share one loader (they show
the same images: each playlist's current one).
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from PIL import Image
from PySide6.QtCore import QObject, QSize, Signal, Slot
from PySide6.QtGui import QGuiApplication, QImage, QPixmap

from ...core.imaging import image_size, load_small
from ...core.model import ImageLoadError
from .jobs import Jobs

log = logging.getLogger(__name__)

CACHE_ENTRIES = 8  # besides the wanted ones, which always stay
THREADS = 2
PREVIEW_SIDE = 480  # logical px: the longer side of a monitor's preview
ROW_THUMBNAIL = QSize(176, 99)  # logical px: a playlist row's image


@dataclass(frozen=True)
class Sizes:
    """What a job makes, in device pixels."""

    side: int  # the small copy covers side x side (the previews' longer side)
    thumbnail: tuple[int, int]  # the row's thumbnail fits in this

    @classmethod
    def at(cls, ratio: float) -> Sizes:
        """For a display scale of `ratio` (devicePixelRatio)."""
        row = ROW_THUMBNAIL * ratio
        return cls(round(PREVIEW_SIDE * ratio), (row.width(), row.height()))

    @classmethod
    def sharpest(cls) -> Sizes:
        """For the screen with the highest scale (`sharpest_ratio`)."""
        return cls.at(sharpest_ratio())


def sharpest_ratio() -> float:
    """
    The highest display scale of the screens: images made for it stay sharp
    on any screen the window is moved to, whichever it opened on.
    """
    return max((s.devicePixelRatio() for s in QGuiApplication.screens()), default=1.0)


@dataclass(frozen=True)
class ImageInfo:
    width: int  # as displayed (after the EXIF orientation)
    height: int
    file_size: int  # bytes


@dataclass(frozen=True)
class Decoded:
    """What a worker made of one file (no QPixmap: those are UI-thread only)."""

    info: ImageInfo | None
    small: Image.Image | None  # the previews' source; None: not decoded (error)
    thumbnail: QImage  # null if not decoded
    error: str | None = None


@dataclass(frozen=True)
class Thumbnail:
    """A loaded image, on the UI thread."""

    info: ImageInfo | None
    pixmap: QPixmap | None  # the row's; None: could not be decoded (see error)
    error: str | None = None
    small: Image.Image | None = None  # the previews' source (read only)


# Runs on a worker thread: (file, sizes) -> result.
Decoder = Callable[[Path, Sizes], Decoded]


def to_qimage(image: Image.Image) -> QImage:
    """An RGB Pillow image as a QImage of its own (no shared buffer)."""
    data = image.tobytes("raw", "RGB")
    width, height = image.size
    view = QImage(data, width, height, 3 * width, QImage.Format.Format_RGB888)
    return view.copy()  # `view` reads `data`, which goes with this frame


def read_image(path: Path, sizes: Sizes) -> Decoded:
    """
    The file size, then one decode: the small copy and the thumbnail (if
    the decode fails, the details still, from the header).
    """
    try:
        file_size = path.stat().st_size
    except OSError as err:
        return Decoded(None, None, QImage(), err.strerror or str(err))
    try:
        small = load_small(path, sizes.side)
    except ImageLoadError as err:
        cause = err.__cause__
        return Decoded(_header_info(path, file_size), None, QImage(), str(cause or err))
    thumbnail = small.image.copy()
    thumbnail.thumbnail(sizes.thumbnail, Image.Resampling.LANCZOS, reducing_gap=3.0)
    info = ImageInfo(small.size[0], small.size[1], file_size)
    return Decoded(info, small.image, to_qimage(thumbnail))


def _header_info(path: Path, file_size: int) -> ImageInfo | None:
    """The details of a file that fails to decode, if its header reads."""
    try:
        width, height = image_size(path)
    except ImageLoadError:
        return None
    return ImageInfo(width, height, file_size)


class _Wanted:
    """
    The paths wanted now, shared with the workers: replaced whole (one
    attribute assignment, atomic under the GIL), never changed in place.
    """

    def __init__(self) -> None:
        self.paths: frozenset[Path] = frozenset()


class _Read:
    """A job's work: one image, unless it is no longer wanted (None)."""

    def __init__(self, path: Path, sizes: Sizes, decode: Decoder, wanted: _Wanted):
        self._path = path
        self._sizes = sizes
        self._decode = decode
        self._wanted = wanted

    def __call__(self) -> Decoded | None:
        if self._path not in self._wanted.paths:
            return None  # no longer wanted while it waited in the queue
        try:
            return self._decode(self._path, self._sizes)
        except Exception as err:  # pylint: disable=broad-exception-caught
            log.exception("Reading %s failed", self._path)
            return Decoded(None, None, QImage(), str(err))


class ThumbnailLoader(QObject):
    """See the module docstring. `close()` before the owner goes away."""

    # pylint: disable=too-many-instance-attributes

    loaded = Signal(object)  # Path: get(path) has it now

    def __init__(
        self,
        sizes: Sizes,
        decode: Decoder = read_image,
        *,
        threads: int = THREADS,
        cache_entries: int = CACHE_ENTRIES,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._sizes = sizes
        self._decode = decode
        self._cache_entries = cache_entries
        self._cache: OrderedDict[Path, Thumbnail] = OrderedDict()
        self._pending: set[Path] = set()  # wanted, queued or running
        self._wanted = _Wanted()
        self._jobs = Jobs(threads, parent=self)
        self._jobs.done.connect(self._done)
        self._closed = False

    def sizes(self) -> Sizes:
        return self._sizes

    def get(self, path: Path) -> Thumbnail | None:
        """The loaded image, or None if it isn't loaded (yet)."""
        thumbnail = self._cache.get(path)
        if thumbnail is not None:
            self._cache.move_to_end(path)
        return thumbnail

    def pending(self) -> set[Path]:
        return set(self._pending)

    def want(self, paths: Iterable[Path]) -> None:
        """Load these (unless cached); forget queued work for any other."""
        if self._closed:
            return
        wanted = dict.fromkeys(paths)  # a set, in the caller's order
        self._wanted.paths = frozenset(wanted)
        # A queued job for any other skips its decode; a running one's
        # result is dropped in `_done`.
        self._pending &= self._wanted.paths
        for path in wanted:
            if path not in self._cache and path not in self._pending:
                self._pending.add(path)
                read = _Read(path, self._sizes, self._decode, self._wanted)
                self._jobs.run(path, read)

    def close(self) -> None:
        """Drop queued jobs and wait for running ones (a decode is short)."""
        self._closed = True
        self._pending.clear()
        self._wanted.paths = frozenset()
        self._jobs.close()

    @Slot(object, object)
    def _done(self, path: Path, result: Decoded | None) -> None:
        if result is None or self._closed or path not in self._pending:
            return  # skipped, or no longer wanted: a stale result
        self._pending.discard(path)
        image = result.thumbnail
        pixmap = None if image.isNull() else QPixmap.fromImage(image)
        self._cache[path] = Thumbnail(result.info, pixmap, result.error, result.small)
        self._trim()
        self.loaded.emit(path)

    def _trim(self) -> None:
        """Drop the least recently used images beyond the budget, never a wanted one."""
        spare = [p for p in self._cache if p not in self._wanted.paths]
        for path in spare[: max(0, len(spare) - self._cache_entries)]:
            del self._cache[path]
