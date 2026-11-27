"""Reading audio files: length, sample rate, a waveform outline, and tempo.

Standard library only. It reads WAV, AIFF and FLAC files (see wavfile.py and
flac.py). For MP3 files it gives the length and format from their headers
(mp3.py); their waveforms are drawn by the page, which can decode them.
Anything else (AAC, Ogg) it quietly gives up on.
"""

from __future__ import annotations

import cmath
import math
import struct
import sys
from array import array
from pathlib import Path

from . import mp3
from .wavfile import WavError, can_read, open_wav

_WAVE_ERRORS = (WavError, EOFError, OSError, ZeroDivisionError, ValueError, struct.error, IndexError)


def describe(file_path: Path, name: str) -> str | None:
    """Describe a WAV or AIFF file, or return None if it is not one we can read.

    `name` is the file's real name. It is passed separately because stored
    objects are named by hash and have no ".wav" or ".aif" on the end.
    """
    if name.lower().endswith(".mp3"):
        return mp3.describe(file_path)
    if not can_read(name):
        return None
    try:
        with open_wav(file_path) as wav:
            rate, seconds, channels = wav.rate, wav.frames / wav.rate, wav.channels
    except _WAVE_ERRORS:
        return None
    layout = {1: "mono", 2: "stereo"}.get(channels, f"{channels} channels")
    return f"{seconds:.2f} s, {rate / 1000:g} kHz, {layout}"


def peaks(file_path: Path, name: str, buckets: int = 96) -> list[float] | None:
    """The loudest level in each of `buckets` equal slices, scaled to 0..1.

    This is the outline drawn as a waveform. Returns None for files we
    cannot read.
    """
    if not can_read(name):
        return None
    try:
        with open_wav(file_path) as wav:
            if wav.frames == 0:
                return None
            per_bucket = max(1, -(-wav.frames // buckets))  # round up, so every frame is covered
            levels = []
            while chunk := wav.readframes(per_bucket):
                levels.append(_loudest(chunk, wav.width, wav.is_float))
    except _WAVE_ERRORS:
        return None
    top = max(levels, default=0.0)
    if top <= 0:
        return [0.0] * len(levels)
    return [level / top for level in levels]


def _loudest(chunk: bytes, width: int, is_float: bool = False) -> float:
    """Peak level of a block of samples, as a fraction of full scale."""
    if is_float:
        samples = _floats(chunk, width)
        return min(1.0, max(max(samples), -min(samples))) if samples else 0.0
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
    if not can_read(name):
        return None
    try:
        with open_wav(file_path) as wav:
            rate = wav.rate
            frames = min(wav.frames, rate * MAX_SECONDS)
            seconds = wav.frames / rate
            if seconds < 1:
                return None  # too short to hold a bar of beats
            hop = max(1, round(rate / 172))  # frames per slice
            energy = []
            while len(energy) * hop < frames and (chunk := wav.readframes(hop)):
                samples = _floats(chunk, wav.width) if wav.is_float else _samples(chunk, wav.width)
                energy.append(sum(x * x for x in samples) / max(1, len(samples)))
    except _WAVE_ERRORS:
        return None
    return _tempo_from_energy(energy, rate / hop, seconds)


def tempo_of(samples, rate: int, seconds: float) -> float | None:
    """`tempo` for sound that is already decoded: mono samples from -1 to 1.

    Used for formats Python does not decode (MP3, AAC), which the page decodes
    and sends. `seconds` is the length of the whole sound, which may be longer
    than the samples sent (only the first MAX_SECONDS are needed).
    """
    if seconds < 1 or rate <= 0:
        return None
    hop = max(1, round(rate / 172))
    end = min(len(samples), rate * MAX_SECONDS)
    energy = [sum(x * x for x in samples[i:i + hop]) / hop for i in range(0, end - hop + 1, hop)]
    return _tempo_from_energy(energy, rate / hop, seconds)


def _tempo_from_energy(energy: list[float], per_second: float, seconds: float) -> float | None:
    """Steps 2 to 4 of `tempo`, from the energy of each slice."""
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


def _floats(chunk: bytes, width: int) -> array:
    """Floating-point samples (full scale is -1.0 to 1.0)."""
    samples = array("f" if width == 4 else "d", chunk[: len(chunk) // width * width])
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


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


# ---- the note of a one-shot -------------------------------------------------

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
LOWEST_HZ, HIGHEST_HZ = 27.5, 1000.0  # A0, the bottom of an 808, up to the top of most melodic one-shots


def note(file_path: Path, name: str) -> str | None:
    """The note a one-shot plays ("F#"), or None when it has no clear pitch.

    Uses YIN (de Cheveigné and Kawahara, 2002): compare the sound with itself
    shifted by each possible period, and take the shortest shift at which it
    lines up almost perfectly. That shift is one cycle of the note.

    The attack (the click of a kick, the pluck of a bass) has no steady pitch,
    so the measurement starts a little after the loudest point. Drums with no
    pitch (snares, hats) never line up well enough, and get None.
    """
    if not can_read(name):
        return None
    try:
        with open_wav(file_path) as wav:
            rate, channels = wav.rate, wav.channels
            raw = wav.readframes(min(wav.frames, int(rate * 1.5)))  # the first 1.5 s is plenty
            samples = _floats(raw, wav.width) if wav.is_float else _samples(raw, wav.width)
    except _WAVE_ERRORS:
        return None
    if channels > 1:  # mix to mono
        samples = [sum(samples[i:i + channels]) / channels for i in range(0, len(samples) - channels + 1, channels)]
    return note_of(list(samples), rate)


def note_of(samples: list[float], rate: int) -> str | None:
    """`note` for sound that is already decoded: mono samples, any scale."""
    samples = samples[:int(rate * 1.5)]
    # Work at about 11 kHz: plenty for notes up to 1 kHz, and four times less work.
    step = max(1, rate // 11025)
    if step > 1:
        samples = [sum(samples[i:i + step]) / step for i in range(0, len(samples) - step + 1, step)]
        rate //= step
    if not samples:
        return None
    loudest = max(range(len(samples)), key=lambda i: abs(samples[i]))
    start = loudest + int(0.06 * rate)  # skip the attack, and an 808's pitch slide into the note
    window = int(0.12 * rate)  # long enough for several cycles of a low 808
    longest = int(rate / LOWEST_HZ)
    if start + window + longest > len(samples):
        start = max(0, len(samples) - window - longest)
        if start + window + longest > len(samples):
            return None  # too short to measure

    frame = samples[start:start + window + longest]
    level = max(abs(x) for x in frame)
    if level < 0.02 * abs(samples[loudest]) or level == 0:
        return None  # it has died away by then: a click, not a note

    # The YIN difference for each shift, normalised so that 0 means "lines up perfectly".
    shortest = max(2, int(rate / HIGHEST_HZ))
    running, normalised = 0.0, {}
    head = frame[:window]
    for shift in range(1, longest + 1):
        moved = frame[shift:shift + window]
        difference = sum((a - b) ** 2 for a, b in zip(head, moved))
        running += difference
        normalised[shift] = difference * shift / running if running else 1.0

    # The first dip below the threshold, followed down to its bottom.
    best = None
    for shift in range(shortest, longest):
        if normalised[shift] < 0.15:
            while shift + 1 < longest and normalised[shift + 1] < normalised[shift]:
                shift += 1
            best = shift
            break
    if best is None:
        return None  # nothing repeats cleanly: no clear pitch

    # Between whole samples, with a parabola through the dip and its neighbours.
    a, b, c = normalised[best - 1], normalised[best], normalised.get(best + 1, normalised[best])
    period = best + (0.5 * (a - c) / (a - 2 * b + c) if a - 2 * b + c else 0)
    hz = rate / period

    # A chord repeats too, but only at a period far below its notes (C, E and G
    # line up every cycle of a C two octaves down), and nothing sounds at that
    # frequency. A real note carries much of its energy there. So measure the
    # share of energy at exactly `hz` (the Goertzel algorithm: one bin of a
    # Fourier transform) and give no note when it is small.
    if _share_at(head, hz / rate) < 0.1:
        return None
    midi = round(69 + 12 * math.log2(hz / 440))
    return NOTE_NAMES[midi % 12]


def _share_at(samples: list[float], cycles_per_sample: float) -> float:
    """The share (0 to 1) of the samples' energy at one frequency."""
    omega = 2 * math.pi * cycles_per_sample
    real = sum(x * math.cos(omega * i) for i, x in enumerate(samples))
    imaginary = sum(x * math.sin(omega * i) for i, x in enumerate(samples))
    total = sum(x * x for x in samples)
    return 2 * (real * real + imaginary * imaginary) / (len(samples) * total) if total else 0.0


# ---- the key of a melodic loop ---------------------------------------------------

# How strongly each of the 12 notes belongs to a key, from listening tests
# (Krumhansl and Kessler, 1982). Index 0 is the key's own note.
MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
KEY_RATE = 5512          # Hz: plenty for notes up to about 2 kHz
KEY_WINDOW = 4096        # samples per slice: about 0.74 s, fine enough to tell neighbouring low notes apart
KEY_SLICES = 16          # slices measured, spread over the loop
KEY_LOWEST, KEY_HIGHEST = 55.0, 2000.0


def key(file_path: Path, name: str) -> str | None:
    """`key_of` for a sound file Kit can read: its first MAX_SECONDS, mixed to one channel."""
    if not can_read(name):
        return None
    try:
        with open_wav(file_path) as wav:
            rate, channels = wav.rate, wav.channels
            raw = wav.readframes(min(wav.frames, rate * MAX_SECONDS))
            samples = _floats(raw, wav.width) if wav.is_float else _samples(raw, wav.width)
    except _WAVE_ERRORS:
        return None
    if channels > 1:
        samples = [sum(samples[i:i + channels]) / channels for i in range(0, len(samples) - channels + 1, channels)]
    return key_of(samples, rate)


def key_of(samples, rate: int) -> str | None:
    """The key of a melodic loop, like "F# min", or None when there is no clear key.

    1. Take slices of the loop and find the strength of every frequency in each
       one (a Fourier transform).
    2. Add up how much of each of the 12 notes is sounding, whatever the octave
       (a "chroma" vector): every peak in the spectrum counts for the note
       nearest its frequency.
    3. Compare those 12 amounts with the profile of each of the 24 major and
       minor keys (correlation), and take the key that fits best.

    Drum loops spread their energy over every note, so no key fits well, and
    they get None. A key and its relative (C major and A minor share their
    notes) can be confused; the profiles favour the right one when the music
    keeps returning to its home note.
    """
    if rate <= 0 or len(samples) < rate:
        return None
    step = max(1, rate // KEY_RATE)
    low = [sum(samples[i:i + step]) / step for i in range(0, len(samples) - step + 1, step)]
    rate //= step
    if len(low) < KEY_WINDOW:
        return None
    starts = range(0, len(low) - KEY_WINDOW + 1, max(1, (len(low) - KEY_WINDOW) // (KEY_SLICES - 1) or 1))
    window = [0.5 - 0.5 * math.cos(2 * math.pi * i / (KEY_WINDOW - 1)) for i in range(KEY_WINDOW)]
    chroma = [0.0] * 12
    bass = [0.0] * 12  # the same, for the low notes only: the bass usually sits on the home note
    for number, start in enumerate(list(starts)[:KEY_SLICES]):
        chunk = [x * w for x, w in zip(low[start:start + KEY_WINDOW], window)]
        if max(map(abs, chunk)) < 1e-6:
            continue  # silence
        spectrum = [abs(c) for c in _fft(chunk)[:KEY_WINDOW // 2]]
        first = int(KEY_LOWEST * KEY_WINDOW / rate)
        last = min(len(spectrum) - 2, int(KEY_HIGHEST * KEY_WINDOW / rate))
        top = max(spectrum[first:last + 1], default=0)
        # Only peaks, and only those standing out: the notes being played, not the noise between them.
        peaks = {k: spectrum[k] for k in range(max(1, first), last + 1)
                 if spectrum[k] > 0.05 * top and spectrum[k] >= spectrum[k - 1] and spectrum[k] > spectrum[k + 1]}
        for k, level in peaks.items():
            hz = k * rate / KEY_WINDOW
            note = round(69 + 12 * math.log2(hz / 440)) % 12
            chroma[note] += level
            if hz < KEY_BASS:
                # Loops usually start at home, so the bass at the start counts more.
                bass[note] += level * (3 if number < KEY_SLICES // 4 else 1)
    if sum(chroma) == 0:
        return None
    # A key needs chords or a melody: at least three notes clearly sounding. A drum
    # loop's tuned kick gives one or two strong notes and nothing else.
    if sum(level >= 0.25 * max(chroma) for level in chroma) < 3:
        return None
    fits = []
    for tonic in range(12):
        for mode, profile in (("maj", MAJOR_PROFILE), ("min", MINOR_PROFILE)):
            rotated = profile[-tonic:] + profile[:-tonic] if tonic else profile
            fits.append((_correlation(chroma, rotated), f"{NOTE_NAMES[tonic]} {mode}"))
    fits.sort(reverse=True)
    best, key = fits[0]
    if best < KEY_FIT:
        return None  # no key fits well: drums, noise, or a sound that wanders between keys
    # A key and its relative (C major, A minor) use the same seven notes, so they
    # fit almost equally well; Am-F-C-G is also vi-IV-I-V in C. What tells them
    # apart is the home note, and the bass plays it most. So between the two,
    # take the one whose home note the bass plays more.
    tonic, mode = NOTE_NAMES.index(key.split()[0]), key.split()[1]
    other = (tonic + 9) % 12 if mode == "maj" else (tonic + 3) % 12
    if bass[other] > 1.2 * bass[tonic]:
        return f"{NOTE_NAMES[other]} {'min' if mode == 'maj' else 'maj'}"
    return key


KEY_FIT = 0.6   # the correlation a key must reach to be named
KEY_BASS = 160  # Hz: below this counts as bass


def _correlation(a: list[float], b: list[float]) -> float:
    mean_a, mean_b = sum(a) / len(a), sum(b) / len(b)
    da, db = [x - mean_a for x in a], [y - mean_b for y in b]
    spread = math.sqrt(sum(x * x for x in da) * sum(y * y for y in db))
    return sum(x * y for x, y in zip(da, db)) / spread if spread else 0.0


def _fft(values: list[float]) -> list[complex]:
    """A Fourier transform (iterative radix-2 Cooley-Tukey). len(values) must be a power of two."""
    n = len(values)
    out = [complex(v) for v in values]
    j = 0
    for i in range(1, n):  # put the values in bit-reversed order
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            out[i], out[j] = out[j], out[i]
    size = 2
    while size <= n:
        turn = cmath.exp(-2j * math.pi / size)
        half = size // 2
        twiddles = [turn ** k for k in range(half)]
        for start in range(0, n, size):
            for k in range(half):
                a, b = out[start + k], out[start + k + half] * twiddles[k]
                out[start + k], out[start + k + half] = a + b, a - b
        size *= 2
    return out


# ---- what kind of sound it is -----------------------------------------------------

KINDS = ("Loop", "Kick", "808", "Snare", "Clap", "Hat", "Perc", "Melodic")


def kind(file_path: Path, name: str) -> str | None:
    """`kind_of` for a sound file Kit can read: its first ten seconds, mixed to one channel."""
    if not can_read(name):
        return None
    try:
        with open_wav(file_path) as wav:
            rate, channels = wav.rate, wav.channels
            raw = wav.readframes(min(wav.frames, rate * 10))
            samples = _floats(raw, wav.width) if wav.is_float else _samples(raw, wav.width)
    except _WAVE_ERRORS:
        return None
    if channels > 1:
        samples = [sum(samples[i:i + channels]) / channels for i in range(0, len(samples) - channels + 1, channels)]
    else:
        samples = list(samples)
    return kind_of(samples, rate)  # any scale: only shares of energy and timings are compared


def kind_of(samples, rate: int) -> str | None:
    """Kick, 808, Snare, Clap, Hat, Perc, Melodic or Loop, from mono samples; None for silence.

    It measures a few things a producer hears at once, and decides by rules:

      * how long the sound is, and whether it has a beat (a loop);
      * how its energy splits across low (under 150 Hz), body (150-600 Hz)
        and high (over 5 kHz) frequencies, and how noisy it is (spectral
        flatness: noise is flat, a note is a few sharp peaks);
      * how fast it dies away, and whether it has a clear pitch.

    The rules, in order: a beat makes a loop; mostly low makes a kick, or an
    808 if it rings on; noisy and mostly high is a hat, noisy with a body is a
    snare, and noisy without one is a clap; a clear pitch that rings is
    melodic; anything else (a short knock or click) is perc.
    """
    if rate <= 0 or not samples:
        return None
    peak = max(range(len(samples)), key=lambda i: abs(samples[i]))
    if abs(samples[peak]) < 1e-4 * (32768 if abs(samples[peak]) > 2 else 1):
        return None  # silence, whether the samples run -1..1 or are 16-bit numbers
    seconds = len(samples) / rate

    # Loudness every 5 ms, and how long the sound takes to fall 20 dB below its peak.
    hop = max(1, rate // 200)
    rms = [math.sqrt(sum(x * x for x in samples[i:i + hop]) / hop) for i in range(0, len(samples) - hop + 1, hop)]
    if not rms:
        return None
    top = max(rms)
    at = rms.index(top)
    decay = next((i - at for i in range(at, len(rms)) if rms[i] < 0.1 * top), len(rms) - at) * hop / rate

    if seconds >= 1.5 and _hits([e * e for e in rms], 200) >= 4:
        return "Loop"  # a beat
    if seconds >= 2 and key_of(samples, rate) is not None:
        return "Loop"  # chords or a melody: a melodic loop

    # The spectrum around the hit: three windows from just before the peak.
    size = 2048
    start = max(0, peak - size // 4)
    power = [0.0] * (size // 2)
    for w in range(3):
        chunk = samples[start + w * size // 2:start + w * size // 2 + size]
        if len(chunk) < size:
            chunk = list(chunk) + [0.0] * (size - len(chunk))
        shape = [0.5 - 0.5 * math.cos(2 * math.pi * i / (size - 1)) for i in range(size)]
        for k, c in enumerate(_fft([x * s for x, s in zip(chunk, shape)])[:size // 2]):
            power[k] += abs(c) ** 2
    hz = [k * rate / size for k in range(size // 2)]
    total = sum(power[1:]) or 1.0
    low = sum(p for f, p in zip(hz, power) if 20 <= f < 150) / total
    body = sum(p for f, p in zip(hz, power) if 150 <= f < 600) / total
    high = sum(p for f, p in zip(hz, power) if f >= 5000) / total
    band = [p for f, p in zip(hz, power) if 200 <= f < min(12000, rate / 2)]
    flatness = (math.exp(sum(math.log(p + 1e-20) for p in band) / len(band)) / (sum(band) / len(band) + 1e-20)) if band else 0.0

    if low > 0.5:
        return "808" if decay > 0.75 else "Kick"  # an 808 rings for a second or more; a kick is gone in half that
    if flatness > 0.15:  # noise: hats, snares, claps
        if high > 0.8:
            return "Hat"
        return "Snare" if body > 0.15 else "Clap"
    if note_of(list(samples), rate) is not None and decay > 0.3:
        return "Melodic"
    return "Perc"
