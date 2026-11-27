"""Building a pack: chosen sounds, sorted into folders by kind, as a zip ready to sell or send.

    Dark Trap Kit pack.zip
      Dark Trap Kit pack/
        Kicks/Kick 01.wav
        808s/808 01 F#.wav
        Loops/Loop 01 140bpm F#m.wav
        Contents.txt     every sound with its tempo, key or note, and loudness
        Changes.txt      the kit's changelog, when every sound in the pack is saved

The sounds are copied as they are on disk; nothing is converted. A sound
whose kind Kit does not know goes in "Other". Two sounds with the same file
name are kept apart by the folder they came from.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

from .kinds import kind_from_name, sort_kit
from .loudness import measure_kit
from .repo import KitError, Repo
from .tags import from_name

FOLDERS = {"Kick": "Kicks", "808": "808s", "Snare": "Snares", "Clap": "Claps", "Hat": "Hats",
           "Perc": "Percs", "Melodic": "Melodic", "Loop": "Loops"}


def plan(repo: Repo, paths: list[str], listen=None) -> list[dict]:
    """[{"from": kit path, "to": path in the pack, "kind", "bpm", "key", "note", "lufs"}], by folder.

    `listen(path)`, if given, gives {"bpm", "key", "note"} heard in a sound.
    """
    if not paths:
        raise KitError("choose some sounds for the pack: star them first")
    working = repo.working()
    missing = [path for path in paths if path not in working]
    if missing:
        raise KitError(f"there is no sound at {missing[0]}")
    kinds = {path: kind["kind"] for path, kind in sort_kit(repo, working=working).items()}
    levels = measure_kit(repo, working=working)
    taken: set[str] = set()
    out = []
    for path in sorted(paths):
        kind = kinds.get(path) or kind_from_name(path)
        folder = FOLDERS.get(kind, "Other")
        name = path.rsplit("/", 1)[-1]
        target = f"{folder}/{name}"
        if target.lower() in taken:  # "Kicks/hit.wav" and "Old/hit.wav": keep both
            parent = path.rsplit("/", 2)[-2] if "/" in path else "top"
            stem, dot, ext = name.rpartition(".")
            target = f"{folder}/{stem} ({parent}).{ext}" if dot else f"{folder}/{name} ({parent})"
        taken.add(target.lower())
        named = from_name(path)
        heard = (listen(path) or {}) if listen else {}
        bpm = named["bpm"] or (round(heard["bpm"]) if heard.get("bpm") else None)
        key = named["key"] or heard.get("key")
        out.append({"from": path, "to": target, "kind": kind, "bpm": bpm, "key": key,
                    "note": None if (bpm or key) else heard.get("note"),
                    "lufs": levels.get(path, {}).get("lufs")})
    return sorted(out, key=lambda item: item["to"].lower())


def contents(title: str, items: list[dict]) -> str:
    """Contents.txt: each folder with its sounds, and what is known about each."""
    lines = [title, "=" * len(title), "", f"{len(items)} sound{'s' if len(items) != 1 else ''}"]
    folder = None
    for item in items:
        here, name = item["to"].split("/", 1)
        if here != folder:
            folder = here
            lines += ["", f"{here} ({sum(1 for other in items if other['to'].startswith(here + '/'))})"]
        facts = [f"{item['bpm']} BPM" if item["bpm"] else None, item["key"], item["note"],
                 f"{item['lufs']:.1f} LUFS" if item["lufs"] is not None else None]
        details = " · ".join(fact for fact in facts if fact)
        lines.append(f"  {name}" + (f"   {details}" if details else ""))
    return "\n".join(lines) + "\n"


def build(repo: Repo, paths: list[str], folder: Path, name: str | None = None, listen=None) -> tuple[Path, int]:
    """Write "<name>.zip" into `folder` (never inside the kit). Returns the zip and how many sounds."""
    folder = Path(folder).expanduser().resolve()
    if folder == repo.folder or repo.folder in folder.parents:
        raise KitError("the pack cannot go inside the kit folder; choose a folder outside it")
    items = plan(repo, paths, listen)
    title = (name or f"{repo.folder.name} pack").replace("/", "-").strip() or "pack"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{title}.zip"
    partial = out.with_name(out.name + ".part")  # written under another name, then renamed
    working = repo.working()
    with zipfile.ZipFile(partial, "w") as archive:
        for item in items:
            # Stored, not compressed: audio barely shrinks, and it opens instantly anywhere.
            archive.write(repo.folder / item["from"], f"{title}/{item['to']}", zipfile.ZIP_STORED)
        archive.writestr(f"{title}/Contents.txt", contents(title, items))
        if repo.head() and all(repo.store.has(working[item["from"]]["hash"]) for item in items):
            from .changelog import changelog  # only when every sound is saved: else it would not list them

            archive.writestr(f"{title}/Changes.txt", changelog(repo))
    os.replace(partial, out)
    return out, len(items)
