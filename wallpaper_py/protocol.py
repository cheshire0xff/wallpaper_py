"""
The app's socket protocol as pydantic models: what goes over the wire, and
nothing else (no logic, no Qt). See docs/protocol.md.

- In: an `Envelope` holding a `Request` (a playlist command) or `Subscribe`.
- Out: a `Reply`, which is data only: one result model per command, tagged
  on `command`, or an `ErrorReply`. The CLI formats its text from these;
  the tray and the UI process show them.
- Out, on a subscription: one `Event` per line, tagged on `event`.

`commands.py` runs requests, `app/ipc.py` carries the lines. The protocol
only ever adds commands, fields, error kinds and events; clients ignore
fields they don't know. A breaking change bumps `PROTOCOL`.

docs/protocol.schema.json is generated from these models
(`scripts/protocol_schema.py`); a test keeps it current.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from .core.config import Order
from .core.model import ImageMode
from .core.service import Change

PROTOCOL = 2

Command = Literal["status", "next", "prev", "fav", "unfav", "remove"]
COMMANDS: tuple[Command, ...] = get_args(Command)
# Commands that act on a target (playlist or monitor): all but `status`.
TARGET_COMMANDS: tuple[Command, ...] = COMMANDS[1:]


class Message(BaseModel):
    """Sent by the app; read leniently, so newer fields are ignored."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class Strict(BaseModel):
    """Sent to the app; checked strictly."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# -- requests ------------------------------------------------------------------


class Request(Strict):
    """A playlist command."""

    command: Command
    target: str | None = None  # playlist name, or a monitor it drives


class Subscribe(Strict):
    """Keep the connection open and receive one `Event` per line."""

    command: Literal["subscribe"] = "subscribe"


class Envelope(Strict):
    """A request on the wire."""

    v: int = PROTOCOL
    request: Annotated[Request | Subscribe, Field(discriminator="command")]
    config: Path  # the config file the sender means; the app refuses others

    @field_validator("v")
    @classmethod
    def _same_protocol(cls, version: int) -> int:
        if version != PROTOCOL:
            raise ValueError(
                f"the running app speaks protocol {PROTOCOL}, not {version}; "
                f"restart it after updating"
            )
        return version


# -- what the app reports ------------------------------------------------------


class MonitorView(Message):
    selector: str  # as written in the config
    name: str  # the monitor's name if listed, else the selector
    attached: bool | None  # None: not listed right now


class PlaylistView(Message):
    name: str
    monitors: tuple[MonitorView, ...]
    current: Path | None
    favourite: bool  # the current image is in favourites

    @property
    def attached(self) -> bool:
        """At least one member is attached, so next / previous can work."""
        return any(m.attached for m in self.monitors)


class Overview(Message):
    """The small snapshot the tray and the windows draw from."""

    playlists: tuple[PlaylistView, ...] = ()
    paused: str | None = None  # why rotation is paused, if it is
    error: str | None = None  # the service could not start (e.g. bad config)


class PlaylistDetails(PlaylistView):
    directory: Path
    order: Order
    interval: float  # seconds of active time
    mode: ImageMode | None  # None: as is
    next_in_seconds: float  # of active time
    upcoming: tuple[Path, ...]


# -- results, one per command --------------------------------------------------


class StatusResult(Message):
    command: Literal["status"] = "status"
    playlists: tuple[PlaylistDetails, ...]
    paused: str | None
    warnings: tuple[str, ...] = ()  # valid config that is probably a mistake


class NextResult(Message):
    command: Literal["next"] = "next"
    playlist: str
    change: Change


class PrevResult(Message):
    command: Literal["prev"] = "prev"
    playlist: str
    change: Change | None  # None: nothing earlier in the history


class MarkResult(Message):
    command: Literal["fav", "unfav"]
    playlist: str
    image: Path
    list_file: Path  # favourites.txt
    changed: bool  # False: it already was (fav) / wasn't (unfav) a favourite


class RemoveResult(Message):
    command: Literal["remove"] = "remove"
    playlist: str
    image: Path  # listed for removal (never deleted)
    list_file: Path  # to-remove.txt
    change: Change | None  # None: no monitor attached, it moves on later


Result = Annotated[
    StatusResult | NextResult | PrevResult | MarkResult | RemoveResult,
    Field(discriminator="command"),
]

ErrorKind = Literal[
    "protocol",  # malformed request, or another protocol version
    "wrong_config",  # the app runs another config file
    "config_error",  # the config is missing or invalid; the app waits for a fix
    "unknown_target",  # no such playlist or monitor, or several and none named
    "not_attached",  # none of the playlist's monitors is attached
    "no_images",  # nothing to show in the playlist's directory
    "nothing_shown",  # no current image yet (fav / unfav / remove)
    "failed",  # any other expected failure; see the message
    "internal",  # a bug; the app logged it
    "unreachable",  # client side only: no app, or it did not answer
]
ERROR_KINDS: tuple[ErrorKind, ...] = get_args(ErrorKind)


class ErrorInfo(Message):
    kind: ErrorKind
    message: str


class ErrorReply(Message):
    error: ErrorInfo

    @classmethod
    def make(cls, kind: ErrorKind, message: object) -> ErrorReply:
        return cls(error=ErrorInfo(kind=kind, message=str(message)))


Reply = Result | ErrorReply


# -- events --------------------------------------------------------------------


class ChangedEvent(Message):
    """A wallpaper changed, by a tick or by a command."""

    event: Literal["changed"] = "changed"
    change: Change


class OverviewChangedEvent(Message):
    """The overview changed; also the first event of every subscription."""

    event: Literal["overview_changed"] = "overview_changed"
    overview: Overview


Event = Annotated[ChangedEvent | OverviewChangedEvent, Field(discriminator="event")]


# -- JSON Schema ---------------------------------------------------------------


def json_schema() -> dict[str, Any]:
    """
    One document for the whole protocol: `Request` (what the app reads),
    `Reply` and `Event` (what it writes), with shared definitions.
    """
    roots: dict[str, TypeAdapter[Any]] = {
        "Request": TypeAdapter(Envelope),
        "Reply": TypeAdapter(Reply),
        "Event": TypeAdapter(Event),
    }
    schemas, definitions = TypeAdapter.json_schemas(
        [(name, "validation", adapter) for name, adapter in roots.items()],
        ref_template="#/$defs/{model}",
    )
    defs = dict(definitions.get("$defs", {}))
    for name in roots:
        defs[name] = schemas[(name, "validation")]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "wallpaper_py socket protocol",
        "description": (
            f"Protocol version {PROTOCOL}. One JSON object per line: a Request "
            "in; a Reply out, or Events while subscribed. See docs/protocol.md."
        ),
        "anyOf": [{"$ref": f"#/$defs/{name}"} for name in roots],
        "$defs": dict(sorted(defs.items())),
    }
