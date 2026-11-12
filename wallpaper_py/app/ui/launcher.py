"""
Opening the window on a page: in the UI process that runs, else start one.

The UI process listens on its own small socket (`UiServer`, next to the
app's, name from `ipc.server_name(data_dir, "ui")`) for one kind of
request, "show the window on this page":

    -> {"v": 2, "page": "home", "config": "C:/.../config.toml"}
    <- {"error": null}

`config` is left out when the user named none: then any config will do.
Named, it must be the one the windows use (the app's).
`WindowOpener` asks it from the tray without blocking, and starts
`wallpaper ui <page>` if nothing answers; `show_in_running_ui` asks it
from a starting UI process (blocking, before its event loop runs), which
exits if another one answered. At most one UI process runs: it holds
`paths.ui_lock()` while it listens.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal, get_args

from pydantic import ValidationError, field_validator
from PySide6.QtCore import QObject, Signal, Slot

from ...backends import allow_foreground
from ...core.model import WallpaperError
from ...protocol import PROTOCOL, Message, Overview, Strict, check_protocol
from .. import ipc
from ..processes import start_detached

log = logging.getLogger(__name__)

PageName = Literal["home", "library", "settings"]  # the main window's pages
PAGE_NAMES: tuple[PageName, ...] = get_args(PageName)
SHOW_TIMEOUT_S = 5.0


class ShowRequest(Strict):
    v: int = PROTOCOL
    page: PageName
    # The UI process refuses another config than its own; None: any.
    config: Path | None = None

    @field_validator("v")
    @classmethod
    def _same_protocol(cls, version: int) -> int:
        return check_protocol(version, "the open windows")


class ShowReply(Message):
    error: str | None = None  # None: shown


def ui_server_name(data_dir: Path) -> str:
    return ipc.server_name(data_dir, "ui")


class UiServer(ipc.LineServer):
    """
    In the UI process: `show_requested(page)` for each valid request.
    Its config is the one it was started with, if any, else the app's once
    the first overview says which (`follow`); until then, any is accepted.
    """

    show_requested = Signal(str)  # PageName

    def __init__(
        self, name: str, config_path: Path | None, parent: QObject | None = None
    ) -> None:
        super().__init__(name, parent)
        self._config_path = config_path

    @Slot(object)
    def follow(self, overview: Overview) -> None:
        """The app's overview: the windows show (and edit) its config."""
        if overview.config is not None:
            self._config_path = overview.config

    def _received(self, connection_id: int, line: bytes) -> None:
        try:
            request = ShowRequest.model_validate_json(line)
        except ValidationError as err:
            self._answer(connection_id, f"Malformed request: {ipc.brief(err)}")
            return
        if (
            request.config is not None
            and self._config_path is not None
            and not ipc.same_file(request.config, self._config_path)
        ):
            self._answer(
                connection_id,
                f"The open windows use {self._config_path}, not "
                f"{request.config}; close them to use another config",
            )
            return
        self.show_requested.emit(request.page)
        self._answer(connection_id, None)

    def _answer(self, connection_id: int, error: str | None) -> None:
        self.finish(connection_id, ipc.encode(ShowReply(error=error)))

    def _too_long(self) -> bytes:
        return ipc.encode(ShowReply(error="Request too long"))


def show_in_running_ui(name: str, request: ShowRequest) -> bool:
    """
    Blocking: True if a running UI process showed the page, False if
    none runs. Raises WallpaperError if it refused or did not answer.
    """
    allow_foreground()
    line = ipc.request_line(
        name, ipc.encode(request), SHOW_TIMEOUT_S, peer="the open windows"
    )
    if line is None:
        return False
    reply = _decode(line)
    if reply.error is not None:
        raise WallpaperError(reply.error)
    return True


def _decode(line: bytes) -> ShowReply:
    try:
        return ShowReply.model_validate_json(line)
    except ValidationError as err:
        raise ipc.ProtocolError(f"Malformed reply: {ipc.brief(err)}") from err


class WindowOpener(QObject):
    """
    For the tray: `open(page)` asks the running UI process to show the
    window on that page, or starts one (`command(page)`) if none answers.
    Never blocks; problems are reported through `failed`.
    """

    failed = Signal(str)  # message

    def __init__(
        self,
        name: str,
        config_path: Path,
        command: Callable[[PageName], Sequence[str]],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._name = name
        self._config_path = config_path
        self._command = command

    def open(self, page: PageName) -> None:
        allow_foreground()  # now, while the click still counts

        def answered(line: bytes) -> None:
            try:
                error = _decode(line).error
            except ipc.ProtocolError as err:
                error = str(err)
            if error is not None:
                self._report(error)

        def not_answered(not_running: bool, message: str) -> None:
            if not_running:
                self._start(page)
            else:
                self._report(f"The open windows don't answer: {message}")

        request = ShowRequest(page=page, config=self._config_path)
        ipc.LineRequest(
            self._name,
            ipc.encode(request),
            answered,
            not_answered,
            timeout_s=SHOW_TIMEOUT_S,
            parent=self,
        )

    def _start(self, page: PageName) -> None:
        try:
            start_detached(self._command(page))
        except OSError as err:
            self._report(f"Cannot open the window ({page}): {err}")

    def _report(self, message: str) -> None:
        log.warning("%s", message)
        self.failed.emit(message)
