"""
The Settings page's edit, without widgets (settings.py shows it).

`Draft` is the settings as the form has them: texts as typed (durations,
folders, list files) and the monitors' rows as chosen. `Draft.of` reads
saved `Settings`, `Draft.settings` turns a draft without problems back
into `Settings` for `save_settings`. `problems` says what keeps a draft
from being saved, per field, for the form to show next to it; the daemon
checks again when it saves (core.settings.check_settings).

Also what the page derives from a draft: monitor labels for the
drop-down (`monitor_entries`), what a playlist is shown on, the summary of
a closed playlist, what changed (`change_summary`) and what the drawing
shows (`drawing`, with `current_images`).
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from ...core.model import (
    BLACK,
    DEFAULT_BLUR,
    Background,
    Colour,
    ImageMode,
    Look,
    MonitorId,
)
from ...core.settings import (
    DEFAULT_IDLE_S,
    DEFAULT_INTERVAL_S,
    MANUAL,
    MIN_INTERVAL_S,
    MonitorSettings,
    Order,
    SAME_LISTS,
    PlaylistSettings,
    Settings,
    format_duration,
    new_playlist_id,
    parse_duration,
    same_path,
)
from ...protocol import DesktopMonitor, MonitorView, Overview, PlaylistView
from .arrangement import twins

NEW_NAME = "New playlist"
NEW_INTERVAL = format_duration(DEFAULT_INTERVAL_S)
DEFAULT_IDLE = format_duration(DEFAULT_IDLE_S)

# A problem's place: (playlist id, or None for the general settings; field).
Field = tuple[str | None, str]
NAME, DIRECTORY, INTERVAL = "name", "directory", "interval"
IDLE, FAVOURITES, TO_REMOVE = "pause_when_idle", "favourites_file", "to_remove_file"
FIELD_NAMES = {
    NAME: "name",
    DIRECTORY: "folder",
    INTERVAL: "interval",
    IDLE: "idle pause",
    FAVOURITES: "favourites file",
    TO_REMOVE: "to-remove file",
}

MODE_NAMES = {
    ImageMode.FILL: "Fill",
    ImageMode.FIT: "Fit",
    ImageMode.STRETCH: "Stretch",
}
MODE_HELP = {
    ImageMode.FILL: "Covers the screen; what doesn't fit is cut off",
    ImageMode.FIT: "Shows the whole image; bars fill the rest",
    ImageMode.STRETCH: "Fills the screen; the image changes its shape",
}

# The Background drop-down (Fit only): a fixed colour, or one from the image.
COLOUR: Literal["colour"] = "colour"
BackgroundKind = Background | Literal["colour"]
BACKGROUND_NAMES: dict[BackgroundKind, str] = {
    COLOUR: "Colour",
    Background.AVERAGE: "Average of the image",
    Background.DOMINANT: "Main colour of the image",
    Background.EDGES: "Edges of the image",
    Background.BLUR: "Blurred image",
}
BACKGROUND_HELP: dict[BackgroundKind, str] = {
    COLOUR: "Fills the bars beside the image",
    Background.AVERAGE: "The image's average colour fills the bars",
    Background.DOMINANT: "The image's most common colour fills the bars",
    Background.EDGES: "The colour of the image's edges beside the bars",
    Background.BLUR: "The image, blurred, fills the bars",
}

# The Blur and Darken sliders (the `blur` background only), in steps that
# feel even. The radius doubles every BLUR_PER_DOUBLING steps, the default
# on step 0: about 0.00105 to 0.02 (core.model.MIN_BLUR, MAX_BLUR). The
# darkening goes up by 1/DARKEN_PER_ONE a step: 0 to 0.8, 0.25 on step 5.
BLUR_PER_DOUBLING = 4
BLUR_STEPS = (-9, 8)
DARKEN_PER_ONE = 20
DARKEN_STEPS = (0, 16)


@dataclass(frozen=True)
class PlaylistDraft:
    id: str  # generated, never edited: it keys the playlist's state
    name: str
    directory: str  # as typed
    order: Order = Order.SHUFFLE
    interval: str = NEW_INTERVAL  # a duration as typed, or MANUAL

    @property
    def manual(self) -> bool:
        return self.interval == MANUAL


@dataclass(frozen=True)
class Draft:
    """The settings as edited (see the module docstring)."""

    playlists: tuple[PlaylistDraft, ...] = ()
    monitors: tuple[MonitorSettings, ...] = ()
    pause_when_locked: bool = True
    pause_when_idle: str | None = DEFAULT_IDLE  # a duration as typed; None: never
    favourites_file: str = ""  # "": the default
    to_remove_file: str = ""

    @classmethod
    def of(cls, settings: Settings) -> Draft:
        idle = settings.pause_when_idle
        return cls(
            playlists=tuple(
                PlaylistDraft(
                    id=p.id,
                    name=p.name,
                    directory=str(p.directory),
                    order=p.order,
                    interval=(
                        MANUAL if p.interval is None else format_duration(p.interval)
                    ),
                )
                for p in settings.playlists
            ),
            monitors=settings.monitors,
            pause_when_locked=settings.pause_when_locked,
            pause_when_idle=None if idle is None else format_duration(idle),
            favourites_file=str(settings.favourites_file or ""),
            to_remove_file=str(settings.to_remove_file or ""),
        )

    # -- reading -----------------------------------------------------------

    def playlist(self, playlist_id: str) -> PlaylistDraft | None:
        return next((p for p in self.playlists if p.id == playlist_id), None)

    def monitor(self, monitor_id: MonitorId) -> MonitorSettings:
        """Its row; a monitor without one has no playlist and the default look."""
        found = next((m for m in self.monitors if m.id == monitor_id), None)
        return found or MonitorSettings(id=monitor_id)

    def has_row(self, monitor_id: MonitorId) -> bool:
        return any(m.id == monitor_id for m in self.monitors)

    def members(self, playlist_id: str) -> tuple[MonitorId, ...]:
        return tuple(m.id for m in self.monitors if m.playlist == playlist_id)

    # -- editing (each returns a new draft) --------------------------------

    def with_monitor(self, monitor: MonitorSettings) -> Draft:
        """`monitor`'s row in place of its old one (or added)."""
        if not self.has_row(monitor.id):
            return replace(self, monitors=(*self.monitors, monitor))
        rows = tuple(monitor if m.id == monitor.id else m for m in self.monitors)
        return replace(self, monitors=rows)

    def without_monitor(self, monitor_id: MonitorId) -> Draft:
        """Forget a monitor: its row goes."""
        rows = tuple(m for m in self.monitors if m.id != monitor_id)
        return replace(self, monitors=rows)

    def with_playlist(self, playlist: PlaylistDraft) -> Draft:
        """`playlist` in place of the one with its id (or added last)."""
        if self.playlist(playlist.id) is None:
            return replace(self, playlists=(*self.playlists, playlist))
        playlists = tuple(
            playlist if p.id == playlist.id else p for p in self.playlists
        )
        return replace(self, playlists=playlists)

    def without_playlist(self, playlist_id: str) -> Draft:
        """Delete a playlist; its monitors are left on none."""
        return replace(
            self,
            playlists=tuple(p for p in self.playlists if p.id != playlist_id),
            monitors=tuple(
                (
                    m.model_copy(update={"playlist": None})
                    if m.playlist == playlist_id
                    else m
                )
                for m in self.monitors
            ),
        )

    def new_playlist(self) -> PlaylistDraft:
        """A playlist to add: a free name, a new id, no folder yet."""
        taken = {p.name.strip().casefold() for p in self.playlists}
        name, number = NEW_NAME, 1
        while name.casefold() in taken:
            number += 1
            name = f"{NEW_NAME} {number}"
        return PlaylistDraft(id=new_playlist_id(), name=name, directory="")

    # -- saving ------------------------------------------------------------

    def settings(self) -> Settings:
        """
        The draft as settings. A monitor's row that says no more than the
        defaults is left out (as if it had none).

        Raises:
            ValueError: a text that doesn't read (see `problems`).
        """
        idle = self.pause_when_idle
        return Settings(
            playlists=tuple(
                PlaylistSettings(
                    id=p.id,
                    name=p.name.strip(),
                    directory=_path(p.directory),
                    order=p.order,
                    interval=None if p.manual else parse_duration(p.interval),
                )
                for p in self.playlists
            ),
            monitors=tuple(m for m in self.monitors if m != MonitorSettings(id=m.id)),
            pause_when_locked=self.pause_when_locked,
            pause_when_idle=None if idle is None else parse_duration(idle),
            favourites_file=_list_file(self.favourites_file),
            to_remove_file=_list_file(self.to_remove_file),
        )

    def settings_or_none(self) -> Settings | None:
        try:
            return self.settings()
        except ValueError:
            return None


def _path(text: str) -> Path:
    return Path(text.strip()).expanduser()


def _list_file(text: str) -> Path | None:
    """A list file as typed; None (empty): the default."""
    return _path(text) if text.strip() else None


# -- problems ------------------------------------------------------------------


def problems(draft: Draft, base: Settings, data_dir: Path) -> dict[Field, str]:
    """
    What keeps `draft` from being saved, per field. A folder must exist
    only when it is new or changed since `base` (the saved settings): an
    unplugged drive must not block changing something else. `data_dir`
    holds the list files left empty (the defaults).
    """
    written = {p.id: p.directory for p in base.playlists}
    found: dict[Field, str] = {}
    names: dict[str, str] = {}
    for playlist in draft.playlists:
        name = playlist.name.strip()
        if not name:
            found[(playlist.id, NAME)] = "Give it a name"
        elif (other := names.get(name.casefold())) is not None:
            found[(playlist.id, NAME)] = f"Another playlist is called {other}"
        else:
            names[name.casefold()] = name
        problem = _folder_problem(playlist.directory, written.get(playlist.id))
        if problem is not None:
            found[(playlist.id, DIRECTORY)] = problem
        if (problem := interval_problem(playlist.interval)) is not None:
            found[(playlist.id, INTERVAL)] = problem
    if draft.pause_when_idle is not None:
        try:
            if parse_duration(draft.pause_when_idle) <= 0:
                found[(None, IDLE)] = "Must be more than 0; untick it to never pause"
        except ValueError:
            found[(None, IDLE)] = "Use a duration like 45s, 5m or 1h"
    return found | _list_problems(draft, data_dir)


def _folder_problem(text: str, written: Path | None) -> str | None:
    if not text.strip():
        return "Choose a folder"
    path = _path(text)
    if not path.is_absolute():
        return "Use the folder's full path"
    if path != written and not path.is_dir():
        return f"No such folder: {path}"
    return None


def interval_problem(interval: str) -> str | None:
    if interval == MANUAL:
        return None
    try:
        seconds = parse_duration(interval)
    except ValueError:
        return "Use a duration like 45s, 30m, 1h30m or 1d"
    if seconds < MIN_INTERVAL_S:
        return f"At least {format_duration(MIN_INTERVAL_S)}"
    return None


def _list_problems(draft: Draft, data_dir: Path) -> dict[Field, str]:
    found: dict[Field, str] = {}
    for key in (FAVOURITES, TO_REMOVE):
        text: str = getattr(draft, key)
        if (problem := _list_problem(text)) is not None:
            found[(None, key)] = problem
    if found:
        return found
    lists = Settings(
        favourites_file=_list_file(draft.favourites_file),
        to_remove_file=_list_file(draft.to_remove_file),
    ).list_files(data_dir)
    if same_path(*lists):  # check_list_files
        found[(None, TO_REMOVE)] = SAME_LISTS
    return found


def _list_problem(text: str) -> str | None:
    if not text.strip():
        return None
    path = _path(text)
    if not path.is_absolute():
        return "Use the file's full path"
    if path.is_dir():
        return "This is a folder; choose a file"
    return None


def problem_summary(found: Mapping[Field, str], draft: Draft) -> str:
    """The first problem, for the line beside Save: "konachan, folder: …"."""
    (playlist_id, key), message = next(iter(found.items()))
    playlist = draft.playlist(playlist_id) if playlist_id is not None else None
    if playlist is not None:
        where = f"{playlist.name.strip() or 'a playlist'}, {FIELD_NAMES[key]}: "
    else:
        where = f"{FIELD_NAMES[key].capitalize()}: "
    more = len(found) - 1
    tail = f" (and {more} more)" if more else ""
    return f"Can't save yet. {where}{message}{tail}"


# -- monitors ------------------------------------------------------------------

State = Literal["drawn", "off", "away"]  # attached, switched off, not listed


@dataclass(frozen=True)
class MonitorEntry:
    """A monitor in the drop-down, with its label."""

    id: MonitorId
    label: str  # "PLX2783H · 1920×1080 · 1 of 2", "ZeroMOD · switched off"
    name: str  # what the texts call it: the label without its state
    state: State


def monitor_entries(
    listed: Sequence[DesktopMonitor], draft: Draft
) -> list[MonitorEntry]:
    """
    Every monitor the page can set up: the attached ones left to right,
    then the switched-off ones, then those the settings have but the
    desktop doesn't list now (not connected). Monitors sharing a name get
    their resolution and a number (`arrangement.twins`).
    """
    numbers = twins(listed)
    attached = sorted(
        (m for m in listed if m.geometry is not None),
        key=lambda m: (m.geometry.x, m.geometry.y) if m.geometry else (0, 0),
    )
    entries = []
    for monitor in attached:
        name = monitor.name
        if (twin := numbers.get(monitor.id)) is not None and monitor.geometry:
            width, height = monitor.geometry.screen
            name = f"{name} · {width}×{height} · {twin[0]} of {twin[1]}"
        entries.append(MonitorEntry(monitor.id, name, name, "drawn"))
    for monitor in listed:
        if monitor.geometry is None:
            entries.append(
                MonitorEntry(
                    monitor.id, f"{monitor.name} · switched off", monitor.name, "off"
                )
            )
    for row in draft.monitors:
        if not any(m.id == row.id for m in listed):
            name = short_id(row.id)
            entries.append(
                MonitorEntry(row.id, f"{name} · not connected", name, "away")
            )
    return entries


def short_id(monitor_id: MonitorId) -> str:
    r"""
    A monitor the desktop doesn't list has no name here (the settings hold
    none), so it goes by its id: on Windows the model code in it
    (`\\?\DISPLAY#DELA1B2#...` -> "DELA1B2"), else the id itself.
    """
    parts = str(monitor_id).split("#")
    return parts[1] if len(parts) > 2 and parts[1] else str(monitor_id)


def entry_names(entries: Sequence[MonitorEntry]) -> dict[MonitorId, str]:
    return {e.id: e.name for e in entries}


def shown_on(
    draft: Draft, playlist_id: str, entries: Sequence[MonitorEntry]
) -> list[str]:
    """
    The monitors a playlist is on, as the texts name them, in the order of
    `entries` (left to right, then those not drawn).
    """
    members = draft.members(playlist_id)
    shown = []
    for entry in entries:
        if entry.id not in members:
            continue
        if entry.state == "drawn":
            shown.append(entry.name)
        else:
            state = "switched off" if entry.state == "off" else "not connected"
            shown.append(f"{entry.name} ({state})")
    listed = {e.id for e in entries}
    shown += [short_id(m) for m in members if m not in listed]
    return shown


def playlist_help(
    draft: Draft, monitor_id: MonitorId, entries: Sequence[MonitorEntry]
) -> str:
    """The Playlist row's description, for the selected monitor."""
    playlist = draft.monitor(monitor_id).playlist
    if playlist is None:
        return "Nothing rotates on this monitor"
    others = shown_on(draft.without_monitor(monitor_id), playlist, entries)
    if not others:
        return "Pick the playlist of another monitor to mirror it"
    return f"Mirrors {' + '.join(others)}: the same image, changed together"


def summary_text(
    draft: Draft, playlist: PlaylistDraft, entries: Sequence[MonitorEntry]
) -> str:
    """A closed playlist's row: "AW3225QF + PLX2783H · shuffle · every 25m"."""
    on = " + ".join(shown_on(draft, playlist.id, entries)) or "no monitor"
    changes = "manually" if playlist.manual else f"every {playlist.interval.strip()}"
    return " · ".join((on, playlist.order.value, changes))


# -- what changed --------------------------------------------------------------

_PLAYLIST_FIELDS = (
    ("name", "name"),
    ("folder", "directory"),
    ("order", "order"),
    ("interval", "interval"),
)
_GENERAL_FIELDS = (
    ("pausing while locked", "pause_when_locked"),
    ("the idle pause", "pause_when_idle"),
    ("the favourites file", "favourites_file"),
    ("the to-remove file", "to_remove_file"),
)
MAX_CHANGES = 3  # named in the status line; the rest are counted


def change_summary(
    base: Settings,
    edited: Settings,
    names: Mapping[MonitorId, str],
    listed: Collection[MonitorId],
) -> str:
    """
    What differs, for the status line: "AW3225QF shows fantasy, interval of
    konachan, delete strip and its history". `names`: what to call each
    monitor (`entry_names`); `listed`: the monitors the desktop lists.
    """
    parts = _monitor_changes(base, edited, names, listed)
    before = {p.id: p for p in base.playlists}
    for playlist in edited.playlists:
        old = before.pop(playlist.id, None)
        if old is None:
            parts.append(f"new playlist {playlist.name}")
            continue
        parts += [
            f"{label} of {old.name}"
            for label, key in _PLAYLIST_FIELDS
            if getattr(old, key) != getattr(playlist, key)
        ]
    parts += [f"delete {p.name} and its history" for p in before.values()]
    parts += [
        label
        for label, key in _GENERAL_FIELDS
        if getattr(base, key) != getattr(edited, key)
    ]
    if len(parts) > MAX_CHANGES:
        more = len(parts) - MAX_CHANGES
        parts = [*parts[:MAX_CHANGES], f"{more} more"]
    return ", ".join(parts)


def _monitor_changes(
    base: Settings,
    edited: Settings,
    names: Mapping[MonitorId, str],
    listed: Collection[MonitorId],
) -> list[str]:
    playlists = {p.id: p.name for p in edited.playlists}
    seen: set[MonitorId] = set()
    parts = []
    for row in (*edited.monitors, *base.monitors):
        monitor_id = row.id
        if monitor_id in seen:
            continue
        seen.add(monitor_id)
        name = names.get(monitor_id) or short_id(monitor_id)
        old, new = _row(base, monitor_id), _row(edited, monitor_id)
        if old == new:
            continue
        if new is None and monitor_id not in listed:
            parts.append(f"forget {name}")
            continue
        old, new = old or MonitorSettings(id=monitor_id), new or MonitorSettings(
            id=monitor_id
        )
        if old.playlist != new.playlist:
            shows = playlists.get(new.playlist or "", "no playlist")
            parts.append(f"{name} shows {shows}")
        if old.look.mode != new.look.mode:
            parts.append(f"mode of {name}")
        if old.look.background != new.look.background:
            parts.append(f"background of {name}")
        if old.look.blur != new.look.blur:
            parts.append(f"blur of {name}")
        if old.look.darken != new.look.darken:
            parts.append(f"darkening of {name}")
    return parts


def _row(settings: Settings, monitor_id: MonitorId) -> MonitorSettings | None:
    """Its row, unless it has none or one that says only the defaults."""
    row = next((m for m in settings.monitors if m.id == monitor_id), None)
    return None if row == MonitorSettings(id=monitor_id) else row


def mode_look(look: Look, mode: ImageMode) -> Look:
    return replace(look, mode=mode)


def background_kind(background: Background | Colour) -> BackgroundKind:
    """Its item in the Background drop-down."""
    return background if isinstance(background, Background) else COLOUR


def background_look(look: Look, background: Background | Colour) -> Look:
    return replace(look, background=background)


def blur_look(look: Look, step: int) -> Look:
    """With the Blur slider's `step`."""
    return replace(look, blur=DEFAULT_BLUR * 2 ** (step / BLUR_PER_DOUBLING))


def blur_step(look: Look) -> int:
    """The Blur slider's nearest step for the look's radius."""
    step = round(BLUR_PER_DOUBLING * math.log2(look.blur / DEFAULT_BLUR))
    return min(max(step, BLUR_STEPS[0]), BLUR_STEPS[1])


def darken_look(look: Look, step: int) -> Look:
    """With the Darken slider's `step`."""
    return replace(look, darken=step / DARKEN_PER_ONE)


def darken_step(look: Look) -> int:
    """The Darken slider's nearest step for the look's darkening."""
    step = round(look.darken * DARKEN_PER_ONE)
    return min(max(step, DARKEN_STEPS[0]), DARKEN_STEPS[1])


def colour_of(look: Look) -> Colour:
    """The look's fixed background colour (black if it has none)."""
    background = look.background
    return BLACK if isinstance(background, Background) else background


def current_images(
    base: Settings, draft: Draft, shown: Sequence[PlaylistView]
) -> dict[str, Path]:
    """
    Playlist id -> the image it shows now (`shown`: the daemon's overview),
    for the saved playlists still in `draft` with their folder unchanged:
    a new folder shows an image from it once saved, unknown here.
    """
    by_id = {p.id: p.current for p in shown if p.current is not None}
    result: dict[str, Path] = {}
    for saved in base.playlists:
        edited = draft.playlist(saved.id)
        if edited is None or not same_path(_path(edited.directory), saved.directory):
            continue
        if (image := by_id.get(saved.id)) is not None:
            result[saved.id] = image
    return result


def drawing(
    draft: Draft,
    listed: tuple[DesktopMonitor, ...],
    current: Mapping[str, Path] | None = None,
) -> Overview:
    """
    What the drawing shows: the attached monitors with their looks and the
    playlists as edited, each with its `current` image (by playlist id).
    """
    current = current or {}
    names = {m.id: m.name for m in listed}
    playlists = tuple(
        PlaylistView(
            id=playlist.id,
            name=playlist.name.strip() or "(no name)",
            directory=_path(playlist.directory),
            monitors=tuple(
                MonitorView(id=m, name=names.get(m, str(m)), attached=m in names)
                for m in draft.members(playlist.id)
            ),
            current=current.get(playlist.id),
            favourite=False,
        )
        for playlist in draft.playlists
    )
    attached = tuple(
        m.model_copy(update={"look": draft.monitor(m.id).look})
        for m in listed
        if m.attached
    )
    return Overview(playlists=playlists, monitors=attached)
