"""KDE Plasma backend: plasmashell scripting (D-Bus), KScreen, EDID names."""

import sys

assert sys.platform == "linux"  # pylint: disable=wrong-import-position

# pylint: disable=wrong-import-position,wrong-import-order
import json
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, ValidationError

from wallpaper_py.core.model import (
    Monitor,
    MonitorId,
    MonitorNotAttachedError,
    Rectangle,
    WallpaperError,
)

from . import tools
from .edid import DRM, connector_name

PLUGIN = "org.kde.image"
# Plasma's "Scaled and Cropped" (Qt's PreserveAspectCrop), its default:
# the Fill of the other platforms. Any mode shows an exact-size image as
# is; this one is kept so another tool's Tile can't misplace it.
FILL_MODE = 2


class _Model(BaseModel):
    # Output of other programs: read leniently, ignore what isn't used.
    model_config = ConfigDict(extra="ignore")


class _Size(_Model):
    width: int
    height: int


class _Output(_Model):
    """One of KScreen's outputs (`kscreen-doctor -j`)."""

    name: str  # the connector, e.g. "DP-4"
    connected: bool = False
    enabled: bool = False
    size: _Size | None = None  # in pixels, rotated as shown


class _Config(_Model):
    outputs: list[_Output] = []


class _Screen(_Model):
    """A Plasma screen, as `_MONITORS_SCRIPT` prints it."""

    x: int
    y: int
    width: int  # in desktop (logical) coordinates
    height: int
    image: str = ""  # a file URL or path; "" if it shows no image


# Prints, for each connector Plasma has a screen on, its desktop geometry
# and the image its desktop shows in the current activity.
_MONITORS_SCRIPT = """
var names = %(names)s;
var out = {};
for (var i = 0; i < names.length; i++) {
  var screen = screenForConnector(names[i]);
  if (screen < 0) continue;
  var g = screenGeometry(screen);
  var d = desktopForScreen(screen);
  var image = "";
  if (d && d.wallpaperPlugin == "%(plugin)s") {
    d.currentConfigGroup = ["Wallpaper", "%(plugin)s", "General"];
    image = String(d.readConfig("Image") || "");
  }
  out[names[i]] = {x: g.x, y: g.y, width: g.width, height: g.height, image: image};
}
print(JSON.stringify(out));
"""

# Sets the image on the connector's desktop in every activity, so a switch
# of activity keeps the playlist's image; prints how many were set.
_SET_SCRIPT = """
var name = %(name)s, url = %(url)s;
var screen = screenForConnector(name);
var count = 0;
if (screen >= 0) {
  var acts = activities();
  for (var a = 0; a < acts.length; a++) {
    var ds = desktopsForActivity(acts[a]);
    for (var i = 0; i < ds.length; i++) {
      var d = ds[i];
      if (d.screen != screen) continue;
      if (d.wallpaperPlugin != "%(plugin)s") d.wallpaperPlugin = "%(plugin)s";
      d.currentConfigGroup = ["Wallpaper", "%(plugin)s", "General"];
      if (String(d.readConfig("FillMode")) != "%(fill)d")
        d.writeConfig("FillMode", %(fill)d);
      d.writeConfig("Image", url);
      count++;
    }
  }
}
print(count);
"""


def _path(image: str) -> Path | None:
    """Plasma's Image setting (a file URL, or a plain path) -> a path."""
    if not image:
        return None
    if image.startswith("file:"):
        return Path(unquote(urlsplit(image).path))
    return Path(image)


class KdeBackend:
    """
    A monitor's id is its connector ("DP-4"): Plasma and KWin go by it too,
    it stays while the monitor is off, and two monitors of one model differ
    by it. Like a Windows device path, it belongs to the port: a monitor
    moved to another port is a new monitor.

    KScreen lists only the outputs connected now, so a monitor switched off
    drops out of its list; the backend remembers every one it saw and lists
    it as detached, as Windows does. Positions are Plasma's (logical on
    Wayland), sizes in pixels are KScreen's.
    """

    def __init__(self, run: tools.Runner = tools.run, drm: Path = DRM) -> None:
        self._run = run
        self._drm = drm
        # Seen this process: id -> name (EDID is unreadable while off).
        self._known: dict[MonitorId, str] = {}

    def monitors(self) -> list[Monitor]:
        # A connected output may be disabled (detached); an empty port
        # (listed on X11) is no monitor.
        connected = [o for o in self._outputs() if o.connected]
        screens = self._screens([o.name for o in connected if o.enabled])
        result: list[Monitor] = []
        for output in connected:
            monitor_id = MonitorId(output.name)
            name = connector_name(output.name, self._drm) or self._known.get(
                monitor_id, output.name
            )
            self._known[monitor_id] = name
            screen = screens.get(output.name)
            rect = None
            if screen is not None and output.size is not None:
                rect = Rectangle(
                    screen.x,
                    screen.y,
                    screen.x + screen.width,
                    screen.y + screen.height,
                    pixels=(output.size.width, output.size.height),
                )
            image = _path(screen.image) if screen is not None else None
            result.append(Monitor(monitor_id, name, rect, image))
        listed = {m.id for m in result}
        for monitor_id, name in self._known.items():
            if monitor_id not in listed:
                result.append(Monitor(monitor_id, name, None))
        return result

    def set_wallpaper(self, monitor_id: MonitorId, image: Path) -> None:
        script = _SET_SCRIPT % {
            "name": json.dumps(str(monitor_id)),
            # Not resolved: Plasma reports the path back (Monitor.wallpaper),
            # and the setter alternates render files by comparing it with
            # its own, unresolved ones (a symlinked ~/.cache).
            "url": json.dumps(image.absolute().as_uri()),
            "plugin": PLUGIN,
            "fill": FILL_MODE,
        }
        if self._evaluate(script).strip() in ("", "0"):
            raise MonitorNotAttachedError(f"{monitor_id} is not attached")

    def _outputs(self) -> list[_Output]:
        output = self._run(["kscreen-doctor", "-j"])
        try:
            return _Config.model_validate_json(output).outputs
        except ValidationError as err:
            raise WallpaperError(
                f"kscreen-doctor's output is not understood: {err}"
            ) from err

    def _screens(self, names: Sequence[str]) -> dict[str, _Screen]:
        if not names:
            return {}
        script = _MONITORS_SCRIPT % {"names": json.dumps(list(names)), "plugin": PLUGIN}
        output = self._evaluate(script)
        try:
            return {
                name: _Screen.model_validate(screen)
                for name, screen in json.loads(output).items()
            }
        except (ValueError, AttributeError) as err:
            raise WallpaperError(
                f"plasmashell's answer is not understood: {err}"
            ) from err

    def _evaluate(self, script: str) -> str:
        """Run a Plasma desktop script; what it prints."""
        reply = tools.call(
            self._run,
            "org.kde.plasmashell",
            "/PlasmaShell",
            "org.kde.PlasmaShell.evaluateScript",
            ["s", script],
        )
        if not reply or not isinstance(reply[0], str):
            raise WallpaperError(f"plasmashell's answer is not understood: {reply}")
        return reply[0]
