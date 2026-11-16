"""A `Client` for tray and window tests: records requests, never reaches a daemon."""

from __future__ import annotations

from typing import override

from wallpaper_py.app.client import Client, ReplyCallback
from wallpaper_py.protocol import ClientRequest, Overview, OverviewChangedEvent


class FakeClient(Client):
    """Records requests; a test answers them through the recorded callbacks."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[ClientRequest, ReplyCallback]] = []

    @override
    def run(self, request: ClientRequest, on_reply: ReplyCallback) -> None:
        self.calls.append((request, on_reply))

    def publish(self, overview: Overview) -> None:
        """As if the daemon sent a new overview."""
        self._dispatch(OverviewChangedEvent(overview=overview))

    def commands(self) -> list[ClientRequest]:
        return [r for r, _ in self.calls if r.command != "status"]
