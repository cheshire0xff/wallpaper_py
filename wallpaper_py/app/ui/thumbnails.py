"""
Thumbnails and image details for the windows, read from the files (never
over the socket), off the UI thread.

`ThumbnailLoader.want(paths)` says which images the window shows now: the
ones not cached yet are decoded on a small `QThreadPool` of its own,
`loaded(path)` fires when one is ready, and `get(path)` returns it. A queued
job for an image that is no longer wanted skips its decode, and a result
that arrives for one anyway is dropped, so a stale image never lands on a
card. (Jobs are never taken back out of the pool: the pool deletes a job
once it has run, so its Python handle may be dead by then.)

Each job reads the header for the dimensions (`QImageReader.size()`, no
full decode) and the file size, then decodes at a reduced size
(`QImageReader.setScaledSize`; JPEG decodes scaled down directly), so a 4K
image never stalls the window. Workers make a `QImage` (safe off the UI
thread); the `QPixmap` is made on the UI thread when the result arrives.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QImage, QImageIOHandler, QImageReader, QPixmap

log = logging.getLogger(__name__)

CACHE_ENTRIES = 64  # Home shows one image per playlist
THREADS = 2


@dataclass(frozen=True)
class ImageInfo:
    width: int  # as displayed (after the EXIF orientation)
    height: int
    file_size: int  # bytes


@dataclass(frozen=True)
class Decoded:
    """What a worker made of one file (no QPixmap: those are UI-thread only)."""

    info: ImageInfo | None
    image: QImage  # null if it could not be decoded
    error: str | None = None


@dataclass(frozen=True)
class Thumbnail:
    """A loaded image, on the UI thread."""

    info: ImageInfo | None
    pixmap: QPixmap | None  # None: could not be decoded (see error)
    error: str | None = None


# Runs on a worker thread: (file, bounding box in device pixels) -> result.
Decoder = Callable[[Path, QSize], Decoded]


def read_thumbnail(path: Path, box: QSize) -> Decoded:
    """Dimensions and file size from the header, then a scaled-down decode."""
    try:
        file_size = path.stat().st_size
    except OSError as err:
        return Decoded(None, QImage(), err.strerror or str(err))
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    stored = reader.size()  # header only
    if not stored.isValid():
        return Decoded(None, QImage(), reader.errorString())
    turned = bool(
        reader.transformation() & QImageIOHandler.Transformation.TransformationRotate90
    )
    shown = stored.transposed() if turned else stored
    info = ImageInfo(shown.width(), shown.height(), file_size)
    # The scaled size applies before the orientation is, so in stored terms.
    stored_box = box.transposed() if turned else box
    if stored.width() > stored_box.width() or stored.height() > stored_box.height():
        reader.setScaledSize(
            stored.scaled(stored_box, Qt.AspectRatioMode.KeepAspectRatio)
        )
    image = reader.read()
    if image.isNull():
        return Decoded(info, QImage(), reader.errorString())
    return Decoded(info, image)


class _Relay(QObject):
    """Lives on the UI thread; workers emit on it, so delivery is queued."""

    done = Signal(object, object)  # Path, Decoded


class _Wanted:
    """
    The paths wanted now, shared with the workers: replaced whole (one
    attribute assignment, atomic under the GIL), never changed in place.
    """

    def __init__(self) -> None:
        self.paths: frozenset[Path] = frozenset()


class _Job(QRunnable):
    # pylint: disable=too-many-arguments
    def __init__(
        self, path: Path, box: QSize, decode: Decoder, relay: _Relay, wanted: _Wanted
    ):
        super().__init__()
        self.path = path
        self._box = box
        self._decode = decode
        self._relay = relay
        self._wanted = wanted

    def run(self) -> None:
        if self.path not in self._wanted.paths:
            return  # no longer wanted while it waited in the queue
        try:
            result = self._decode(self.path, self._box)
        except Exception as err:  # pylint: disable=broad-exception-caught
            log.exception("Reading %s failed", self.path)
            result = Decoded(None, QImage(), str(err))
        self._relay.done.emit(self.path, result)


class ThumbnailLoader(QObject):
    """See the module docstring. `close()` before the owner goes away."""

    # pylint: disable=too-many-instance-attributes

    loaded = Signal(object)  # Path: get(path) has it now

    def __init__(
        self,
        box: QSize,
        decode: Decoder = read_thumbnail,
        *,
        threads: int = THREADS,
        cache_entries: int = CACHE_ENTRIES,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._box = QSize(box)
        self._decode = decode
        self._cache_entries = cache_entries
        self._cache: OrderedDict[Path, Thumbnail] = OrderedDict()
        self._pending: set[Path] = set()  # wanted, queued or running
        self._wanted = _Wanted()
        # The pool first: as children go in creation order, it is deleted
        # (waiting for running jobs) before the relay they emit on.
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(threads)
        self._relay = _Relay(self)
        self._relay.done.connect(self._done)
        self._closed = False

    def box(self) -> QSize:
        return QSize(self._box)

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
                job = _Job(path, self._box, self._decode, self._relay, self._wanted)
                self._pool.start(job)

    def close(self) -> None:
        """Drop queued jobs and wait for running ones (a decode is short)."""
        self._closed = True
        self._pending.clear()
        self._wanted.paths = frozenset()
        self._pool.clear()
        self._pool.waitForDone()

    @Slot(object, object)
    def _done(self, path: Path, result: Decoded) -> None:
        if self._closed or path not in self._pending:
            return  # no longer wanted: a stale result
        self._pending.discard(path)
        pixmap = None if result.image.isNull() else QPixmap.fromImage(result.image)
        self._cache[path] = Thumbnail(result.info, pixmap, result.error)
        while len(self._cache) > self._cache_entries:
            self._cache.popitem(last=False)
        self.loaded.emit(path)
