"""
The daemon's socket protocol as pydantic models: what goes over the wire, and
nothing else (no logic, no Qt). See docs/protocol.md.

- In: an `Envelope` holding a `StatusRequest`, a `Request` (a command on a
  playlist), a `MarkRequest` (put an image on a list or take it off), a
  `PickRequest` (`pick`, which also carries an image), an `UpcomingRequest`
  (the images to come), a `StatsRequest` / `ResetCycleRequest` (count a
  library / start its shuffle cycle over), a `SettingsRequest` /
  `SaveSettingsRequest` (read / write the settings), `Subscribe`, or
  `ShowWindowRequest`. Each command's request model says whether it
  `changes` anything (a class variable). `subscribe` and `show_window` are
  the socket's own (`ipc.CommandServer`), not commands: they also work
  while `PlaylistService` is stopped.
- Out: a `Reply`, which is data only: one result model per command, tagged
  on `command`, or an `ErrorReply`. The CLI formats its text from these;
  the tray and the window process show them.
- Out, on a subscription: one `Event` per line, tagged on `event`.

The window process subscribes as the window (`Subscribe.window`); only the
first such subscriber is, and `show_window` and `show_page` reach it.

`commands.py` runs requests, `app/ipc.py` carries the lines. The protocol
only ever adds commands, fields, error kinds and events; clients ignore
fields they don't know. A breaking change bumps `PROTOCOL`. (Until the
first release it may still change in place; see AGENTS.md.)

docs/protocol.schema.json is generated from these models
(`scripts/protocol_schema.py`); a test keeps it current.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    TypeAdapter,
    field_validator,
    model_validator,
)

from .core.model import Look, MonitorId
from .core.service import Change, ListStats, PlaylistStats
from .core.settings import Order, PlaylistId, Settings

PROTOCOL = 3

# The commands a plain `Request` carries.
PlaylistCommand = Literal["next", "prev", "hold", "resume"]
# The commands a `MarkRequest` carries: an image on a list, or off it.
MarkCommand = Literal["fav", "unfav", "remove", "unremove"]
MARK_COMMANDS: tuple[MarkCommand, ...] = get_args(MarkCommand)
# The commands on a playlist's state or its current image (the tray's and
# Home's buttons): a `Request`, or a `MarkRequest` with only a target
# (`target_request`).
TargetCommand = Literal["next", "prev", "fav", "unfav", "remove", "hold", "resume"]
TARGET_COMMANDS: tuple[TargetCommand, ...] = get_args(TargetCommand)
MAX_UPCOMING = 1000  # images an `upcoming` request may ask for
PageName = Literal["home", "library", "settings"]  # the window's pages
PAGE_NAMES: tuple[PageName, ...] = get_args(PageName)


class Message(BaseModel):
    """Sent by the daemon; read leniently, so newer fields are ignored."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class Strict(BaseModel):
    """Sent to the daemon; checked strictly."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# -- requests ------------------------------------------------------------------


class StatusRequest(Strict):
    """The playlists with their schedule."""

    changes: ClassVar[bool] = False
    command: Literal["status"] = "status"


class Request(Strict):
    """A command on a playlist."""

    changes: ClassVar[bool] = True
    command: PlaylistCommand
    target: str | None = None  # playlist id or name, or a monitor it drives


class MarkRequest(Strict):
    """
    Put an image on a list (`fav`, `remove`) or take it off (`unfav`,
    `unremove`). Without `image`: the target playlist's current image, and
    `remove` then shows the next one. With it: that image (any path, no
    target); `remove` then only lists it (a playlist showing it moves on at
    its next tick). `unremove` needs an image: the current one is never
    listed for long.
    """

    changes: ClassVar[bool] = True
    command: MarkCommand
    target: str | None = None  # playlist id or name, or a monitor it drives
    image: Path | None = None  # absolute: the daemon has no client directory

    @field_validator("image")
    @classmethod
    def _absolute(cls, image: Path | None) -> Path | None:
        return None if image is None else _absolute(image)

    @model_validator(mode="after")
    def _image_or_target(self) -> MarkRequest:
        if self.image is not None and self.target is not None:
            raise ValueError("name an image or a target, not both")
        if self.command == "unremove" and self.image is None:
            raise ValueError("unremove needs an image")
        return self


class PickRequest(Strict):
    """Show a chosen image on a playlist now (from any directory)."""

    changes: ClassVar[bool] = True
    command: Literal["pick"] = "pick"
    target: str | None = None  # playlist id or name, or a monitor it drives
    image: Path  # absolute: the daemon doesn't know the client's directory

    @field_validator("image")
    @classmethod
    def _absolute(cls, image: Path) -> Path:
        return _absolute(image)


def _absolute(image: Path) -> Path:
    if not image.is_absolute():
        raise ValueError(f"image must be an absolute path, not {image}")
    return image


class UpcomingRequest(Strict):
    """The images a playlist shows next, in order."""

    changes: ClassVar[bool] = False
    command: Literal["upcoming"] = "upcoming"
    target: str | None = None  # playlist id or name, or a monitor it drives
    limit: Annotated[StrictInt, Field(ge=1, le=MAX_UPCOMING)] = 100


class StatsRequest(Strict):
    """What playlists pick up from their folders, and the lists' counts."""

    changes: ClassVar[bool] = False
    command: Literal["stats"] = "stats"
    target: str | None = None  # one playlist (or a monitor it drives); None: all
    rescan: StrictBool = False  # scan the folders again first


class ResetCycleRequest(Strict):
    """Start a shuffle playlist's cycle over: every image unseen again."""

    changes: ClassVar[bool] = True
    command: Literal["reset_cycle"] = "reset_cycle"
    target: str | None = None  # playlist id or name, or a monitor it drives


class SettingsRequest(Strict):
    """The stored settings, and their revision (for `save_settings`)."""

    changes: ClassVar[bool] = False
    command: Literal["settings"] = "settings"


class SaveSettingsRequest(Strict):
    """
    Store these settings in place of the stored ones, and use them at once.
    Refused (`stale_settings`) if the stored ones are no longer `revision`,
    the one the edit was made on.
    """

    changes: ClassVar[bool] = True
    command: Literal["save_settings"] = "save_settings"
    settings: Settings
    revision: int


# Every request answered with one reply (what `commands.execute` takes).
CommandRequest = (
    StatusRequest
    | Request
    | MarkRequest
    | PickRequest
    | UpcomingRequest
    | StatsRequest
    | ResetCycleRequest
    | SettingsRequest
    | SaveSettingsRequest
)


def target_request(command: TargetCommand, target: str | None) -> Request | MarkRequest:
    """A command on a playlist's state or its current image."""
    match command:
        case "fav" | "unfav" | "remove":
            return MarkRequest(command=command, target=target)
        case _:
            return Request(command=command, target=target)


class ShowWindowRequest(Strict):
    """
    Bring the window to the front on `page`, if a window process has it
    (`Subscribe.window`). Starting one if not is the asker's job.
    """

    command: Literal["show_window"] = "show_window"
    page: PageName


# Every request answered with one reply: what `Client.run` sends.
ClientRequest = CommandRequest | ShowWindowRequest


class Subscribe(Strict):
    """
    Keep the connection open and receive one `Event` per line.

    With `window`: "I am the window", shown on that page. Only the first
    such subscriber is; a later one has its page shown there instead
    (`show_page`), gets `ShowWindowResult(shown=True)` in place of the
    events, and is closed.
    """

    command: Literal["subscribe"] = "subscribe"
    window: PageName | None = None


class Envelope(Strict):
    """A request on the wire."""

    v: int = PROTOCOL
    request: Annotated[
        CommandRequest | ShowWindowRequest | Subscribe,
        Field(discriminator="command"),
    ]

    @field_validator("v")
    @classmethod
    def _same_protocol(cls, version: int) -> int:
        return check_protocol(version, "the running app")


def check_protocol(version: int, receiver: str) -> int:
    """For a `v` field: the receiver only reads its own protocol version."""
    if version != PROTOCOL:
        raise ValueError(
            f"{receiver} speaks protocol {PROTOCOL}, not {version}; "
            f"restart it after updating"
        )
    return version


# -- what the daemon reports ---------------------------------------------------


class MonitorView(Message):
    """
    A playlist's member. `id` is spelt as the desktop spells it when the
    monitor is listed (as in `Overview.monitors`), else as the settings
    do; ids compare case-insensitively (`MonitorId`).
    """

    id: MonitorId
    name: str  # the monitor's name if listed, else the id
    attached: bool | None  # None: not listed right now
    look: Look = Look()  # how it shows the playlist's images


class Geometry(Message):
    """
    A monitor's area in desktop coordinates (x and y may be negative), and
    its size in pixels if that differs (Wayland's logical coordinates:
    core.model.Rectangle).
    """

    x: int
    y: int
    width: int
    height: int
    pixels: tuple[int, int] | None = None

    @property
    def screen(self) -> tuple[int, int]:
        """The screen's size in pixels: what its wallpaper is rendered at."""
        return self.pixels or (self.width, self.height)


class DesktopMonitor(Message):
    """A monitor the desktop lists, whether a playlist drives it or not."""

    id: MonitorId
    name: str
    geometry: Geometry | None  # None: detached (known but switched off)
    look: Look = Look()  # its look in the settings (the default if none)

    @property
    def attached(self) -> bool:
        return self.geometry is not None


class PlaylistView(Message):
    id: PlaylistId
    name: str
    directory: Path
    monitors: tuple[MonitorView, ...]
    current: Path | None
    # The current image is in favourites. None: favourites.txt has not
    # been read yet (another program holds it), so it isn't known.
    favourite: bool | None
    held: bool = False  # held by `hold`: ticks don't change it (never if manual)
    manual: bool = False  # no interval: only next / prev / pick change it

    @property
    def attached(self) -> bool:
        """At least one member is attached, so next / previous can work."""
        return any(m.attached for m in self.monitors)


class Overview(Message):
    """The small snapshot the tray and the windows draw from."""

    playlists: tuple[PlaylistView, ...] = ()
    monitors: tuple[DesktopMonitor, ...] = ()  # every listed monitor
    paused: str | None = None  # why rotation is paused, if it is
    error: str | None = None  # the service isn't running (e.g. a bad list file)
    # favourites.txt and to-remove.txt as the settings name them; None
    # while the service isn't running.
    favourites_file: Path | None = None
    to_remove_file: Path | None = None


class PlaylistDetails(PlaylistView):
    order: Order
    interval: float | None  # seconds of active time; None: manual
    # Of active time; None: manual. While held, what is left once resumed.
    next_in_seconds: float | None
    upcoming: tuple[Path, ...]


# -- results, one per command --------------------------------------------------


class StatusResult(Message):
    command: Literal["status"] = "status"
    playlists: tuple[PlaylistDetails, ...]
    paused: str | None
    warnings: tuple[str, ...] = ()  # valid settings that are probably a mistake


class NextResult(Message):
    command: Literal["next"] = "next"
    playlist: str
    change: Change


class PrevResult(Message):
    command: Literal["prev"] = "prev"
    playlist: str
    change: Change | None  # None: nothing earlier in the history


class MarkResult(Message):
    command: Literal["fav", "unfav", "unremove"]
    playlist: str | None  # None: the request named an image
    image: Path
    list_file: Path  # favourites.txt; to-remove.txt for unremove
    # False: it already was (fav) / wasn't (unfav, unremove) on the list.
    changed: bool


class RemoveResult(Message):
    command: Literal["remove"] = "remove"
    playlist: str | None  # None: the request named an image
    image: Path  # listed for removal (never deleted)
    list_file: Path  # to-remove.txt
    changed: bool  # False: it was listed already
    # The next image shown. None: no monitor attached (it moves on later),
    # or the request named an image (only listed).
    change: Change | None


class UpcomingResult(Message):
    command: Literal["upcoming"] = "upcoming"
    playlist: str
    images: tuple[Path, ...]  # in order: the next one first


class PickResult(Message):
    command: Literal["pick"] = "pick"
    playlist: str
    change: Change


class StatsResult(Message):
    """
    Per playlist asked (all, or the target): its folder's images, seen,
    unseen and skipped, and the list entries among them; and the lists.
    """

    command: Literal["stats"] = "stats"
    playlists: tuple[PlaylistStats, ...]
    favourites: ListStats
    to_remove: ListStats


class ResetCycleResult(Message):
    command: Literal["reset_cycle"] = "reset_cycle"
    playlist: str
    unseen: int  # now: every image but the skipped ones


class SettingsResult(Message):
    command: Literal["settings"] = "settings"
    settings: Settings
    revision: int  # what `save_settings` sends back with the edit


class SaveSettingsResult(Message):
    """Saved and in use: every monitor shows what they say."""

    command: Literal["save_settings"] = "save_settings"
    revision: int  # of the saved settings: the next edit goes on this one
    warnings: tuple[str, ...] = ()  # valid settings that are probably a mistake


class HoldResult(Message):
    command: Literal["hold", "resume"]
    playlist: str
    held: bool  # now: True after `hold`, False after `resume`
    changed: bool  # False: it was held already (`hold`) / wasn't held (`resume`)


class ShowWindowResult(Message):
    """
    `show_window`'s reply, and a window subscription's refusal: True if
    the window process that has the window was asked to show the page.
    """

    command: Literal["show_window"] = "show_window"
    shown: bool


Result = Annotated[
    StatusResult
    | NextResult
    | PrevResult
    | MarkResult
    | RemoveResult
    | PickResult
    | UpcomingResult
    | HoldResult
    | StatsResult
    | ResetCycleResult
    | SettingsResult
    | SaveSettingsResult
    | ShowWindowResult,
    Field(discriminator="command"),
]

ErrorKind = Literal[
    "protocol",  # malformed request, or another protocol version
    "config_error",  # a list file can't be used; the daemon waits for a fix
    "unknown_target",  # no such playlist or monitor, or several and none named
    "not_attached",  # none of the playlist's monitors is attached
    "no_images",  # nothing to show in the playlist's directory
    "nothing_shown",  # no current image yet (fav / unfav / remove, no image named)
    "bad_image",  # pick: the image is missing or not one Pillow can open
    "invalid_settings",  # save_settings: a value can't be used; nothing saved
    "stale_settings",  # save_settings: changed since they were read; nothing saved
    "failed",  # any other expected failure; see the message
    "internal",  # a bug; the daemon logged it
    "unreachable",  # client side only: no daemon, or it did not answer
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


class MarksChangedEvent(Message):
    """
    Favourites or to-remove changed (by a command, by hand, or the settings
    name other files): read them again.
    """

    event: Literal["marks_changed"] = "marks_changed"


class ShowPageEvent(Message):
    """Only to the window: come to the front on `page`."""

    event: Literal["show_page"] = "show_page"
    page: PageName


Event = Annotated[
    ChangedEvent | OverviewChangedEvent | MarksChangedEvent | ShowPageEvent,
    Field(discriminator="event"),
]


# -- JSON Schema ---------------------------------------------------------------


def json_schema() -> dict[str, Any]:
    """
    One document for the whole protocol: `Request` (what the daemon reads),
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
