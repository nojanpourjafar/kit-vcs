"""Small audio details for nicer output, like "0.35 s, 44.1 kHz, mono".

Standard library only, so it reads plain PCM WAV files and quietly gives up on
anything else. Version 4 (web previews with waveforms) will grow from here.
"""

from __future__ import annotations

import wave
from pathlib import Path


def describe(file_path: Path, name: str) -> str | None:
    """Describe a WAV file, or return None if it is not one we can read.

    `name` is the file's real name. It is passed separately because stored
    objects are named by hash and have no ".wav" on the end.
    """
    if not name.lower().endswith(".wav"):
        return None
    try:
        with wave.open(str(file_path), "rb") as wav:
            rate = wav.getframerate()
            seconds = wav.getnframes() / rate
            channels = wav.getnchannels()
    except (wave.Error, EOFError, OSError, ZeroDivisionError):
        return None
    layout = {1: "mono", 2: "stereo"}.get(channels, f"{channels} channels")
    return f"{seconds:.2f} s, {rate / 1000:g} kHz, {layout}"
