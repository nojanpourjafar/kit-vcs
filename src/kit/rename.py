"""Naming sounds whose names say nothing ("Sample 0042.wav") after what Kit heard in them.

    Sample 0042.wav     ->  Kick 01.wav
    Audio 7.wav         ->  808 01 F#.wav          (the note it plays)
    Untitled loop.wav   ->  Loop 01 140bpm F#m.wav  (tempo and key)

Only sounds whose names give no kind are renamed, and only when Kit heard a
kind. The new names use the forms Kit reads back ("140bpm", "F#m"), so the
tempo and key show and can be searched like any other. Numbers carry on from
the ones already in the folder. A rename is a move: once saved, a version
records it as a rename, and the sound keeps its credit.
"""

from __future__ import annotations

import re
from pathlib import Path

from .kinds import kind_from_name
from .repo import KitError
from .tags import from_name


def _short_key(key: str) -> str:
    """"F# min" -> "F#m", "C maj" -> "Cmaj": the forms producers write in names."""
    note, mode = key.split()
    return f"{note}m" if mode == "min" else f"{note}maj"


def _taken_numbers(folder: str, kind: str, paths) -> set[int]:
    pattern = re.compile(rf"^{re.escape(kind)} (\d+)\b", re.I)
    numbers = set()
    for path in paths:
        parent, _, name = path.rpartition("/")
        if parent == folder and (match := pattern.match(name)):
            numbers.add(int(match.group(1)))
    return numbers


def suggest(working: dict, heard_kinds: dict[str, str], listen) -> list[dict]:
    """[{"from": old path, "to": new path, "kind", "bpm", "key", "note"}] for each sound to rename.

    `heard_kinds` is {path: kind} for sounds whose kind came from listening;
    `listen(path)` gives {"bpm", "key", "note"} heard in a sound.
    """
    unnamed = sorted(path for path in working if kind_from_name(path) is None and path in heard_kinds)
    taken_paths = {path.lower() for path in working}
    numbers: dict[tuple[str, str], set[int]] = {}
    plan = []
    for path in unnamed:
        kind = heard_kinds[path]
        folder, _, name = path.rpartition("/")
        ext = Path(name).suffix
        named = from_name(path)
        heard = listen(path) or {}
        bpm = named["bpm"] or (round(heard["bpm"]) if heard.get("bpm") else None)
        key = named["key"] or heard.get("key")
        note = None if (bpm or key) else heard.get("note")
        used = numbers.setdefault((folder, kind), _taken_numbers(folder, kind, working))
        number = 1
        while True:
            while number in used:
                number += 1
            parts = [kind, f"{number:02d}"]
            if kind == "Loop" or bpm or key:
                parts += [f"{bpm}bpm"] if bpm else []
                parts += [_short_key(key)] if key else []
            elif note:
                parts.append(note)
            new = (f"{folder}/" if folder else "") + " ".join(parts) + ext
            if new.lower() not in taken_paths:
                break
            number += 1
        used.add(number)
        taken_paths.add(new.lower())
        plan.append({"from": path, "to": new, "kind": kind, "bpm": bpm, "key": key, "note": note})
    return plan


def apply(folder: Path, plan: list[dict]) -> int:
    """Rename the files. Every move is checked before any is made, so it never stops half way."""
    folder = Path(folder)
    for move in plan:
        old, new = folder / move["from"], folder / move["to"]
        if not old.is_file():
            raise KitError(f"{move['from']} is not there any more")
        if new.exists():
            raise KitError(f"there is already a file at {move['to']}")
        if Path(move["to"]).parent != Path(move["from"]).parent or ".." in move["to"].split("/"):
            raise KitError(f"{move['to']} is not in the same folder as {move['from']}")
    for move in plan:
        (folder / move["from"]).rename(folder / move["to"])
    return len(plan)
