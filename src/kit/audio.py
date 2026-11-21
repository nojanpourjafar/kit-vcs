"""Reading audio files: length, sample rate, a waveform outline, and tempo.

Standard library only, so it understands plain PCM WAV files and quietly gives
up on anything else (compressed formats, 32-bit float WAVs).
"""

from __future__ import annotations

import math
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


# ---- tempo ------------------------------------------------------------------

MIN_BPM, MAX_BPM = 60, 200
MAX_SECONDS = 30  # a loop's tempo is clear well within this; longer files are cut short


def tempo(file_path: Path, name: str) -> float | None:
    """Estimate a loop's tempo in BPM, or None when there is no clear beat.

    1. Split the sound into short slices (about 170 a second) and measure each
       one's energy.
    2. Mark where the energy jumps up: those are the hits (kicks, snares, hats).
    3. Score a beat length by how well the hits line up with themselves shifted
       by that length (autocorrelation), and a little by two and four beats
       (half a bar, a bar), because a real beat repeats there too.
    4. Loops are cut to whole bars, so the file's length allows only a few
       tempos (a 3.43 s loop is 70, 140 or 280 BPM). When the length fits,
       choose among those; otherwise try every tempo from MIN_BPM to MAX_BPM.

    One-shots and sounds without a steady pulse return None rather than a guess.
    """
    if not name.lower().endswith(".wav"):
        return None
    try:
        with wave.open(str(file_path), "rb") as wav:
            rate, width = wav.getframerate(), wav.getsampwidth()
            frames = min(wav.getnframes(), rate * MAX_SECONDS)
            seconds = wav.getnframes() / rate
            if seconds < 1 or width not in (1, 2, 3, 4):
                return None  # too short to hold a bar of beats
            hop = max(1, round(rate / 172))  # frames per slice
            energy = []
            while len(energy) * hop < frames and (chunk := wav.readframes(hop)):
                samples = _samples(chunk, width)
                energy.append(sum(x * x for x in samples) / max(1, len(samples)))
    except _WAVE_ERRORS:
        return None
    per_second = rate / hop
    if _hits(energy, per_second) < 4:
        return None  # a one-shot (an 808, a kick) has one hit, and one hit is not a beat
    onsets = _onsets(energy)
    if onsets is None:
        return None

    # Step 3. A loop's end joins its start, so the shift wraps around (i + lag) % n.
    n = len(onsets)
    cache: dict[int, float] = {}

    def lined_up(lag: int) -> float:
        if lag not in cache:
            cache[lag] = sum(onsets[i] * onsets[(i + lag) % n] for i in range(n)) / n
        return cache[lag]

    def score(bpm: float) -> float:
        lag = per_second * 60 / bpm
        total = 0.0
        for multiple, weight in ((1, 1.0), (2, 0.25), (4, 0.25)):
            exact = lag * multiple
            if exact >= n:
                break
            low = int(exact)  # in between two whole slices, blend the two
            total += weight * (lined_up(low) * (low + 1 - exact) + lined_up(low + 1) * (exact - low))
        # A preference for tempos near 125 settles a tempo against its double or half.
        return total * math.exp(-0.5 * (math.log2(bpm / 125) / 0.6) ** 2)

    # Step 4: the tempos a whole-bar loop of this length can have.
    from_length = [60 * beats / seconds for beats in (4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128)]
    candidates = [bpm for bpm in from_length if MIN_BPM <= bpm <= MAX_BPM]
    if not candidates:
        candidates = [MIN_BPM + step * 0.5 for step in range((MAX_BPM - MIN_BPM) * 2 + 1)]
    best = max(candidates, key=score)
    everywhere = max(score(MIN_BPM + step) for step in range(MAX_BPM - MIN_BPM + 1))
    if len(candidates) < 20 and score(best) < 0.6 * everywhere:
        # The length does not fit the beat (a trimmed or padded file): search every tempo.
        best = max((MIN_BPM + step * 0.5 for step in range((MAX_BPM - MIN_BPM) * 2 + 1)), key=score)
    if score(best) < 0.12 * lined_up(0):
        return None  # nothing repeats clearly: a one-shot, a pad, a texture
    return round(best, 1)


def _onsets(energy: list[float]) -> list[float] | None:
    """How sharply each slice gets louder than the one before (step 2), centred on zero."""
    loudest = max(energy, default=0)
    if loudest <= 0 or len(energy) < 8:
        return None
    # Ears hear ratios, so compare on a log scale. Only increases count as hits.
    level = [math.log(e / loudest + 1e-6) for e in energy]
    rise = [0.0] + [max(0.0, b - a) for a, b in zip(level, level[1:])]
    mean = sum(rise) / len(rise)
    centred = [r - mean for r in rise]
    return centred if any(centred) else None


def _hits(energy: list[float], per_second: float) -> int:
    """How many separate hits there are: places where the loudness at least doubles.

    The loudness is averaged over 50 ms first. A slice is shorter than one cycle
    of a bass note, so without this an 808's own wave makes the loudness wobble
    up and down, and the wobble would look like a rhythm. The moment before the
    file starts counts as silence, so a sound that starts on its first sample
    still has its hit counted.
    """
    width = max(1, round(0.05 * per_second))
    floor = max(energy, default=0) * 1e-4 or 1e-12
    smooth = [sum(energy[max(0, i - width + 1):i + 1]) / width for i in range(len(energy))]
    hits, rise, previous = 0, 0.0, math.log(floor)
    for value in smooth:
        level = math.log(value + floor)
        if level > previous:
            rise += level - previous     # still getting louder: part of the same hit
        else:
            hits += rise >= math.log(2)  # the rise has ended; count it if it doubled
            rise = 0.0
        previous = level
    return hits + (rise >= math.log(2))


def _samples(chunk: bytes, width: int) -> array:
    """The samples in a block, as numbers (all channels together)."""
    if width == 1:
        return array("b", bytes((b - 128) & 0xFF for b in chunk))
    if width == 2:
        samples = array("h", chunk[: len(chunk) // 2 * 2])
        if sys.byteorder == "big":
            samples.byteswap()
        return samples
    # 24- and 32-bit: keep the top two bytes of each sample, which is plenty for loudness.
    top = bytearray(len(chunk) // width * 2)
    top[0::2], top[1::2] = chunk[width - 2 :: width], chunk[width - 1 :: width]
    samples = array("h", bytes(top))
    if sys.byteorder == "big":
        samples.byteswap()
    return samples
