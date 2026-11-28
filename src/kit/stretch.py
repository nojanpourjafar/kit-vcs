"""Reversing a sound, and stretching a loop to another tempo without changing its pitch.

Reverse plays the sound backwards: the reversed cymbal or vocal that swells
into a drop. Stretch makes a loop longer or shorter at the same pitch, so a
loop at 140 BPM becomes a real file at 150 that drops into any project; the
"Loops at BPM" player only does this while listening.

Stretching uses WSOLA (waveform-similarity overlap-add; Verhelst and Roelands,
1993): the loop is cut into short overlapping pieces, about 46 ms long, which
are laid down at the new spacing and blended. Pieces laid down blindly would
cancel where their waves do not line up, so each piece is taken from wherever,
near its place, it lines up best with what is already laid down. Drums keep
their punch best when the change is small; past about 25% faster or slower,
any stretcher starts to smear hits. Both make a new file beside the original.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from .fix import read, save
from .repo import KitError
from .tags import from_name

WINDOW = 0.046      # seconds in each piece
SEARCH = 0.012      # how far either side of its place a piece may be taken from
COARSE = 8          # the search is first made on every 8th sample, then refined
MOST = 2.0          # at most twice as fast or slow


def reverse(repo, path: str) -> Path:
    """A reversed copy, "<name> reversed.wav", in the same format."""
    values, rate, channels, width, is_float = read(repo, path, "reverse")
    frames = [values[i:i + channels] for i in range(0, len(values) - channels + 1, channels)]
    flipped = [v for frame in reversed(frames) for v in frame]
    return save(repo, path, "reversed", flipped, rate, channels, width, is_float, in_place=False)


def wsola(channels: list[list[float]], rate: int, ratio: float) -> list[list[float]]:
    """The channels made `ratio` times as long (2 is half the speed), at the same pitch."""
    size = max(64, int(WINDOW * rate)) // 2 * 2
    half = size // 2
    window = [0.5 - 0.5 * math.cos(2 * math.pi * i / size) for i in range(size)]  # Hann: halves sum to 1
    search = int(SEARCH * rate)
    length = len(channels[0])
    out_length = int(length * ratio)
    out = [[0.0] * (out_length + size) for _ in channels]
    mono = [sum(values) / len(values) for values in zip(*channels)]
    padded = lambda k: mono[k] if 0 <= k < length else 0.0

    source = 0   # where the last piece was taken from
    for start in range(0, out_length, half):
        if start == 0:
            take = 0
        else:
            # What would naturally follow the last piece, half a window on: the new
            # piece should look like that, so the waves line up.
            follow = source + half
            ideal = int(start / ratio)
            low, high = max(0, ideal - search), min(length - size, ideal + search)
            if high < low:
                take = max(0, min(length - size, ideal))
            else:
                # How alike a candidate is to what should follow, measured on every
                # 4th sample: first at every 8th place, then each place near the best.
                points = range(0, size, 4)
                target = [padded(follow + i) for i in points]
                alike = lambda k: sum(t * padded(k + i) for t, i in zip(target, points))
                take = max(range(low, high + 1, COARSE), key=alike)
                take = max(range(max(low, take - COARSE + 1), min(high, take + COARSE - 1) + 1), key=alike)
        source = take
        for c, channel in enumerate(channels):
            row = out[c]
            for i in range(size):
                j = take + i
                if j < length:
                    row[start + i] += channel[j] * window[i]
    return [row[:out_length] for row in out]


def stretch(repo, path: str, bpm: float, to_bpm: float | None = None) -> tuple[Path, float]:
    """A copy of the loop at `path` stretched to `bpm`, "<name> 150bpm.wav". Returns it and the loop's tempo.

    `to_bpm` aside, the loop's tempo comes from its name, else from listening.
    """
    from .audio import tempo

    named = from_name(path)["bpm"]
    now = to_bpm or named or tempo(repo.folder / path, path)
    if not now:
        raise KitError(f"Kit cannot tell the tempo of {Path(path).name}: put it in the name, like 140bpm")
    ratio = now / bpm
    if not 1 / MOST <= ratio <= MOST:
        raise KitError(f"{bpm:g} BPM is too far from {now:g}: at most twice as fast or slow")
    if abs(ratio - 1) < 0.002:
        raise KitError(f"{Path(path).name} is already at {now:g} BPM")
    values, rate, channels, width, is_float = read(repo, path, "stretch")
    centre = 128 if width == 1 else 0
    split = [[values[i] - centre for i in range(c, len(values), channels)] for c in range(channels)]
    longer = wsola(split, rate, ratio)
    if is_float:
        merged = [longer[c][i] for i in range(len(longer[0])) for c in range(channels)]
    else:
        top = (1 << (8 * width - 1)) - 1
        low, high = (0, 255) if width == 1 else (-top - 1, top)
        merged = [min(high, max(low, int(round(longer[c][i] + centre)))) for i in range(len(longer[0])) for c in range(channels)]
    word = f"{round(bpm)}bpm" if abs(bpm - round(bpm)) < 0.05 else f"{bpm:.1f}bpm"
    out = save(repo, path, word, merged, rate, channels, width, is_float, in_place=False)
    # "Groove 140bpm.wav" becomes "Groove 150bpm.wav", not "Groove 140bpm 150bpm.wav".
    stem = re.sub(r"[\s_-]*\d{2,3}[\s_-]?bpm", "", Path(path).stem, flags=re.I).strip()
    if stem and stem != Path(path).stem:
        target, number = out.with_name(f"{stem} {word}.wav"), 2
        while target.exists():
            target, number = out.with_name(f"{stem} {word} {number}.wav"), number + 1
        out = out.rename(target)
    return out, now
