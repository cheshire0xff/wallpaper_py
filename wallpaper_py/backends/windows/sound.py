"""Play a WAV file through the Windows sound API (winsound), without waiting."""

import sys

assert sys.platform == "win32"  # pylint: disable=wrong-import-position

# pylint: disable=wrong-import-position,wrong-import-order
import winsound
from pathlib import Path

from wallpaper_py.core.model import WallpaperError


def play_sound(path: Path) -> None:
    """
    Returns at once (SND_ASYNC); a sound still playing is cut off. No
    default beep when the file can't be played (SND_NODEFAULT).

    Raises:
        WallpaperError: Windows can't play it.
    """
    flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
    try:
        winsound.PlaySound(str(path), flags)
    except RuntimeError as err:
        raise WallpaperError(f"Can't play {path}: {err}") from err
