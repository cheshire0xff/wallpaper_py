#!/usr/bin/env python3
"""
The README's screenshots of the window (docs/images/), from made-up
monitors and playlists and NASA's photos (public domain, images.nasa.gov).

    uv run python scripts/screenshots.py

Run it again when the window's look changes. The photos are downloaded
once into build/screenshot-photos/ (a folder per playlist); the window is
rendered off screen, with the app's data in a temporary directory and a
client that answers in place of the daemon, so it never reaches a running
app or reads the real data. Each page is rendered in dark and in light
mode, with Windows' default accent; the README shows the one that matches
the reader's GitHub theme.
"""

# pylint: disable=wrong-import-order,ungrouped-imports
# First: pydantic before Qt (AGENTS.md, Qt for Python rule 4).
from wallpaper_py.protocol import (
    ClientRequest,
    DesktopMonitor,
    Geometry,
    MonitorView,
    Overview,
    OverviewChangedEvent,
    PageName,
    PlaylistDetails,
    PlaylistView,
    SettingsRequest,
    SettingsResult,
    StatsRequest,
    StatsResult,
    StatusResult,
    UpcomingRequest,
    UpcomingResult,
)

import os
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import override

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from wallpaper_py.app.client import Client, ReplyCallback
from wallpaper_py.app.ui.process import body_font
from wallpaper_py.app.ui.window import MainWindow
from wallpaper_py.core import paths
from wallpaper_py.core.model import ImageMode, Look, MonitorId
from wallpaper_py.core.service import ListStats, PlaylistStats
from wallpaper_py.core.settings import (
    MonitorSettings,
    Order,
    PlaylistSettings,
    Settings,
)

ROOT = Path(__file__).resolve().parent.parent
PHOTOS = ROOT / "build" / "screenshot-photos"
OUT = ROOT / "docs" / "images"

ASSETS = "https://images-assets.nasa.gov/image/{id}/{id}~large.jpg"
# A playlist's folder: (NASA's id, the file's name), all crew photos.
FOLDERS: dict[str, tuple[tuple[str, str], ...]] = {
    "Mountains": (
        ("iss058e000188", "andes-mountain-range"),
        ("iss058e006004", "argentina-chile-and-the-andes"),
        ("sts065-102-034", "northern-chile-and-the-andes"),
        ("iss073e0384067", "andes-through-chile-and-bolivia"),
        ("sts054-151-150", "thunderstorms-over-the-andes"),
        ("iss072e572361", "andes-between-chile-and-argentina"),
        ("iss074e0349412", "andes-from-orbit"),
        ("s66-38290", "gemini-9-over-peru"),
        ("iss072e398041", "snow-tipped-andes-and-lakes"),
        ("iss074e0791058", "clouds-around-the-andes"),
        ("iss074e0546820", "cerro-de-la-majadita"),
        ("iss074e0573516", "lake-argentino"),
        ("iss047e014747", "lakes-and-snow"),
        ("iss055e086530", "ridges"),
        ("iss042e071507", "station-and-horizon"),
    ),
    "Oceans": (
        ("iss071e449837", "waters-around-the-bahamas"),
        ("iss058e002206", "cuba-and-the-bahamas"),
        ("iss064e002482", "shallow-banks"),
        ("iss066e108216", "islands-and-reefs"),
        ("iss036e032513", "turquoise-shallows"),
        ("iss067e290699", "ocean-horizon"),
        ("iss068e036382", "sandbanks"),
        ("iss047e069406", "blue-planet"),
        ("iss073e0118628", "turks-and-caicos"),
        ("iss060e050367", "storm-from-orbit"),
    ),
}

CENTRE = DesktopMonitor(
    id=MonitorId("DISPLAY1"),
    name="QX2790",
    geometry=Geometry(x=0, y=0, width=2560, height=1440),
)
LEFT = DesktopMonitor(
    id=MonitorId("DISPLAY2"),
    name="FX2410",
    geometry=Geometry(x=-1920, y=180, width=1920, height=1080),
)
RIGHT = DesktopMonitor(  # turned upright
    id=MonitorId("DISPLAY3"),
    name="FX2410",
    geometry=Geometry(x=2560, y=-240, width=1080, height=1920),
)
ACCENT = "#0078d4"  # Windows' default
SCALE = "1.5"  # drawn as on a 150 % display: sharp on high-DPI screens
SETTLE_S = 6.0  # long enough for every thumbnail to be made and drawn


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="wallpaper_py-screenshots-"))
    # The window's data and thumbnails, never the real ones.
    paths.data_dir = lambda: work / "data"
    paths.cache_dir = lambda: work / "cache"
    paths.log_file = lambda: work / "logs" / "wallpaper.log"
    os.environ["QT_SCALE_FACTOR"] = SCALE
    world = World(
        {name: download(name, photos) for name, photos in FOLDERS.items()}, work
    )
    app = QApplication(sys.argv[:1])
    app.setApplicationName("wallpaper_py")
    app.setFont(body_font(app.font()))
    OUT.mkdir(parents=True, exist_ok=True)
    for scheme, suffix in (
        (Qt.ColorScheme.Dark, "dark"),
        (Qt.ColorScheme.Light, "light"),
    ):
        set_scheme(scheme)
        for page in ("home", "library", "settings"):
            render(app, world, page, OUT / f"{page}-{suffix}.webp", work)
    return 0


def download(folder: str, photos: tuple[tuple[str, str], ...]) -> Path:
    """The folder with its photos, each downloaded unless it is there already."""
    directory = PHOTOS / folder
    directory.mkdir(parents=True, exist_ok=True)
    for nasa_id, name in photos:
        file = directory / f"{name}.jpg"
        if file.exists():
            continue
        print(f"Downloading {nasa_id} as {file.name}", flush=True)
        request = urllib.request.Request(
            ASSETS.format(id=nasa_id),
            headers={"User-Agent": "wallpaper_py-screenshots"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            file.write_bytes(response.read())
    return directory


# -- what the daemon would say -----------------------------------------------------


class World:
    """The made-up app: its settings, overview and replies."""

    def __init__(self, folders: dict[str, Path], work: Path) -> None:
        mountains, oceans = folders["Mountains"], folders["Oceans"]
        self.mountains = sorted(mountains.glob("*.jpg"))
        self.oceans = sorted(oceans.glob("*.jpg"))
        self.favourites = work / "favourites.txt"
        self.to_remove = work / "to-remove.txt"
        favourites = (
            mountains / "lake-argentino.jpg",
            mountains / "ridges.jpg",
            oceans / "turks-and-caicos.jpg",
        )
        self.favourites.write_text(
            "".join(f"{file}\n" for file in favourites), encoding="utf-8"
        )
        self.to_remove.write_text(
            f"{mountains / 'andes-from-orbit.jpg'}\n", encoding="utf-8"
        )
        self.settings = Settings(
            playlists=(
                PlaylistSettings(
                    id="mountains",
                    name="Mountains",
                    directory=mountains,
                    interval=1800.0,
                ),
                PlaylistSettings(
                    id="oceans",
                    name="Oceans",
                    directory=oceans,
                    order=Order.SORTED,
                    interval=3600.0,
                ),
            ),
            monitors=(
                MonitorSettings(id=CENTRE.id, playlist="mountains"),
                MonitorSettings(id=LEFT.id, playlist="oceans"),
                MonitorSettings(
                    id=RIGHT.id, playlist="oceans", look=Look(ImageMode.FIT)
                ),
            ),
            favourites_file=self.favourites,
            to_remove_file=self.to_remove,
        )
        self.overview = Overview(
            playlists=(
                PlaylistView(
                    id="mountains",
                    name="Mountains",
                    directory=mountains,
                    monitors=(_member(CENTRE),),
                    current=mountains / "lake-argentino.jpg",
                    favourite=True,
                ),
                PlaylistView(
                    id="oceans",
                    name="Oceans",
                    directory=oceans,
                    monitors=(_member(LEFT), _member(RIGHT, Look(ImageMode.FIT))),
                    current=oceans / "waters-around-the-bahamas.jpg",
                    favourite=False,
                    held=True,
                ),
            ),
            monitors=(CENTRE, LEFT, RIGHT),
            favourites_file=self.favourites,
            to_remove_file=self.to_remove,
        )

    def reply_to(self, request: ClientRequest) -> object | None:
        """The daemon's reply; None for a request no page shown here makes."""
        if isinstance(request, SettingsRequest):
            return SettingsResult(settings=self.settings, revision=1)
        if isinstance(request, StatsRequest):
            return self._stats()
        if isinstance(request, UpcomingRequest):
            return UpcomingResult(
                playlist="Mountains", images=tuple(self.mountains[:8])
            )
        if request.command == "status":
            return StatusResult(playlists=self._details(), paused=None)
        return None

    def _details(self) -> tuple[PlaylistDetails, ...]:
        mountains = self.overview.playlists[0]
        oceans = self.overview.playlists[1]
        return (
            PlaylistDetails(
                **dict(mountains),
                order=Order.SHUFFLE,
                interval=1800.0,
                next_in_seconds=1260.0,
                upcoming=tuple(self.mountains[:3]),
            ),
            PlaylistDetails(
                **dict(oceans),
                order=Order.SORTED,
                interval=3600.0,
                next_in_seconds=2400.0,
                upcoming=tuple(self.oceans[1:4]),
            ),
        )

    def _stats(self) -> StatsResult:
        return StatsResult(
            playlists=(
                PlaylistStats(
                    name="Mountains",
                    directory=self.settings.playlists[0].directory,
                    order=Order.SHUFFLE,
                    scan_age=120.0,
                    images=len(self.mountains),
                    seen=6,
                    unseen=len(self.mountains) - 7,
                    skipped=1,
                    favourites_here=2,
                    to_remove_here=1,
                ),
            ),
            favourites=ListStats(self.favourites, listed=3, available=3),
            to_remove=ListStats(self.to_remove, listed=1, available=1),
        )


def _member(monitor: DesktopMonitor, look: Look = Look()) -> MonitorView:
    return MonitorView(id=monitor.id, name=monitor.name, attached=True, look=look)


class StandInClient(Client):
    """The daemon's side of the socket, answered by `World` on `answer()`."""

    def __init__(self, world: World) -> None:
        super().__init__()
        self._world = world
        self._waiting: list[tuple[ClientRequest, ReplyCallback]] = []
        self._dispatch(OverviewChangedEvent(overview=world.overview))

    @override
    def run(self, request: ClientRequest, on_reply: ReplyCallback) -> None:
        self._waiting.append((request, on_reply))

    def answer(self) -> None:
        """Replies to what was asked so far (later, as a daemon would)."""
        waiting, self._waiting = self._waiting, []
        for request, on_reply in waiting:
            reply = self._world.reply_to(request)
            if reply is not None:
                on_reply(reply)  # type: ignore[arg-type]


# -- rendering ---------------------------------------------------------------------


def set_scheme(scheme: Qt.ColorScheme) -> None:
    """The system's light or dark palette, with the default accent."""
    QGuiApplication.styleHints().setColorScheme(scheme)
    palette = QPalette(QGuiApplication.palette())
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        palette.setColor(group, QPalette.ColorRole.Accent, QColor(ACCENT))
        palette.setColor(group, QPalette.ColorRole.Highlight, QColor(ACCENT))
    QApplication.setPalette(palette)


def render(
    app: QApplication, world: World, page: PageName, out: Path, work: Path
) -> None:
    client = StandInClient(world)
    window = MainWindow(client)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    window.show_page(page)
    if page == "settings":
        window.settings.open_playlist("mountains")
    window.show()
    settle(app, client.answer)
    grabbed = work / "grab.png"
    window.grab().save(str(grabbed))
    window.shutdown()
    window.deleteLater()
    app.processEvents()
    with Image.open(grabbed) as image:
        image.save(out, quality=88, method=6)
    print(f"Saved {out.relative_to(ROOT)}", flush=True)


def settle(app: QApplication, answer: Callable[[], None]) -> None:
    end = time.monotonic() + SETTLE_S
    while time.monotonic() < end:
        answer()
        app.processEvents()
        time.sleep(0.02)


if __name__ == "__main__":
    sys.exit(main())
