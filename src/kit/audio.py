"""Reading audio files: length, sample rate, and a waveform outline.

Standard library only, so it understands plain PCM WAV files and quietly gives
up on anything else (compressed formats, 32-bit float WAVs).
"""

from __future__ import annotations

import sys
import wave
from array import array
from pathlib import Path

_WAVE_ERRORS = (wave.Error, EOFError, OSError, ZeroDivisionError, ValueError)


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
    except _WAVE_ERRORS:
        return None
    layout = {1: "mono", 2: "stereo"}.get(channels, f"{channels} channels")
    return f"{seconds:.2f} s, {rate / 1000:g} kHz, {layout}"


def peaks(file_path: Path, name: str, buckets: int = 96) -> list[float] | None:
    """The loudest level in each of `buckets` equal slices, scaled to 0..1.

    This is the outline drawn as a waveform. Returns None for files we
    cannot read.
    """
    if not name.lower().endswith(".wav"):
        return None
    try:
        with wave.open(str(file_path), "rb") as wav:
            width, frames = wav.getsampwidth(), wav.getnframes()
            if frames == 0 or width not in (1, 2, 3, 4):
                return None
            per_bucket = max(1, -(-frames // buckets))  # round up, so every frame is covered
            levels = []
            while chunk := wav.readframes(per_bucket):
                levels.append(_loudest(chunk, width))
    except _WAVE_ERRORS:
        return None
    top = max(levels, default=0.0)
    if top <= 0:
        return [0.0] * len(levels)
    return [level / top for level in levels]


def _loudest(chunk: bytes, width: int) -> float:
    """Peak level of a block of samples, as a fraction of full scale."""
    if width == 1:  # 8-bit WAV is unsigned, centred on 128
        return max(abs(b - 128) for b in chunk) / 128
    if width == 2:
        samples = array("h", chunk[: len(chunk) // 2 * 2])
        if sys.byteorder == "big":
            samples.byteswap()
        return max(max(samples), -min(samples)) / 32768 if samples else 0.0
    # 24- and 32-bit: the top byte of each sample is enough for an outline.
    top_bytes = array("b", chunk[width - 1 :: width])
    return max(max(top_bytes), -min(top_bytes)) / 128 if top_bytes else 0.0
