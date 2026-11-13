"""A `Client` for window tests: records requests, never reaches an app."""

from __future__ import annotations

from typing import override

from wallpaper_py.app.ui.client import Client, ReplyCallback
from wallpaper_py.protocol import CommandRequest, Overview


class FakeClient(Client):
    """Records requests; a test answers them through the recorded callbacks."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[CommandRequest, ReplyCallback]] = []

    @override
    def run(self, request: CommandRequest, on_reply: ReplyCallback) -> None:
        self.calls.append((request, on_reply))

    def publish(self, overview: Overview) -> None:
        """As if the app sent a new overview."""
        self._set_overview(overview)

    def commands(self) -> list[CommandRequest]:
        return [r for r, _ in self.calls if r.command != "status"]
