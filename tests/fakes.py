"""In-memory Backend for tests; behaves like the real ones on the edge cases."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from PIL import Image

from wallpaper_py.core.model import (
    Monitor,
    MonitorId,
    MonitorNotAttachedError,
    Rectangle,
)
from wallpaper_py.core.settings import PlaylistSettings, Settings
from wallpaper_py.core.store import Store

GUID = "{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}"


def fake_id(name: str, pnp: str | None = None) -> str:
    """The id `make_monitor` gives a monitor (what a config holds)."""
    return f"\\\\?\\DISPLAY#{pnp or name.upper()}#5&48925cd&0&UID1#{GUID}"


def make_monitor(
    name: str,
    x: int = 0,
    size: tuple[int, int] = (1920, 1080),
    attached: bool = True,
    pnp: str | None = None,
) -> Monitor:
    rect = Rectangle(x, 0, x + size[0], size[1]) if attached else None
    return Monitor(id=MonitorId(fake_id(name, pnp)), name=name, rect=rect)


@dataclass(frozen=True)
class SetCall:
    monitor_id: MonitorId
    image: Path  # what the backend got: a render
    source: Path | None  # the image it was rendered from (see make_images)


class FakeBackend:
    def __init__(self, monitors: Iterable[Monitor]) -> None:
        self._monitors = list(monitors)
        self.set_calls: list[SetCall] = []
        self.monitors_calls = 0
        # Called with the call number before each monitors() result, so a
        # test can detach a monitor "while" the code under test is working.
        self.before_monitors: Callable[[int], None] | None = None

    def monitors(self) -> list[Monitor]:
        self.monitors_calls += 1
        if self.before_monitors is not None:
            self.before_monitors(self.monitors_calls)
        return list(self._monitors)

    def update(self, name: str, **changes: object) -> None:
        self._monitors = [
            replace(m, **changes) if m.name == name else m  # type: ignore[arg-type]
            for m in self._monitors
        ]

    def detach(self, name: str) -> None:
        self.update(name, rect=None)

    def unlist(self, name: str) -> Monitor:
        """Drop a monitor from the list (Windows does this for a while)."""
        monitor = next(m for m in self._monitors if m.name == name)
        self._monitors.remove(monitor)
        return monitor

    def relist(self, monitor: Monitor) -> None:
        self._monitors.append(monitor)

    def set_wallpaper(self, monitor_id: MonitorId, image: Path) -> None:
        monitor = next((m for m in self._monitors if m.id == monitor_id), None)
        if monitor is None or not monitor.attached:
            raise MonitorNotAttachedError(str(monitor_id))
        self.set_calls.append(SetCall(monitor_id, image, source_of(image)))
        self.update(monitor.name, wallpaper=image)

    def source(self, name: str) -> Path | None:
        """The image the monitor's render was made from, if it has one."""
        found = next(m for m in self._monitors if m.name == name)
        return None if found.wallpaper is None else source_of(found.wallpaper)


class FakeAutostart:
    def __init__(self) -> None:
        self.command: list[str] | None = None

    def enabled(self) -> bool:
        return self.command is not None

    def enable(self, command: Sequence[str]) -> None:
        self.command = list(command)

    def disable(self) -> None:
        self.command = None


class FakeClock:
    """Manual clock: `advance()` moves time on."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakePresence:
    def __init__(self) -> None:
        self.locked: bool | None = False
        self.idle: float | None = 0.0

    def session_locked(self) -> bool | None:
        return self.locked

    def idle_seconds(self) -> float | None:
        return self.idle


def add_playlists(store: Store, *ids: str) -> Store:
    """
    Playlists with these ids in the settings (a playlist's state goes in
    its row, so a test of the state needs one).
    """
    playlists = tuple(
        PlaylistSettings(id=i, name=f"playlist {i}", directory=Path(i)) for i in ids
    )
    store.save_settings(Settings(playlists=playlists), revision=None)
    return store


def toml_ids(*names: str) -> str:
    """The `monitors` of a config: the ids of fake monitors, as a TOML array."""
    return "[" + ", ".join(f"'{fake_id(name)}'" for name in names) + "]"


def write_config(directory: Path) -> Path:
    """A config with one sorted playlist "walls" of a.jpg, b.jpg, c.jpg on Main."""
    make_images(directory / "walls", "a.jpg", "b.jpg", "c.jpg")
    config = directory / "config.toml"
    config.write_text(
        "pause_when_idle = false\n"
        "[playlists.walls]\n"
        "id = 'walls-id'\n"
        "directory = 'walls'\n"
        f"monitors = {toml_ids('Main')}\n"
        'order = "sorted"\n'
        'interval = "10m"\n',
        encoding="utf-8",
    )
    return config


# Every image make_images writes has a colour of its own, so the image a
# render was made from can be told from the render (source_of).
_COLOURS: dict[tuple[int, int, int], Path] = {}
_STEPS = 6  # per channel: 216 colours, 40 apart (well above JPEG's error)
_made = [0]  # images made so far: the next colour


def _colour(index: int) -> tuple[int, int, int]:
    def channel(value: int) -> int:
        return 20 + 40 * (value % _STEPS)

    i = index % _STEPS**3
    return channel(i), channel(i // _STEPS), channel(i // _STEPS**2)


def make_images(directory: Path, *names: str) -> list[Path]:
    """
    Tiny images, each in a colour of its own (a name Pillow can't save to,
    such as `.txt`, gets an empty file).
    """
    formats = Image.registered_extensions()
    paths = []
    for name in names:
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        image_format = formats.get(path.suffix.lower())
        if image_format is None:
            path.touch()
        else:
            colour = _colour(_made[0])
            _made[0] += 1
            Image.new("RGB", (8, 6), colour).save(path, image_format)
            _COLOURS[colour] = path
        paths.append(path)
    return paths


def source_of(render: Path) -> Path | None:
    """The make_images image `render` shows (by its centre's colour), if any."""
    with Image.open(render) as img:
        centre = img.convert("RGB").getpixel((img.width // 2, img.height // 2))
    assert isinstance(centre, tuple)
    for colour, path in _COLOURS.items():
        if all(abs(a - b) <= 12 for a, b in zip(colour, centre)):
            return path
    return None


class BusyFile:
    """
    While `busy`, reading `file` fails like a file another program holds
    open (an editor, a virus scan): a PermissionError.
    """

    def __init__(self, file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.file = file
        self.busy = True
        read_bytes = Path.read_bytes

        def fake(path: Path) -> bytes:
            if self.busy and path == self.file:
                raise PermissionError(13, "Permission denied", str(path))
            return read_bytes(path)

        monkeypatch.setattr(Path, "read_bytes", fake)


def settled(file: Path) -> Path:
    """
    `file` as if written long ago: a list file that won't decode is only
    broken once it stopped changing (marks.SETTLE_NS).
    """
    os.utime(file, ns=(1, 1))
    return file
