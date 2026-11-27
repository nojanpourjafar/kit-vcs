"""Turning a sound up or down to a loudness, so a kit's sounds sit at the same level.

The whole sound is turned by one amount (a gain), so nothing about it changes
but how loud it is. Turning up stops where the loudest sample would pass
-1 dBFS: past full scale it would clip, and a little room keeps it clear of
clipping when converted to MP3 or another rate. When that limit is hit, the
sound ends up quieter than asked, and Kit says by how much.

Like Fix, a sound saved in a version is changed in place (Before plays the
original), and any other sound is written beside it as "<name> levelled.wav".
"""

from __future__ import annotations

from pathlib import Path

from .fix import read, save
from .loudness import measure_file
from .repo import KitError

CEILING = -1.0     # dBFS: the loudest sample is never turned up past this
CLOSE = 0.5        # LU: a sound this close to the target is left alone


def level(repo, path: str, target: float) -> tuple[Path | None, float, float]:
    """Turn the sound at `path` to `target` LUFS.

    Returns the file written (None if it was close enough already), the gain
    applied in dB, and how loud the result is in LUFS.
    """
    measured = measure_file(repo.folder / path, path)
    if measured is None:
        raise KitError(f"{path} is silent, or Kit cannot read it")
    gain = target - measured["lufs"]
    if gain > 0:
        gain = min(gain, CEILING - measured["peak"])  # never past the ceiling
    if abs(gain) < CLOSE:
        return None, 0.0, measured["lufs"]
    values, rate, channels, width, is_float = read(repo, path, "level")
    factor = 10 ** (gain / 20)
    centre = 128 if width == 1 else 0  # 8-bit WAV is unsigned: silence is 128
    if is_float:
        values = [v * factor for v in values]
    else:
        top = (1 << (8 * width - 1)) - 1
        low = 0 if width == 1 else -top - 1
        high = 255 if width == 1 else top
        values = [min(high, max(low, int(round(centre + (v - centre) * factor)))) for v in values]
    out = save(repo, path, "levelled", values, rate, channels, width, is_float)
    return out, round(gain, 1), round(measured["lufs"] + gain, 1)


def describe(path: str, out: Path | None, gain: float, result: float, target: float) -> str:
    """One line on what happened, for the app and the terminal."""
    name = Path(path).name
    if out is None:
        return f"{name} is already at {result:.1f} LUFS"
    way = "up" if gain > 0 else "down"
    where = "" if out.name == name else f", as {out.name}"
    short = f" ({target - result:.1f} LU short: any louder would clip)" if target - result >= CLOSE else ""
    return f"Turned {name} {way} {abs(gain):.1f} dB to {result:.1f} LUFS{short}{where}"
