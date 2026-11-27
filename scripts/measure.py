"""Measure Kit on a real kit folder: make measure KIT=~/Music/MyKit

Reads the folder and prints:

- how many files and how much audio it holds;
- how long Kit takes to read it the first time (hashing every file) and the
  second time (the way `kit status` does, checking only sizes and dates);
- how much space a new version costs, compared with zipping the kit again,
  and how much space duplicates would save;
- tempo, key and kind detection checked against real sample-pack names:
  loops with a BPM or key in the name, and sounds whose name or folder says
  what they are ("Kicks/...", "hh_closed"), are a ready-made answer key;
- what the problem finder flags, and how spread out the loudness is.

Sending the output of a real pack (`make measure KIT=...`) is the most useful
report there is: so far Kit's listening has been measured on generated sounds.

It only reads. Nothing is written into the folder, and it does not need to be
a Kit repo. Add --json for the numbers in a form other programs can read.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))  # works without installing Kit too

from kit.audio import key, kind, tempo  # noqa: E402
from kit.kinds import kind_from_name  # noqa: E402
from kit.loudness import measure_file  # noqa: E402
from kit.problems import check  # noqa: E402
from kit.wavfile import can_read  # noqa: E402
from kit.manifest import dumps, scan  # noqa: E402
from kit.tags import from_name  # noqa: E402

AUDIO = {".wav", ".mp3", ".flac", ".aif", ".aiff", ".ogg", ".m4a"}


def _kinds_check(folder: Path, readable: list[str], limit: int) -> dict:
    """The kind heard in each sound whose name says its kind, against what the name says."""
    named = [(path, kind_from_name(path)) for path in readable if kind_from_name(path)][:limit]
    confusion: dict[str, Counter] = defaultdict(Counter)
    misses = []
    for path, said in named:
        heard = kind(folder / path, path) or "none"
        confusion[said][heard] += 1
        if heard != said:
            misses.append({"path": path, "name says": said, "heard": heard})
    return {"checked": len(named), "right": sum(c[k] for k, c in confusion.items() if k in c),
            "by kind": {k: dict(c) for k, c in sorted(confusion.items())}, "misses": misses}


def _keys_check(folder: Path, readable: list[str], tags: dict, limit: int) -> dict:
    """The key heard in each sound with a key in its name. Relative keys (C maj / A min) share every note."""
    relative = {}
    notes = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    flats = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}
    for i, note in enumerate(notes):
        relative[f"{note} maj"] = f"{notes[(i + 9) % 12]} min"
        relative[f"{notes[(i + 9) % 12]} min"] = f"{note} maj"

    def plain(k: str | None) -> str | None:
        if not k:
            return None
        note, mode = k.split()
        return f"{flats.get(note, note)} {mode}"

    named = [path for path in readable if tags[path]["key"]][:limit]
    results = {"exact": 0, "relative": 0, "no answer": 0, "other": 0}
    misses = []
    for path in named:
        expected, got = plain(tags[path]["key"]), plain(key(folder / path, path))
        verdict = ("no answer" if got is None else "exact" if got == expected
                   else "relative" if relative.get(expected) == got else "other")
        results[verdict] += 1
        if verdict == "other":
            misses.append({"path": path, "name says": expected, "heard": got})
    return {"checked": len(named), "results": results, "misses": misses}


def _sound_check(folder: Path, readable: list[str], limit: int) -> dict:
    """What the problem finder flags, and the loudness spread, on up to `limit` sounds."""
    flagged: Counter = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    levels = []
    for path in readable[:limit]:
        loop = kind_from_name(path) == "Loop" or bool(from_name(path)["bpm"])
        for problem in check(folder / path, path, loop) or []:
            flagged[problem["kind"]] += 1
            if len(examples[problem["kind"]]) < 5:
                examples[problem["kind"]].append(path)
        level = measure_file(folder / path, path)
        if level:
            levels.append(level["lufs"])
    levels.sort()
    pick = lambda share: levels[min(len(levels) - 1, int(share * len(levels)))] if levels else None
    return {"checked": min(len(readable), limit), "problems": dict(flagged), "examples": dict(examples),
            "loudness": {"quietest": pick(0), "10%": pick(0.1), "middle": pick(0.5), "90%": pick(0.9),
                         "loudest": pick(0.999)} if levels else None}


def measure(folder: Path, tempo_limit: int = 300) -> dict:
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise SystemExit(f"measure: there is no folder at {folder}")

    started = time.perf_counter()
    cache: dict = {}
    manifest = scan(folder, cache)  # first read: every file is hashed
    first_read = time.perf_counter() - started
    started = time.perf_counter()
    scan(folder, cache)  # second read: sizes and dates only, as `kit status` does
    second_read = time.perf_counter() - started

    total = sum(entry["size"] for entry in manifest.values())
    kinds = Counter(Path(path).suffix.lower() for path in manifest)
    audio = {path: entry for path, entry in manifest.items() if Path(path).suffix.lower() in AUDIO}

    # Files with the same hash are the same sound saved twice. Kit stores each hash once.
    by_hash = defaultdict(list)
    for path, entry in manifest.items():
        by_hash[entry["hash"]].append(path)
    copies = [paths for paths in by_hash.values() if len(paths) > 1]
    duplicate_files = sum(len(paths) - 1 for paths in copies)
    duplicate_bytes = sum((len(paths) - 1) * manifest[paths[0]]["size"] for paths in copies)

    tags = {path: from_name(path) for path in audio}
    named_bpm = {path: t["bpm"] for path, t in tags.items() if t["bpm"]}
    checked = {"exact": 0, "half or double": 0, "no answer": 0, "other": 0}
    misses = []
    wavs = [path for path in sorted(named_bpm) if can_read(path)][:tempo_limit]
    started = time.perf_counter()
    for path in wavs:
        expected, got = named_bpm[path], tempo(folder / path, path)
        if got is None:
            verdict = "no answer"
        elif abs(got - expected) <= 1:
            verdict = "exact"
        elif abs(got - expected * 2) <= 2 or abs(got - expected / 2) <= 1:
            verdict = "half or double"
        else:
            verdict = "other"
        checked[verdict] += 1
        if verdict in ("other", "half or double"):
            misses.append({"path": path, "name says": expected, "estimate": got})
    tempo_seconds = time.perf_counter() - started

    unnamed = [path for path in sorted(audio) if can_read(path) and path not in named_bpm][:tempo_limit]
    estimated = sum(tempo(folder / path, path) is not None for path in unnamed)

    readable = [path for path in sorted(audio) if can_read(path)]
    started = time.perf_counter()
    kinds_found = _kinds_check(folder, readable, tempo_limit)
    keys_found = _keys_check(folder, readable, tags, tempo_limit)
    sounds_found = _sound_check(folder, readable, tempo_limit)
    listening_seconds = time.perf_counter() - started

    return {
        "folder": str(folder),
        "files": len(manifest),
        "bytes": total,
        "audio files": len(audio),
        "kinds": dict(kinds.most_common()),
        "first read seconds": round(first_read, 3),
        "second read seconds": round(second_read, 4),
        "version without changes bytes": len(dumps(manifest)),
        "average file bytes": total // len(manifest) if manifest else 0,
        "duplicate files": duplicate_files,
        "duplicate bytes": duplicate_bytes,
        "bpm in name": len(named_bpm),
        "key in name": sum(1 for t in tags.values() if t["key"]),
        "tempo checked": len(wavs),
        "tempo results": checked,
        "tempo seconds per loop": round(tempo_seconds / len(wavs), 3) if wavs else None,
        "tempo misses": misses,
        "unnamed wav loops tried": len(unnamed),
        "unnamed wav loops estimated": estimated,
        "kind check": kinds_found,
        "key check": keys_found,
        "sound check": sounds_found,
        "listening seconds": round(listening_seconds, 1),
    }


def size(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


def report(m: dict) -> str:
    share = lambda part, whole: f"{part} ({round(part * 100 / whole)}%)" if whole else str(part)
    speed = m["bytes"] / m["first read seconds"] if m["first read seconds"] else 0
    kinds = ", ".join(f"{kind or 'no extension'} {count}" for kind, count in list(m["kinds"].items())[:6])
    lines = [
        f"Kit measurements for {m['folder']}",
        "",
        "Files",
        f"  {m['files']} files, {size(m['bytes'])}; {m['audio files']} are audio ({kinds})",
        "",
        "Reading the kit",
        f"  First read, hashing every file:   {m['first read seconds']:.2f} s ({size(speed)}/s)",
        f"  Next reads, as kit status does:   {m['second read seconds']:.3f} s (checks only sizes and dates)",
        "",
        "Space",
        f"  A new version with nothing changed adds {size(m['version without changes bytes'])} (the list of files),",
        f"  where zipping the kit again would take {size(m['bytes'])}.",
        f"  Changing one sound of average size adds about {size(m['average file bytes'] + m['version without changes bytes'])}.",
        f"  Duplicates: {m['duplicate files']} files are exact copies of another; storing each once saves {size(m['duplicate bytes'])}.",
        "",
        "Tempo and key",
        f"  {m['bpm in name']} sounds have a BPM in their name and {m['key in name']} have a key.",
    ]
    results, checked = m["tempo results"], m["tempo checked"]
    if checked:
        lines += [
            f"  Tempo from the audio, checked against the BPM in the name ({checked} WAV and AIFF files):",
            f"    exact (within 1 BPM):  {share(results['exact'], checked)}",
            f"    half or double:        {share(results['half or double'], checked)}",
            f"    no answer:             {share(results['no answer'], checked)}   (one-shots land here, which is right)",
            f"    other:                 {share(results['other'], checked)}",
            f"    {m['tempo seconds per loop']} s per file",
        ]
        if m["tempo misses"]:
            lines.append("  Misses (name says -> estimate):")
            lines += [f"    {miss['name says']:>5} -> {miss['estimate']:>6}   {miss['path']}" for miss in m["tempo misses"][:12]]
    else:
        lines.append("  No WAV or AIFF files with a BPM in their name, so tempo detection could not be checked here.")
    if m["unnamed wav loops tried"]:
        lines.append(f"  WAV and AIFF files with no BPM in the name that got an estimate: "
                     f"{m['unnamed wav loops estimated']} of {m['unnamed wav loops tried']}")
    lines += _report_listening(m, share)
    return "\n".join(lines)


def _report_listening(m: dict, share) -> list[str]:
    lines = ["", "Kind (Kick, 808, Snare, Clap, Hat, Perc, Melodic, Loop)"]
    kc = m["kind check"]
    if kc["checked"]:
        lines.append(f"  Heard from the audio, checked against what the name or folder says ({kc['checked']} sounds):"
                     f" right {share(kc['right'], kc['checked'])}")
        for said, heard in kc["by kind"].items():
            total = sum(heard.values())
            wrong = ", ".join(f"{h} {n}" for h, n in sorted(heard.items(), key=lambda x: -x[1]) if h != said)
            lines.append(f"    {said:<8} {heard.get(said, 0)}/{total} right" + (f"   (heard as: {wrong})" if wrong else ""))
        if kc["misses"]:
            lines.append("  Some misses (name says -> heard):")
            lines += [f"    {x['name says']:>7} -> {x['heard']:<7}  {x['path']}" for x in kc["misses"][:12]]
    else:
        lines.append("  No WAV or AIFF files whose names say their kind, so kinds could not be checked here.")
    lines += ["", "Key"]
    keys = m["key check"]
    if keys["checked"]:
        r, n = keys["results"], keys["checked"]
        lines += [f"  Key from the audio, checked against the key in the name ({n} files):",
                  f"    exact:              {share(r['exact'], n)}",
                  f"    relative key:       {share(r['relative'], n)}   (C maj for A min: the same notes)",
                  f"    no answer:          {share(r['no answer'], n)}   (drums and one-shots land here, which is right)",
                  f"    other:              {share(r['other'], n)}"]
        lines += [f"    {x['name says']:>7} -> {x['heard'] or '-':<7}  {x['path']}" for x in keys["misses"][:8]]
    else:
        lines.append("  No WAV or AIFF files with a key in their name, so keys could not be checked here.")
    sc = m["sound check"]
    lines += ["", f"Problems and loudness ({sc['checked']} sounds)"]
    if sc["problems"]:
        for problem, count in sorted(sc["problems"].items(), key=lambda x: -x[1]):
            lines.append(f"  {problem:<9} {count}   e.g. {', '.join(sc['examples'][problem][:3])}")
        lines.append("  Listen to a few: a flag on a sound that sounds fine is a false alarm worth reporting.")
    else:
        lines.append("  No problems flagged.")
    if sc["loudness"]:
        lv = sc["loudness"]
        lines.append(f"  Loudness (LUFS): quietest {lv['quietest']}, 10% {lv['10%']}, middle {lv['middle']},"
                     f" 90% {lv['90%']}, loudest {lv['loudest']}")
    lines += ["", f"Listening took {m['listening seconds']} s."]
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Measure Kit on a kit folder (read only).")
    parser.add_argument("folder", help="the kit folder to measure")
    parser.add_argument("--json", action="store_true", help="print the numbers as JSON")
    parser.add_argument("--tempo-limit", type=int, default=300, help="check at most this many files for tempo")
    args = parser.parse_args(argv)
    numbers = measure(Path(args.folder), args.tempo_limit)
    print(json.dumps(numbers, indent=2) if args.json else report(numbers))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
