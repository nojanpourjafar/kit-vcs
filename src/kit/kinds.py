"""What kind of sound each file is: Kick, 808, Snare, Clap, Hat, Perc, Melodic or Loop.

A file's name or folder usually says ("Kicks/Punch.wav", "hh_closed_03.wav",
"808s/Glide.wav"), and names are what the producer meant, so they win. Only
sounds whose names say nothing ("Sample 0042.wav") are listened to (see
audio.kind_of); those answers are kept in .kit/kinds.json by hash, so each
sound is listened to once.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .audio import KINDS, kind

# Words in names, for each kind. Matched as whole words, ignoring case, so
# "Kicks", "kick_01" and "BD" count but "kickstarter" does not.
WORDS = [
    ("Loop", r"loops?|bpm|\d{2,3}bpm|breaks?|drum ?loops?"),
    ("808", r"808s?|sub ?bass|subs?"),
    ("Kick", r"kicks?|kck|bd|bass ?drums?"),
    ("Snare", r"snares?|snr|sd|rim ?shots?"),
    ("Clap", r"claps?|clp|snaps?"),
    ("Hat", r"hats?|hh|hi-?hats?|ch|oh|open ?hats?|closed ?hats?|cymbals?|rides?|crash(es)?|shakers?"),
    ("Perc", r"percs?|percussion|rims?|toms?|congas?|bongos?|cowbells?|woodblocks?|clicks?|fx"),
    ("Melodic", r"melod(y|ies|ic)|synths?|pads?|keys|piano|leads?|plucks?|bells?|chords?|vox|vocals?|guitars?|strings?|flutes?|brass"),
]
_PATTERNS = [(name, re.compile(rf"(?<![a-z0-9])({words})(?![a-z])", re.I)) for name, words in WORDS]


def kind_from_name(path: str) -> str | None:
    """The kind a sound's name says, file name first, then its folders; None if nothing says."""
    for part in reversed(path.replace("\\", "/").split("/")):
        stem = part.rsplit(".", 1)[0] if "." in part else part
        stem = re.sub(r"[_\-.]+", " ", stem)
        for name, pattern in _PATTERNS:
            if pattern.search(stem):
                return name
    return None


def _cache(repo) -> dict:
    try:
        cache = json.loads((repo.dir / "kinds.json").read_text())
    except (OSError, ValueError):
        return {}
    return cache if isinstance(cache, dict) else {}


def known(repo, manifest: dict) -> dict[str, str]:
    """{path: kind} for the sounds in `manifest` whose kind is known now: from their
    names, or listened to before. Nothing is listened to here, so it is quick."""
    cache = _cache(repo)
    found = {}
    for path, entry in manifest.items():
        kind_ = kind_from_name(path) or cache.get(entry["hash"])
        if kind_ in KINDS:
            found[path] = kind_
    return found


def unsorted(repo, manifest: dict) -> int:
    """How many sounds in `manifest` have no kind from their names and have not been listened to yet."""
    cache = _cache(repo)
    return sum(1 for path, entry in manifest.items() if kind_from_name(path) is None and entry["hash"] not in cache)


def sort_kit(repo, progress=None, working: dict | None = None) -> dict[str, dict]:
    """{path: {"kind": ..., "from": "name" or "sound"}} for every sound in the kit's folder.

    Sounds whose names say nothing are listened to, once: the answer is kept in
    .kit/kinds.json by hash. Sounds Kit cannot decode, and silence, get no kind.
    `working` is the folder's manifest, if the caller has it already.
    """
    cache_file = repo.dir / "kinds.json"
    cache = _cache(repo)
    working = working if working is not None else repo.working()
    unnamed = [path for path in working if kind_from_name(path) is None]
    result = {}
    changed = False
    for done, path in enumerate(unnamed, 1):
        digest = working[path]["hash"]
        if digest not in cache or (cache[digest] is not None and cache[digest] not in KINDS):
            cache[digest] = kind(repo.folder / path, path)
            changed = True
        if cache[digest]:
            result[path] = {"kind": cache[digest], "from": "sound"}
        if progress:
            progress(done, len(unnamed))
    for path in working:
        if (named := kind_from_name(path)) is not None:
            result[path] = {"kind": named, "from": "name"}
    if changed:
        try:
            tmp = cache_file.with_name("kinds.json.tmp")
            tmp.write_text(json.dumps(cache))
            tmp.replace(cache_file)
        except OSError:
            pass  # only a cache
    return dict(sorted(result.items()))
