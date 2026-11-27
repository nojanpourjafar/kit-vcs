"""Tuning a one-shot to a note: an 808 in G becomes an 808 in F# for a song in F# minor.

Kit measures the exact pitch the sound plays (see audio.hz_of) and plays it
faster or slower by just the right amount, the way a sampler does: higher
notes come out shorter, lower ones longer, and the character of the sound
moves with it, as it would on any sampler or in FL Studio's Channel settings.
The note goes to the nearest octave, so no sound moves more than 6 semitones.
A sound a little out of tune is corrected too: it lands on the note exactly.

The tuned sound is a new file beside the original, "808 Glide in F#.wav", in
the same format. The original is never changed. Loops are not tuned: playing
a loop faster changes its tempo (Loops at BPM does that).
"""

from __future__ import annotations

import math
from pathlib import Path

from .audio import NOTE_NAMES, hz_of
from .fix import read, save
from .problems import channels_of
from .repo import KitError
from .tags import from_name

CLOSE_CENTS = 10   # a sound within this many cents of the note is left alone
FLATS = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}


def pitch(repo, path: str) -> float:
    """The exact pitch of the sound at `path`, in Hz."""
    read_back = channels_of(repo.folder / path, path)
    if read_back is None:
        raise KitError(f"Kit can only tune WAV, AIFF and FLAC files, not {path}")
    channels, rate = read_back
    mono = [sum(values) / len(values) for values in zip(*channels)]
    hz = hz_of(mono, rate)
    if hz is None:
        raise KitError(f"{Path(path).name} has no clear note to tune (snares, hats and claps do not)")
    return hz


def shift_for(hz: float, note: str) -> float:
    """Semitones (with a fraction) from `hz` to the nearest `note`: between -6 and +6."""
    target = NOTE_NAMES.index(FLATS.get(note, note))
    exact = 69 + 12 * math.log2(hz / 440)  # MIDI number, with cents
    shift = (target - exact) % 12
    return shift - 12 if shift > 6 else shift


def resample(values: list, channels: int, ratio: float) -> list[float]:
    """Interleaved samples played `ratio` times as fast (2 is an octave up), with cubic interpolation."""
    frames = len(values) // channels
    out_frames = max(1, int((frames - 1) / ratio))
    out = []
    for j in range(out_frames):
        position = j * ratio
        i = int(position)
        t = position - i
        for c in range(channels):
            at = lambda k: values[min(frames - 1, max(0, k)) * channels + c]
            p0, p1, p2, p3 = at(i - 1), at(i), at(i + 1), at(i + 2)
            # Catmull-Rom: a smooth curve through the four samples around the point.
            out.append(p1 + 0.5 * t * (p2 - p0 + t * (2 * p0 - 5 * p1 + 4 * p2 - p3 + t * (3 * (p1 - p2) + p3 - p0))))
    return out


def tune(repo, path: str, note: str) -> tuple[Path | None, str, float]:
    """Tune the sound at `path` to `note`. Returns the new file (None if it is in tune already),
    the note it played, and the shift in semitones."""
    note = FLATS.get(note, note)
    if note not in NOTE_NAMES:
        raise KitError(f"{note} is not a note: use C, C#, D ... B")
    if from_name(path)["bpm"]:
        raise KitError(f"{Path(path).name} is a loop: tuning it would change its tempo")
    hz = pitch(repo, path)
    played = NOTE_NAMES[round(69 + 12 * math.log2(hz / 440)) % 12]
    shift = shift_for(hz, note)
    if abs(shift) * 100 < CLOSE_CENTS:
        return None, played, 0.0
    values, rate, channels, width, is_float = read(repo, path, "tune")
    centre = 128 if width == 1 else 0
    tuned = resample([v - centre for v in values], channels, 2 ** (shift / 12))
    if is_float:
        tuned = [v + centre for v in tuned]
    else:
        top = (1 << (8 * width - 1)) - 1
        low, high = (0, 255) if width == 1 else (-top - 1, top)
        tuned = [min(high, max(low, int(round(v + centre)))) for v in tuned]
    out = save(repo, path, f"in {note}", tuned, rate, channels, width, is_float, in_place=False)
    return out, played, round(shift, 2)
