"""Fixing what `problems` finds, where it can be fixed without changing the sound.

  * a late start: the silence before a one-shot is cut, keeping 1 ms before
    the first sound so the very start of the attack is not lost;
  * a click at the end of a one-shot: its last 4 ms fade to silence;
  * a click where a loop repeats: its first and last 3 ms fade, so the end and
    the start both meet at silence. The length stays the same, so the loop
    stays in time.

Clipping cannot be fixed: the tops of the wave are gone. Silence is left alone.

A sound saved in a version is fixed in place: Kit already keeps the original,
and the Before button plays it. A sound not saved yet, or not a WAV file, is
written beside it as "<name> fixed.wav", so nothing is ever lost.
"""

from __future__ import annotations

import struct
from pathlib import Path

from .audio import _WAVE_ERRORS
from .chop import _write_wav
from .problems import QUIET
from .repo import KitError
from .wavfile import can_read, open_wav

LEAD = 0.001        # a trimmed start keeps 1 ms before the first sound
TAIL_FADE = 0.004   # a one-shot fades out over its last 4 ms
SEAM_FADE = 0.003   # a loop fades in and out over 3 ms at each end
FIXABLE = {"late", "click"}


def _decode(data: bytes, width: int, is_float: bool) -> list:
    if width == 3:  # 24-bit: no struct code, so through ints
        return [int.from_bytes(data[i:i + 3], "little", signed=True) for i in range(0, len(data) - 2, 3)]
    code = {1: "B", 2: "h", 4: "f" if is_float else "i", 8: "d"}[width]
    return list(struct.unpack(f"<{len(data) // width}{code}", data[: len(data) // width * width]))


def _encode(values: list, width: int, is_float: bool) -> bytes:
    if width == 3:
        return b"".join(int(v).to_bytes(3, "little", signed=True) for v in values)
    code = {1: "B", 2: "h", 4: "f" if is_float else "i", 8: "d"}[width]
    return struct.pack(f"<{len(values)}{code}", *values)


def repair(values: list, rate: int, channels: int, width: int, is_float: bool, found: set[str], loop: bool) -> list:
    """The samples (all channels together, as stored) with the fixable problems in `found` fixed."""
    centre = 128 if width == 1 else 0  # 8-bit WAV is unsigned: silence is 128
    full = 1.0 if is_float else float(1 << (8 * width - 1))
    values = list(values)

    def scale(frame_from: int, frame_to: int, gain) -> None:
        for frame in range(max(0, frame_from), min(frame_to, len(values) // channels)):
            g = gain(frame)
            for c in range(channels):
                i = frame * channels + c
                v = centre + (values[i] - centre) * g
                values[i] = v if is_float else int(round(v))

    if "late" in found and not loop:
        quiet = QUIET * full
        first = next((i // channels for i, v in enumerate(values) if abs(v - centre) >= quiet), 0)
        start = max(0, first - int(LEAD * rate))
        values = values[start * channels:]
    frames = len(values) // channels
    if "click" in found:
        fade = min(frames, max(1, int((SEAM_FADE if loop else TAIL_FADE) * rate)))
        scale(frames - fade, frames, lambda f: max(0.0, 1 - (f - (frames - fade) + 1) / fade))
        if loop:
            scale(0, fade, lambda f: f / fade)
    return values


def read(repo, path: str, verb: str) -> tuple[list, int, int, int, bool]:
    """A sound's samples as stored (all channels together), with its rate, channels, width and float flag."""
    if not can_read(path):
        raise KitError(f"Kit can only {verb} WAV, AIFF and FLAC files, not {path}")
    try:
        with open_wav(repo.folder / path) as wav:
            rate, channels, width, is_float = wav.rate, wav.channels, wav.width, wav.is_float
            raw = wav.readframes(wav.frames)
    except _WAVE_ERRORS as err:
        raise KitError(f"cannot read {path}: {err}") from None
    return _decode(raw, width, is_float), rate, channels, width, is_float


def save(repo, path: str, word: str, values: list, rate: int, channels: int, width: int, is_float: bool,
         in_place: bool = True) -> Path:
    """Write changed samples: over the sound if a version keeps the original, else beside it as "<name> <word>.wav".

    With `in_place` False, always beside it: for a new sound made from this one.
    """
    source = repo.folder / path
    entry = repo.working().get(path)
    if in_place and entry is not None and repo.store.has(entry["hash"]) and path.lower().endswith(".wav"):
        out = source
    else:
        out = source.with_name(f"{source.stem} {word}.wav")
        number = 2
        while out.exists():
            out = source.with_name(f"{source.stem} {word} {number}.wav")
            number += 1
    tmp = out.with_name(out.name + ".tmp")
    _write_wav(tmp, _encode(values, width, is_float), rate, channels, width, is_float)
    tmp.replace(out)
    return out


def fix(repo, path: str, found: list[dict], loop: bool = False) -> tuple[Path, list[str]]:
    """Fix the sound at `path` in the kit. Returns the file written and what was fixed, in words.

    `found` is what problems.check said about it.
    """
    kinds_found = {problem["kind"] for problem in found} & FIXABLE
    if not kinds_found:
        raise KitError("there is nothing Kit can fix in " + path
                       + (": clipping cannot be undone" if any(p["kind"] == "clipping" for p in found) else ""))
    values, rate, channels, width, is_float = read(repo, path, "fix")
    values = repair(values, rate, channels, width, is_float, kinds_found, loop)
    out = save(repo, path, "fixed", values, rate, channels, width, is_float)
    done = []
    if "late" in kinds_found and not loop:
        done.append("trimmed the silence at the start")
    if "click" in kinds_found:
        done.append("faded both ends where it repeats" if loop else "faded out the end")
    return out, done
