"""In-memory Backend for tests; behaves like the real ones on the edge cases."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from wallpaper_py.core.model import (
    ImageMode,
    Monitor,
    MonitorNotAttachedError,
    Rectangle,
    UnsupportedModeError,
)

GUID = "{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}"


def make_monitor(
    name: str,
    x: int = 0,
    size: tuple[int, int] = (1920, 1080),
    attached: bool = True,
    pnp: str | None = None,
) -> Monitor:
    rect = Rectangle(x, 0, x + size[0], size[1]) if attached else None
    return Monitor(
        id=f"\\\\?\\DISPLAY#{pnp or name.upper()}#5&48925cd&0&UID1#{GUID}",
        name=name,
        rect=rect,
    )


@dataclass(frozen=True)
class SetCall:
    monitor_id: str
    image: Path
    mode: ImageMode | None


class FakeBackend:
    def __init__(
        self,
        monitors: Iterable[Monitor],
        per_monitor_modes: bool = False,
        global_mode: ImageMode | None = ImageMode.FILL,
    ) -> None:
        self._monitors = list(monitors)
        self._per_monitor_modes = per_monitor_modes
        self._global_mode = global_mode
        self.set_calls: list[SetCall] = []
        self.global_mode_calls: list[ImageMode] = []
        self.monitors_calls = 0
        # Called with the call number before each monitors() result, so a
        # test can detach a monitor "while" the code under test is working.
        self.before_monitors: Callable[[int], None] | None = None

    @property
    def per_monitor_modes(self) -> bool:
        return self._per_monitor_modes

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

    def set_wallpaper(
        self, monitor_id: str, image: Path, mode: ImageMode | None = None
    ) -> None:
        if mode is not None and not self._per_monitor_modes:
            raise UnsupportedModeError("global modes only")
        monitor = next((m for m in self._monitors if m.id == monitor_id), None)
        if monitor is None or not monitor.attached:
            raise MonitorNotAttachedError(monitor_id)
        self.set_calls.append(SetCall(monitor_id, image, mode))
        self.update(monitor.name, wallpaper=image)

    def set_global_mode(self, mode: ImageMode) -> None:
        self.global_mode_calls.append(mode)
        self._global_mode = mode

    def global_mode(self) -> ImageMode | None:
        return self._global_mode
