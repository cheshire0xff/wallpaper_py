"""
Play a sound file on Linux with the sound server's own player: `pw-play`
(PipeWire, Plasma 6's default), else `paplay` (PulseAudio), without waiting.
"""

import sys

assert sys.platform == "linux"  # pylint: disable=wrong-import-position

# pylint: disable=wrong-import-position,wrong-import-order
import subprocess
from pathlib import Path

from wallpaper_py.core.model import WallpaperError

PLAYERS = ("pw-play", "paplay")


def play_sound(path: Path) -> None:
    """
    Starts the first player found and returns; the player's own failure
    (no sound server, a file it can't read) is not waited for. A finished
    player is reaped by the next subprocess the daemon starts.

    Raises:
        WallpaperError: neither player is installed, or it can't be run.
    """
    for player in PLAYERS:
        try:
            subprocess.Popen(  # pylint: disable=consider-using-with
                [player, str(path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except FileNotFoundError:
            continue
        except OSError as err:  # not executable, ...
            raise WallpaperError(f"{player} can't be run: {err}") from err
    raise WallpaperError(
        "No sound player: install pw-play (PipeWire; Ubuntu: pipewire-bin) "
        "or paplay (PulseAudio; Ubuntu: pulseaudio-utils)"
    )
