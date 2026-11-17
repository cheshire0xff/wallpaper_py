#!/usr/bin/env python3
"""
Write wallpaper_py/app/sounds/rotation.wav, the rotation sound: one high,
glass-like ping (C7), half a second, quiet. Synthesised here, so it is ours
and the same on every run.

    python scripts/rotation_sound.py
"""

import math
import struct
import sys
import wave
from pathlib import Path

SOUND_FILE = (
    Path(__file__).parents[1] / "wallpaper_py" / "app" / "sounds" / "rotation.wav"
)
RATE = 44_100  # samples per second; 16-bit mono
PEAK = 0.25  # of full scale: a notification, not an alarm
# (start s, frequency Hz, length s)
NOTES = ((0.0, 2093.0, 0.5),)
# Glass-like partials: (frequency ratio, loudness, decay per second)
PARTIALS = ((1.0, 1.0, 9.0), (2.76, 0.4, 14.0), (5.4, 0.15, 22.0))
ATTACK_S = 0.005  # no click at the start
RELEASE_S = 0.04  # to silence at the end


def note(t: float, frequency: float, length: float) -> float:
    if not 0.0 <= t < length:
        return 0.0
    envelope = min(1.0, t / ATTACK_S, (length - t) / RELEASE_S)
    return envelope * sum(
        loudness * math.exp(-decay * t) * math.sin(2 * math.pi * ratio * frequency * t)
        for ratio, loudness, decay in PARTIALS
    )


def samples() -> list[float]:
    total = max(start + length for start, _, length in NOTES)
    values = [
        sum(note(i / RATE - start, f, length) for start, f, length in NOTES)
        for i in range(round(total * RATE))
    ]
    scale = PEAK / max(abs(v) for v in values)
    return [v * scale for v in values]


def main() -> int:
    data = b"".join(struct.pack("<h", round(v * 32767)) for v in samples())
    SOUND_FILE.parent.mkdir(parents=True, exist_ok=True)
    with wave.Wave_write(str(SOUND_FILE)) as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(data)
    print(f"Wrote {SOUND_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
