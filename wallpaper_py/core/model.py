"""Platform-independent data model shared by backends, core logic, CLI and GUI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, final, override

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema

# Seconds since the epoch (time.time); injected so tests control time.
Clock = Callable[[], float]


@final
@dataclass(frozen=True, eq=False)
class MonitorId:
    """
    A monitor's id, as some API spells it.

    Windows spells one id in different cases in different APIs (the
    wallpaper COM interface, DisplayConfig), so ids are equal, and hash,
    case-insensitively: plain `==`, `in` and dict keys are right. `str()`
    gives the spelling it was made from, for the backend and for display.

    In pydantic models (the config file, the protocol) it is a plain
    string, read and written.

    The one place an id is normalized: surrounding whitespace goes, so every
    reader of the config (the app, the settings window) gets the same id.
    """

    text: str
    # The case-folded form: what equality goes by (also a storage key).
    # Computed once: ids are dict keys on every tick and paint.
    key: str = field(init=False)

    def __post_init__(self) -> None:
        text = self.text.strip()
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "key", text.casefold())

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MonitorId):
            return NotImplemented
        return self.key == other.key

    @override
    def __hash__(self) -> int:
        return hash(self.key)

    @override
    def __str__(self) -> str:
        return self.text

    @override
    def __repr__(self) -> str:
        return f"MonitorId({self.text!r})"

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source: Any, _handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        # Strict: a number or `true` in config.toml is not an id.
        from_text = core_schema.no_info_after_validator_function(
            cls, core_schema.str_schema(strict=True)
        )
        return core_schema.json_or_python_schema(
            json_schema=from_text,
            python_schema=core_schema.union_schema(
                [core_schema.is_instance_schema(cls), from_text]
            ),
            serialization=core_schema.plain_serializer_function_ser_schema(str),
        )


@dataclass(frozen=True)
class Rectangle:
    """Monitor area in physical pixels, in desktop coordinates."""

    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height


class ImageMode(Enum):
    FILL = "fill"
    FIT = "fit"
    STRETCH = "stretch"


@dataclass(frozen=True)
class Monitor:
    """
    One physical monitor.

    `id` is the only stable identity: it survives the monitor being switched
    off and on, while index and position do not (on Windows, switching the
    monitor off re-indexes and moves the remaining ones).
    `rect` is None while the monitor is known but detached.
    """

    id: MonitorId
    name: str
    rect: Rectangle | None
    wallpaper: Path | None = None

    @property
    def attached(self) -> bool:
        return self.rect is not None


class AppError(Exception):
    """
    An expected failure, with a message for the user. Catch it only where
    any of them ends what runs (the CLI, opening the service); elsewhere
    catch one of the two kinds:

    - WallpaperError: one operation failed; the rest goes on.
    - config.ConfigError: the config, or a file it names, can't be used;
      whatever runs on it stops (the service, until a reload).
    """


class WallpaperError(AppError):
    """One operation failed (backends and core); the rest goes on."""


class MonitorNotAttachedError(WallpaperError):
    """The target monitor is unknown or currently detached; nothing was set."""


class UnsupportedModeError(WallpaperError):
    """The backend cannot apply the requested image mode this way."""


class UnsupportedPlatformError(WallpaperError):
    """No backend exists for the current desktop environment."""


class ImageLoadError(WallpaperError):
    """The image file is missing, unreadable or not an image."""
