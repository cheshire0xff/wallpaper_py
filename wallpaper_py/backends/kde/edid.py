"""A monitor's name from its EDID, read from sysfs (no privileges needed)."""

import sys

assert sys.platform == "linux"  # pylint: disable=wrong-import-position

# pylint: disable=wrong-import-position,wrong-import-order
from pathlib import Path

DRM = Path("/sys/class/drm")

_HEADER = bytes([0, 255, 255, 255, 255, 255, 255, 0])
_NAME_TAG = 0xFC  # display descriptor: the monitor's name


def monitor_name(edid: bytes) -> str | None:
    """
    The name in the EDID's name descriptor (e.g. "AW3225QF"), else the
    manufacturer and product code (e.g. "DELA246", as Windows ids spell
    it); None if this is no EDID.
    """
    if len(edid) < 128 or not edid.startswith(_HEADER):
        return None
    for start in range(54, 126, 18):
        block = edid[start : start + 18]
        if block[:3] == b"\0\0\0" and block[3] == _NAME_TAG:
            name = block[5:].split(b"\n")[0].decode("cp437").strip()
            if name:
                return name
    packed = int.from_bytes(edid[8:10], "big")
    letters = "".join(chr(64 + (packed >> s & 0x1F)) for s in (10, 5, 0))
    product = int.from_bytes(edid[10:12], "little")
    return f"{letters}{product:04X}"


def connector_name(connector: str, drm: Path = DRM) -> str | None:
    """
    The name of the monitor on a connector ("DP-4": sysfs has it as
    card<N>-DP-4); None while nothing readable is plugged in.
    """
    for path in sorted(drm.glob(f"card*-{connector}/edid")):
        try:
            name = monitor_name(path.read_bytes())
        except OSError:
            continue
        if name:
            return name
    return None
