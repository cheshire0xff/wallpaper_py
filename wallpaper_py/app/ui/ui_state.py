"""
The window's view state: what it shows, remembered between openings (the
Library's playlist and tab, each grid tab's sort and chips). Not
settings: no revision, never on the socket, nothing the daemon reads.
Written only by the window process, to
`paths.ui_state()` (in the data directory, which About lists; not
`QSettings`, which is the registry on Windows).

A missing or unreadable file gives the defaults, logged, never an error
shown: losing it only loses where the window was. So does one of an older
version that no longer fits (pre-release, no migrations): the tab named
`overview` is `stats` now.
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from .entries import Sort

log = logging.getLogger(__name__)

GridName = Literal["all", "favourites", "to_remove", "history", "upcoming"]
LibraryTab = Literal["all", "favourites", "to_remove", "history", "upcoming", "stats"]


class UiState(BaseModel):
    # Lenient: a field dropped later is ignored, not a reason to lose the rest.
    model_config = ConfigDict(frozen=True, extra="ignore")

    library_playlist: str | None = None  # a playlist's id; None: the first one
    library_tab: LibraryTab = "all"
    # Each grid tab's, as chosen (a tab not here: its default).
    library_sorts: dict[GridName, Sort] = {}
    library_switches: dict[GridName, bool] = {}  # Hide removed, Only <playlist>
    library_badges: dict[GridName, bool] = {}  # shown (default: yes)


def load_ui_state(file: Path) -> UiState:
    """The stored state; the defaults if there is none or it can't be read."""
    try:
        text = file.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.info("No %s yet: the defaults", file.name)
        return UiState()
    except OSError as err:
        log.warning("Cannot read %s, using the defaults: %s", file, err)
        return UiState()
    try:
        return UiState.model_validate_json(text)
    except ValidationError as err:
        log.warning("Cannot use %s, using the defaults: %s", file, err)
        return UiState()


def save_ui_state(file: Path, state: UiState) -> None:
    """Written beside it, then swapped in; a failure is only logged."""
    temporary = file.with_name(file.name + ".tmp")
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, file)
    except OSError as err:
        log.warning("Cannot save %s: %s", file, err)
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)
