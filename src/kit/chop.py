"""Chopping a loop into one-shots: each hit saved as its own file.

A drum loop is a run of hits: kick, hat, snare, hat... Producers chop loops to
reuse those hits on their own. Kit finds where each hit starts (an onset: the
loudness jumps up), cuts the loop there, and saves every piece as a WAV file
in the same format as the loop, numbered in order:

    Loops/Groove.wav  ->  Loops/Groove chops/Groove 01.wav, Groove 02.wav, ...

The pieces are not named by kind. Measured on generated loops, guessing the
kind of a piece was right only about half the time: a hat cut from a loop
still carries the end of the kick before it, which sounds like a kick. Kit's
kind buttons sort the pieces like any other sounds.

The new files are ordinary sounds in the kit, ready to save as a version.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

from .audio import _WAVE_ERRORS, _floats, _samples
from .repo import KitError
from .wavfile import can_read, open_wav

HOP_SECONDS = 0.005    # loudness is measured every 5 ms
RISE = 2.5             # a hit starts where the loudness is this many times what it was just before
LOOKBACK = 0.03        # "just before" means the quietest point in the previous 30 ms
FLOOR = 0.02           # hits quieter than 2% of the loudest are ignored: they are tails and noise
MIN_GAP = 0.06         # two hits closer than 60 ms are one hit (a flam, a clap's bursts)
FADE = 0.004           # each piece fades out over its last 4 ms, so it ends without a click
LEAD = 0.002           # and starts 2 ms early, so the very start of the attack is kept


def onsets(samples, rate: int, min_gap: float = MIN_GAP) -> list[int]:
    """Where each hit starts, as sample positions (mono samples, any scale).

    Two loudness curves are watched, each measured over 20 ms every 5 ms (one
    slice of 5 ms would rise and fall with each cycle of a low kick, and look
    like many hits): the sound itself, where kicks and 808s show, and its
    changes from sample to sample, which brings out the highs, so a hat
    still shows while a kick is ringing underneath it. A hit starts where
    either curve jumps up.
    """
    hop = max(1, int(rate * HOP_SECONDS))
    width = 4 * hop
    starts = range(0, max(0, len(samples) - width + 1), hop)
    diffs = [0.0] + [b - a for a, b in zip(samples, samples[1:])]
    curves = []
    for signal in (samples, diffs):
        squares = [x * x for x in signal]
        total = sum(squares[:width])
        curve = []
        for i in starts:  # a running sum over the window
            curve.append(total)
            total += sum(squares[i + width:i + width + hop]) - sum(squares[i:i + hop])
        curves.append(curve)
    if not starts or max(curves[0]) == 0:
        return []
    back = max(1, int(LOOKBACK / HOP_SECONDS))
    gap = max(1, int(min_gap / HOP_SECONDS))
    found: list[int] = []
    for i in range(len(starts)):
        rising = False
        for curve in curves:
            loud = max(curve)
            before = min(curve[max(0, i - back):i], default=0.0)
            if curve[i] > RISE * before and curve[i] > FLOOR * loud and (i == 0 or curve[i] >= curve[i - 1]):
                rising = True
        if rising and (not found or i - found[-1] >= gap):
            found.append(i)
    # The window that first catches a hit can start up to 20 ms before the hit:
    # what is new in it is its last part. There, the hit starts at the first
    # sample whose change is a good part of the biggest change: the front of
    # the attack.
    exact = []
    for i in found:
        # At the very start of the file the hit can be anywhere in the first window.
        first = 0 if i * hop < width else i * hop + width - 2 * hop
        last = min(len(diffs), i * hop + width + hop)
        biggest = max((abs(d) for d in diffs[first:last]), default=0.0)
        j = next((k for k in range(first, last) if abs(diffs[k]) >= 0.3 * biggest), first)
        exact.append(max(0, j - int(LEAD * rate)))
    return exact


def chop(path: Path, name: str, min_gap: float = MIN_GAP) -> list[Path]:
    """Cut the sound at `path` at each hit and save the pieces in a "<name> chops" folder beside it.

    Returns the new files. A folder of chops that is already there is never
    overwritten: the new one is "<name> chops 2", and so on.
    """
    if not can_read(name):
        raise KitError(f"Kit can only chop WAV, AIFF and FLAC files, not {name}")
    try:
        with open_wav(path) as wav:
            rate, channels, width, is_float = wav.rate, wav.channels, wav.width, wav.is_float
            raw = wav.readframes(wav.frames)
    except _WAVE_ERRORS as err:
        raise KitError(f"cannot read {name}: {err}") from None
    block = channels * width
    numbers = _floats(raw, width) if is_float else _samples(raw, width)
    mono = [sum(numbers[i:i + channels]) / channels for i in range(0, len(numbers) - channels + 1, channels)]
    starts = onsets(mono, rate, min_gap)
    if len(starts) < 2:
        raise KitError(f"{name} has fewer than two hits, so there is nothing to chop")

    stem = Path(name).stem
    folder = path.parent / f"{stem} chops"
    number = 2
    while folder.exists():
        folder = path.parent / f"{stem} chops {number}"
        number += 1
    folder.mkdir()

    written = []
    ends = starts[1:] + [len(mono)]
    for n, (start, end) in enumerate(zip(starts, ends), 1):
        piece = _fade_out(raw[start * block:end * block], rate, channels, width, is_float)
        out = folder / f"{stem} {n:02d}.wav"
        _write_wav(out, piece, rate, channels, width, is_float)
        written.append(out)
    return written


def _fade_out(data: bytes, rate: int, channels: int, width: int, is_float: bool) -> bytes:
    """The piece with its last few milliseconds faded to silence, so it does not end in a click."""
    frames = len(data) // (channels * width)
    fade = min(frames, max(1, int(FADE * rate)))
    if width == 3:  # 24-bit: no struct code, so through ints
        values = [int.from_bytes(data[i:i + 3], "little", signed=True) for i in range(0, len(data), 3)]
    else:
        code = {1: "B", 2: "h", 4: "f" if is_float else "i", 8: "d"}[width]
        values = list(struct.unpack(f"<{len(data) // width}{code}", data))
    centre = 128 if width == 1 else 0  # 8-bit WAV is unsigned: silence is 128
    first = (frames - fade) * channels
    for i in range(first, frames * channels):
        gain = 1 - ((i // channels) - (frames - fade) + 1) / fade
        v = centre + (values[i] - centre) * max(0.0, gain)
        values[i] = v if is_float else int(round(v))
    if width == 3:
        return b"".join(v.to_bytes(3, "little", signed=True) for v in values)
    return struct.pack(f"<{len(values)}{code}", *values)


def _write_wav(path: Path, data: bytes, rate: int, channels: int, width: int, is_float: bool) -> None:
    fmt = struct.pack("<HHIIHH", 3 if is_float else 1, channels, rate, rate * channels * width, channels * width, width * 8)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    if len(data) % 2:
        body += b"\0"  # chunks are padded to an even length
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
