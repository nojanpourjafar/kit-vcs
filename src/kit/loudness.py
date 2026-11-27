"""How loud each sound is, the way streaming services and mastering engineers measure it.

Loudness is in LUFS (ITU-R BS.1770-4, the standard behind Spotify's -14 and
broadcast's -23): the sound goes through a "K" filter that hears roughly the way
ears do (a gentle lift above 1.5 kHz, a cut below 40 Hz), its power is
averaged over 400 ms blocks that overlap by three quarters, and blocks quieter
than -70 LUFS, or 10 LU below the average, are left out so silence does not
pull the number down. A one-shot shorter than one block is measured as a
single block.

It also gives the sample peak in dBFS. Each sound is measured once: answers are
kept in .kit/loudness.json by hash. Sounds Kit cannot decode (MP3) are not measured.
"""

from __future__ import annotations

import json
import math

from .problems import channels_of
from .wavfile import can_read

BLOCK = 0.4            # seconds
STEP = 0.1             # blocks start every 100 ms (75% overlap)
ABSOLUTE_GATE = -70.0  # LUFS
RELATIVE_GATE = -10.0  # LU below the first average
APART = 6.0            # a sound this many LU from the kit's middle is "much louder" or "much quieter"


def _biquads(rate: int) -> list[tuple[float, float, float, float, float]]:
    """The two K-weighting filters for `rate`, as (b0, b1, b2, a1, a2) with a0 = 1.

    The standard lists coefficients for 48 kHz only. These are the same two
    filters worked out for any rate (the derivation by Brecht De Man, also
    used by the pyloudnorm library), which give the standard's numbers at 48 kHz.
    """
    # Stage 1: the head's effect, a +4 dB shelf above about 1.7 kHz.
    gain, f0, q = 3.999843853973347, 1681.974450955533, 0.7071752369554196
    k = math.tan(math.pi * f0 / rate)
    vh = 10 ** (gain / 20)
    vb = vh ** 0.4996667741545416
    a0 = 1 + k / q + k * k
    shelf = ((vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0,
             2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0)
    # Stage 2: the "RLB" high pass at about 38 Hz.
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / rate)
    a0 = 1 + k / q + k * k
    high_pass = (1.0, -2.0, 1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0)
    return [shelf, high_pass]


def _filter(samples, coefficients) -> list[float]:
    b0, b1, b2, a1, a2 = coefficients
    x1 = x2 = y1 = y2 = 0.0
    out = []
    append = out.append
    for x in samples:
        y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, x, y1, y
        append(y)
    return out


def _lufs(power: float) -> float:
    return -0.691 + 10 * math.log10(power) if power > 0 else -math.inf


def measure(channels: list, rate: int) -> dict | None:
    """{"lufs": integrated loudness, "peak": sample peak in dBFS} for channels at full scale 1.0.

    None for silence. The channels count equally, as left and right do in the
    standard (surround weights do not apply to sample packs).
    """
    peak = max((max(map(abs, channel), default=0.0) for channel in channels), default=0.0)
    if peak == 0:
        return None
    squared = []
    for channel in channels:
        filtered = channel
        for coefficients in _biquads(rate):
            filtered = _filter(filtered, coefficients)
        # Running sums make every block's mean a subtraction.
        sums = [0.0]
        total = 0.0
        for y in filtered:
            total += y * y
            sums.append(total)
        squared.append(sums)
    length = min(len(channel) for channel in channels)
    size = int(BLOCK * rate)
    if length <= size:
        starts, size = [0], length  # shorter than one block: the whole sound is the block
    else:
        starts = range(0, length - size + 1, int(STEP * rate))
    blocks = [sum((sums[start + size] - sums[start]) / size for sums in squared) for start in starts]
    loud = [power for power in blocks if _lufs(power) > ABSOLUTE_GATE]
    if not loud:
        return None
    gate = _lufs(sum(loud) / len(loud)) + RELATIVE_GATE
    kept = [power for power in loud if _lufs(power) > gate]
    return {"lufs": round(_lufs(sum(kept) / len(kept)), 1), "peak": round(20 * math.log10(peak), 1)}


def measure_file(file_path, name: str) -> dict | None:
    read = channels_of(file_path, name)
    if read is None or not read[0] or not len(read[0][0]):
        return None
    return measure(*read)


def _cache(repo) -> dict:
    try:
        cache = json.loads((repo.dir / "loudness.json").read_text())
    except (OSError, ValueError):
        return {}
    return cache if isinstance(cache, dict) else {}


def known(repo, manifest: dict) -> dict[str, dict]:
    """{path: {"lufs", "peak"}} for the sounds in `manifest` measured before. Quick: nothing is read."""
    cache = _cache(repo)
    return {path: cache[entry["hash"]] for path, entry in manifest.items() if cache.get(entry["hash"])}


def unmeasured(repo, manifest: dict) -> int:
    cache = _cache(repo)
    return sum(1 for path, entry in manifest.items() if can_read(path) and entry["hash"] not in cache)


def measure_kit(repo, progress=None, working: dict | None = None) -> dict[str, dict]:
    """{path: {"lufs", "peak"}} for every sound in the kit's folder, measuring those not measured before."""
    cache = _cache(repo)
    working = working if working is not None else repo.working()
    todo = [path for path in working if can_read(path) and working[path]["hash"] not in cache]
    for done, path in enumerate(todo, 1):
        cache[working[path]["hash"]] = measure_file(repo.folder / path, path)
        if progress:
            progress(done, len(todo))
    if todo:
        try:
            tmp = repo.dir / "loudness.json.tmp"
            tmp.write_text(json.dumps(cache))
            tmp.replace(repo.dir / "loudness.json")
        except OSError:
            pass  # only a cache
    return known(repo, working)


def middle(levels: list[float]) -> float | None:
    """The median: the level half the sounds are louder than. One very loud sound does not move it."""
    if not levels:
        return None
    ordered = sorted(levels)
    half = len(ordered) // 2
    return ordered[half] if len(ordered) % 2 else (ordered[half - 1] + ordered[half]) / 2


def middles(measured: dict[str, dict], kind_of: dict[str, str]) -> dict[str, float]:
    """{kind: middle loudness} for each kind with at least 3 measured sounds."""
    by_kind: dict[str, list[float]] = {}
    for path, level in measured.items():
        if kind_of.get(path):
            by_kind.setdefault(kind_of[path], []).append(level["lufs"])
    return {kind: middle(levels) for kind, levels in by_kind.items() if len(levels) >= 3}


def apart(lufs: float, centre: float | None) -> str | None:
    """"much louder" or "much quieter" than the kit's middle, or None."""
    if centre is None:
        return None
    if lufs - centre >= APART:
        return "much louder"
    if centre - lufs >= APART:
        return "much quieter"
    return None
