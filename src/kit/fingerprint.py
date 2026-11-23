"""Finding the same sound in several places, even when the files differ.

Packs share sounds. The same hat turns up in three kits, renamed, or bounced
again at another bit depth or sample rate, or turned up a few dB. Identical
files are easy: they have the same hash. For the rest, each sound gets a
small fingerprint:

  * its length;
  * its loudness over time: 64 points across the first ten seconds;
  * its tone: how strong it is in 40 frequency bands, from 40 Hz to 4 kHz,
    in decibels below its loudest band, so a sound turned up or down looks
    the same.

Two sounds are the same when their lengths are within 3%, their loudness
curves differ by under 0.023 on average (on a scale of 0 to 1), and no band
that is clearly sounding differs by more than 10 dB. Those limits come from
measuring: copies resampled from 44.1 to 22, 48 and 96 kHz, turned down,
made 24-bit, stereo or FLAC all stayed inside them, and different kicks,
snares and hats (kicks a few hertz apart included) all fell outside. Different
kicks fade fast and sit low just the same, so the limits are tight: a near
miss is reported as nothing rather than as a false match.

Only formats Kit decodes itself (WAV, AIFF, FLAC) get fingerprints; others,
like MP3, are matched only when the files are identical. Standard library only.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from .audio import _WAVE_ERRORS, _fft, _floats, _samples
from .wavfile import can_read, open_wav

SECONDS = 10           # only the start is compared; a longer sound's start is enough to tell
POINTS = 64            # loudness points
BANDS = 40             # frequency bands
LOW_HZ, HIGH_HZ = 40.0, 4000.0  # above this, resampling at different rates disagrees too much
WINDOW = 2048
LOUDNESS_LIMIT = 0.023  # average difference of the loudness curves
TONE_LIMIT = 10.0       # dB, the most any clearly sounding band may differ
LENGTH_TOLERANCE = 0.03


@dataclass
class Fingerprint:
    seconds: float
    loudness: list[float]
    tone: list[float]

    def to_json(self) -> dict:
        return {"seconds": round(self.seconds, 4), "loudness": [round(x, 4) for x in self.loudness],
                "tone": [round(x, 3) for x in self.tone]}

    @classmethod
    def from_json(cls, data: dict) -> Fingerprint:
        return cls(float(data["seconds"]), [float(x) for x in data["loudness"]], [float(x) for x in data["tone"]])


def fingerprint(path: Path, name: str) -> Fingerprint | None:
    """The fingerprint of a sound file, or None for silence, very short sounds and formats Kit cannot read."""
    if not can_read(name):
        return None
    try:
        with open_wav(path) as wav:
            rate, channels, frames = wav.rate, wav.channels, wav.frames
            raw = wav.readframes(min(frames, rate * SECONDS))
            samples = _floats(raw, wav.width) if wav.is_float else _samples(raw, wav.width)
    except _WAVE_ERRORS:
        return None
    if channels > 1:
        samples = [sum(samples[i:i + channels]) / channels for i in range(0, len(samples) - channels + 1, channels)]
    seconds = frames / rate if rate else 0
    # Down to about 11 kHz: what the bands need, and four times less work.
    step = max(1, rate // 11025)
    if step > 1:
        samples = [sum(samples[i:i + step]) / step for i in range(0, len(samples) - step + 1, step)]
        rate //= step
    if len(samples) < POINTS * 4 or max(map(abs, samples), default=0) < 1e-4:
        return None

    size = len(samples) / POINTS
    loudness = []
    for p in range(POINTS):
        part = samples[int(p * size):int((p + 1) * size)] or [0.0]
        loudness.append(math.sqrt(sum(x * x for x in part) / len(part)))
    top = max(loudness)
    loudness = [x / top for x in loudness]

    # Tone: band energies summed over the whole of the (first ten seconds of the) sound.
    edges = [LOW_HZ * (HIGH_HZ / LOW_HZ) ** (b / BANDS) for b in range(BANDS + 1)]
    energy = [0.0] * BANDS
    if len(samples) < WINDOW:
        samples = samples + [0.0] * (WINDOW - len(samples))
    starts = range(0, len(samples) - WINDOW + 1, WINDOW // 2)  # every window, half overlapping: steadier for noisy sounds
    shape = [0.5 - 0.5 * math.cos(2 * math.pi * i / (WINDOW - 1)) for i in range(WINDOW)]
    for start in starts:
        spectrum = _fft([x * w for x, w in zip(samples[start:start + WINDOW], shape)])
        for k in range(1, WINDOW // 2):
            hz = k * rate / WINDOW
            if LOW_HZ <= hz < HIGH_HZ:
                band = min(BANDS - 1, int(BANDS * math.log(hz / LOW_HZ) / math.log(HIGH_HZ / LOW_HZ)))
                energy[band] += abs(spectrum[k]) ** 2
    loudest = max(energy) or 1.0
    tone = [10 * math.log10(max(e / loudest, 1e-9)) for e in energy]  # dB below the loudest band
    tone = [max(t, -80.0) for t in tone]  # below -80 dB is silence, whatever the file's noise floor
    return Fingerprint(seconds, loudness, tone)


def difference(a: Fingerprint, b: Fingerprint) -> tuple[float, float]:
    """(average loudness difference, largest difference in dB of any band clearly sounding in either)."""
    # Resampling smears the very start of a fast hit (a hat) by a millisecond or two,
    # so the curves are also compared shifted by up to two points, and the best match counts.
    loudness = min(sum(abs(a.loudness[i] - b.loudness[i + shift]) for i in range(max(0, -shift), min(POINTS, POINTS - shift)))
                   / (POINTS - abs(shift)) for shift in range(-2, 3))
    sounding = [i for i in range(BANDS) if max(a.tone[i], b.tone[i]) > -40]
    tone = max((abs(a.tone[i] - b.tone[i]) for i in sounding), default=0.0)
    return loudness, tone


def same_sound(a: Fingerprint, b: Fingerprint) -> bool:
    """Whether two fingerprints are the same sound."""
    longer = max(a.seconds, b.seconds)
    if longer and abs(a.seconds - b.seconds) / longer > LENGTH_TOLERANCE:
        return False
    if len(a.loudness) != POINTS or len(a.tone) != BANDS or len(b.loudness) != POINTS or len(b.tone) != BANDS:
        return False  # made by another version of Kit with other settings
    loudness, tone = difference(a, b)
    return loudness <= LOUDNESS_LIMIT and tone <= TONE_LIMIT


# ---- finding duplicates ----------------------------------------------------------

@dataclass
class Place:
    kit: str     # the kit folder's name
    folder: str  # the kit folder's full path
    path: str    # the sound's path inside the kit
    hash: str
    size: int


@dataclass
class Group:
    places: list[Place]
    exact: bool  # True: the very same file everywhere; False: the same sound in different files

    @property
    def extra_bytes(self) -> int:
        """The space the copies take beyond the first."""
        return sum(p.size for p in self.places) - max(p.size for p in self.places)


def find_duplicates(folders: list[Path], progress=None) -> list[Group]:
    """Sounds that are in more than one place across `folders` (kits, or plain folders of sounds).

    Identical files are found by their hash. Different files are compared by
    fingerprint, which is kept in each kit's .kit/fingerprints.json by hash, so
    asking again is quick. Groups come back largest first.
    `progress(done, total)` is called while fingerprints are made.
    """
    from .manifest import scan
    from .repo import DIR, Repo

    places: list[Place] = []
    cache_of: dict[str, dict] = {}            # hash -> the fingerprint cache of the kit it was found in
    cache_files: dict[Path, dict] = {}        # each kit's cache file -> its contents
    for folder in folders:
        folder = Path(folder).expanduser().resolve()
        if (folder / DIR).is_dir():
            repo = Repo(folder)
            manifest = repo.working()
            cache = cache_files.setdefault(repo.dir / "fingerprints.json", _load(repo.dir / "fingerprints.json"))
        else:
            manifest = scan(folder)  # a plain folder: read, never written to
            cache = {}
        for path, entry in manifest.items():
            places.append(Place(folder.name, str(folder), path, entry["hash"], entry["size"]))
            cache_of.setdefault(entry["hash"], cache)

    # One fingerprint per distinct file.
    first_place = {}
    for place in places:
        first_place.setdefault(place.hash, place)
    prints: dict[str, Fingerprint] = {}
    for done, (digest, place) in enumerate(first_place.items(), 1):
        cache = cache_of[digest]
        try:
            saved = cache[digest]  # None means: tried, and it has no fingerprint (an MP3, silence)
            if saved is not None:
                prints[digest] = Fingerprint.from_json(saved)
        except (KeyError, TypeError, ValueError):
            made = fingerprint(Path(place.folder) / place.path, place.path)
            cache[digest] = made.to_json() if made else None
            if made:
                prints[digest] = made
        if progress:
            progress(done, len(first_place))
    for cache_file, cache in cache_files.items():
        _save(cache_file, cache)

    # Join hashes whose sounds are the same (union-find). Comparing every pair
    # would be slow for thousands of sounds, so only sounds of nearly the same
    # length are compared: sorted by length, each looks ahead until too long.
    parent = {digest: digest for digest in first_place}

    def root(digest: str) -> str:
        while parent[digest] != digest:
            parent[digest] = parent[parent[digest]]
            digest = parent[digest]
        return digest

    by_length = sorted(prints.items(), key=lambda item: item[1].seconds)
    for i, (digest, fp) in enumerate(by_length):
        for other, other_fp in by_length[i + 1:]:
            if other_fp.seconds > fp.seconds * (1 + LENGTH_TOLERANCE) + 1e-9:
                break
            if root(digest) != root(other) and same_sound(fp, other_fp):
                parent[root(other)] = root(digest)

    groups: dict[str, list[Place]] = {}
    for place in places:
        groups.setdefault(root(place.hash), []).append(place)
    found = [Group(sorted(members, key=lambda p: (p.kit.lower(), p.path.lower())), len({p.hash for p in members}) == 1)
             for members in groups.values() if len(members) > 1]
    return sorted(found, key=lambda g: (-len(g.places), g.places[0].kit.lower(), g.places[0].path.lower()))


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(path: Path, cache: dict) -> None:
    try:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(cache))
        tmp.replace(path)
    except OSError:
        pass  # only a cache: the next run makes the fingerprints again
