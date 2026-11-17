"""The Linux rotation sound's choice of player; nothing is played."""

import subprocess
import sys
from pathlib import Path

import pytest

if sys.platform != "linux":
    pytest.skip("Linux only", allow_module_level=True)
# Never fails (skipped above); tells mypy the rest is Linux-only.
assert sys.platform == "linux"

# pylint: disable=wrong-import-position
from wallpaper_py.backends import pipewire
from wallpaper_py.core.model import WallpaperError


class FakePopen:
    """subprocess.Popen with only `installed` players on the PATH."""

    def __init__(self, installed: set[str]) -> None:
        self.installed = installed
        self.started: list[list[str]] = []

    def __call__(self, argv: list[str], **_: object) -> None:
        if argv[0] not in self.installed:
            raise FileNotFoundError(argv[0])
        self.started.append(argv)


@pytest.mark.parametrize(
    ("installed", "player"),
    [({"pw-play", "paplay"}, "pw-play"), ({"paplay"}, "paplay")],
)
def test_plays_with_the_first_player_found(
    monkeypatch: pytest.MonkeyPatch, installed: set[str], player: str
) -> None:
    popen = FakePopen(installed)
    monkeypatch.setattr(subprocess, "Popen", popen)
    pipewire.play_sound(Path("/sounds/rotation.wav"))
    assert popen.started == [[player, "/sounds/rotation.wav"]]


def test_no_player_says_what_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "Popen", FakePopen(set()))
    with pytest.raises(WallpaperError, match="install pw-play"):
        pipewire.play_sound(Path("/sounds/rotation.wav"))
