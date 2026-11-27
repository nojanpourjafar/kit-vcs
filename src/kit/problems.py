"""Problems a producer would want to know about before a sound goes in a pack.

  * clipping: the waveform is flattened against full scale, which sounds harsh;
  * a late start: silence before a one-shot, so it plays late when triggered;
  * a click at the end: a one-shot cut off instead of faded out, or a loop whose
    end does not meet its start, so it clicks every time it repeats;
  * silence: nothing in the file at all.

Each sound is checked once: answers are kept in .kit/problems.json by hash.
Sounds Kit cannot decode (MP3) are not checked.
"""

from __future__ import annotations

import json
from array import array

from .audio import _WAVE_ERRORS, _floats, _samples
from .wavfile import can_read, open_wav

QUIET = 10 ** (-60 / 20)      # -60 dB: below this, a sample counts as silence
FULL = 0.999                  # a sample this close to full scale is at the ceiling
CLIP_RUN = 3                  # this many in a row at the ceiling is a clip, not a peak
LATE_MS = 10                  # silence before a one-shot longer than this is a late start
CLICK = 10 ** (-40 / 20)      # -40 dB: a one-shot whose last sample is louder than this stops with a click
SEAM = 10 ** (-30 / 20)       # -30 dB: a loop whose end and start differ by more than this, and by
SEAM_STEPS = 4                # 4 times the usual step between samples there, clicks when it repeats
LIMIT_SECONDS = 120           # only the first two minutes are read, so huge files stay quick


def channels_of(file_path, name: str) -> tuple[list[array], int] | None:
    """Each channel's samples, scaled so full scale is 1.0, and the rate; None if Kit cannot read it."""
    if not can_read(name):
        return None
    try:
        with open_wav(file_path) as wav:
            rate, count, width, is_float = wav.rate, wav.channels, wav.width, wav.is_float
            raw = wav.readframes(min(wav.frames, rate * LIMIT_SECONDS))
    except _WAVE_ERRORS:
        return None
    if is_float:
        samples, scale = _floats(raw, width), 1.0
    else:
        samples = _samples(raw, width)
        scale = 1 / 128 if width == 1 else 1 / 32768  # _samples keeps the top 16 bits of wider samples
    return [array("d", (x * scale for x in samples[c::count])) for c in range(count)], rate


def find(channels: list, rate: int, loop: bool) -> list[dict]:
    """The problems in one sound, from its channels (full scale = 1.0). `loop` is True for loops."""
    peak = max((max(map(abs, channel), default=0.0) for channel in channels), default=0.0)
    if peak < QUIET:
        return [{"kind": "silent", "text": "Silent: nothing above -60 dB"}]
    found = []

    # Clipping: runs of samples at the ceiling, in any channel. A float file can
    # also go past it; those samples will clip as soon as it is turned into 16-bit.
    places = over = 0
    for channel in channels:
        run = 0
        for x in channel:
            if abs(x) > 1.0:
                over += 1
            if abs(x) >= FULL:
                run += 1
                if run == CLIP_RUN:
                    places += 1
            else:
                run = 0
    if places or over:
        where = f"{places} place{'s' if places != 1 else ''}" if places else f"{over} samples over full scale"
        found.append({"kind": "clipping", "text": f"Clips: flattened at full scale ({where})"})

    length = min(len(channel) for channel in channels)
    if not loop:
        # A late start: silence before the first sample above -60 dB.
        first = next((i for i in range(length) if any(abs(channel[i]) >= QUIET for channel in channels)), 0)
        if first * 1000 / rate > LATE_MS:
            found.append({"kind": "late", "text": f"Starts late: {first * 1000 / rate:.0f} ms of silence before the sound"})
        # Cut off: the last sample is still loud, so playback stops with a jump.
        if max(abs(channel[length - 1]) for channel in channels) > CLICK:
            found.append({"kind": "click", "text": "Clicks at the end: cut off instead of faded out"})
    elif any(_seam_clicks(channel[:length], rate) for channel in channels):
        found.append({"kind": "click", "text": "Clicks where it repeats: the end does not meet the start"})
    return found


def _seam_clicks(channel, rate: int) -> bool:
    """Whether a loop jumps where its end meets its start, by more than its own wiggle there.

    A drum loop with a little noise moves by about as much from one sample to
    the next all the time, so a jump that size is not heard; a pad cut in the
    middle of a wave jumps far more than it ever moves. A loop that starts on a
    hit (a kick on the one) jumps there anyway, and the hit covers it.
    """
    jump = abs(channel[-1] - channel[0])
    if jump <= SEAM:
        return False
    near = max(2, rate // 100)  # 10 ms either side of the seam
    before = (sum(x * x for x in channel[-near:]) / near) ** 0.5
    if max(map(abs, channel[:near // 2])) >= 4 * max(before, QUIET):
        return False  # it starts on a hit, 4 times louder than what came before
    edge = list(channel[-near:]) + list(channel[:near])
    steps = [abs(b - a) for a, b in zip(edge, edge[1:]) if (a, b) != (edge[near - 1], edge[near])]
    return jump > SEAM_STEPS * (sum(steps) / len(steps))


def check(file_path, name: str, loop: bool = False) -> list[dict] | None:
    """The problems in a sound file; None if Kit cannot read it."""
    read = channels_of(file_path, name)
    if read is None or not read[0] or not len(read[0][0]):
        return None
    return find(read[0], read[1], loop)


def _cache(repo) -> dict:
    try:
        cache = json.loads((repo.dir / "problems.json").read_text())
    except (OSError, ValueError):
        return {}
    return cache if isinstance(cache, dict) else {}


def _key(digest: str, loop: bool) -> str:
    return f"{digest}:loop" if loop else digest  # a loop is checked differently from a one-shot


def known(repo, manifest: dict, loops: set[str]) -> dict[str, list[dict]]:
    """{path: problems} for the sounds in `manifest` already checked that have any. Quick: nothing is read."""
    cache = _cache(repo)
    found = {}
    for path, entry in manifest.items():
        problems = cache.get(_key(entry["hash"], path in loops))
        if problems:
            found[path] = problems
    return found


def unchecked(repo, manifest: dict, loops: set[str]) -> int:
    """How many sounds Kit can read in `manifest` have not been checked yet."""
    cache = _cache(repo)
    return sum(1 for path, entry in manifest.items()
               if can_read(path) and _key(entry["hash"], path in loops) not in cache)


def check_kit(repo, loops: set[str], progress=None, working: dict | None = None) -> dict[str, list[dict]]:
    """{path: problems} for every sound in the kit's folder that has any, checking those not checked before."""
    cache = _cache(repo)
    working = working if working is not None else repo.working()
    todo = [path for path in working if can_read(path) and _key(working[path]["hash"], path in loops) not in cache]
    for done, path in enumerate(todo, 1):
        cache[_key(working[path]["hash"], path in loops)] = check(repo.folder / path, path, path in loops)
        if progress:
            progress(done, len(todo))
    if todo:
        try:
            tmp = repo.dir / "problems.json.tmp"
            tmp.write_text(json.dumps(cache))
            tmp.replace(repo.dir / "problems.json")
        except OSError:
            pass  # only a cache
    return known(repo, working, loops)
