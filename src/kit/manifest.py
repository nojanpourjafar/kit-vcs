"""Manifests: a snapshot of a kit folder, written as "path -> hash".

    {"Kicks/kick_01.wav": {"hash": "ab12...", "size": 30914}, ...}

A manifest holds no audio, only names and hashes, so it is tiny. Comparing two
manifests tells you exactly what changed between two versions of a kit, and
three of them (a shared starting point plus two edited copies) can be merged.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from .hashing import hash_file

Manifest = dict[str, dict]

IGNORE_FILE = ".kitignore"

# Clutter that operating systems drop into folders. Dot-files and dot-folders
# (.DS_Store, .git, our own .kit) are skipped by a separate rule in scan().
SKIP_NAMES = {"Thumbs.db", "desktop.ini"}

# A file modified in the last two seconds can change again without its
# timestamp moving, so its hash is not remembered until it has settled.
RACY_NS = 2_000_000_000


def load_ignore(folder: Path) -> list[str]:
    """Patterns from .kitignore: one per line, `#` starts a comment."""
    try:
        lines = (Path(folder) / IGNORE_FILE).read_text().splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def is_ignored(rel: str, patterns: list[str]) -> bool:
    """`*.asd` matches by file name or full path; `Drafts/` matches a folder."""
    parts = rel.split("/")
    for pattern in patterns:
        if pattern.endswith("/"):
            if pattern[:-1] in parts[:-1] or rel.startswith(pattern):
                return True
        elif fnmatchcase(rel, pattern) or fnmatchcase(parts[-1], pattern):
            return True
    return False


def scan(folder: Path, cache: dict | None = None) -> Manifest:
    """Hash every file in a kit folder and return its manifest.

    `cache` maps path -> [size, modified time, hash] from the previous scan.
    A file whose size and modified time are unchanged is not hashed again,
    which is what keeps `status` fast on a multi-gigabyte kit. The cache is
    updated in place.
    """
    folder = Path(folder)
    patterns = load_ignore(folder)
    manifest: Manifest = {}
    fresh: dict = {}
    now = time.time_ns()
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if (name.startswith(".") and name != IGNORE_FILE) or name in SKIP_NAMES:
                continue
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                continue
            # Always forward slashes, so a kit saved on Windows restores on a Mac.
            rel = path.relative_to(folder).as_posix()
            if is_ignored(rel, patterns):
                continue
            stat = path.stat()
            known = cache.get(rel) if cache is not None else None
            if known and known[0] == stat.st_size and known[1] == stat.st_mtime_ns:
                digest = known[2]
            else:
                digest = hash_file(path)
            manifest[rel] = {"hash": digest, "size": stat.st_size}
            if now - stat.st_mtime_ns > RACY_NS:
                fresh[rel] = [stat.st_size, stat.st_mtime_ns, digest]
    if cache is not None:
        cache.clear()
        cache.update(fresh)
    return manifest


def dumps(manifest: Manifest) -> bytes:
    """One fixed byte form per manifest, so equal manifests always hash the same."""
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()


def loads(data: bytes) -> Manifest:
    return json.loads(data)


@dataclass(frozen=True)
class Change:
    kind: str  # "added", "removed", "modified" or "renamed"
    path: str
    old_path: str | None = None  # only set for "renamed"


def diff(old: Manifest, new: Manifest) -> list[Change]:
    """List what changed going from `old` to `new`."""
    removed = sorted(old.keys() - new.keys())
    added = sorted(new.keys() - old.keys())
    changes = [
        Change("modified", path)
        for path in sorted(old.keys() & new.keys())
        if old[path]["hash"] != new[path]["hash"]
    ]

    # A rename is a path that vanished while the exact same audio appeared
    # under a new path. We can spot it because the hash did not change.
    gone_by_hash: dict[str, list[str]] = {}
    for path in removed:
        gone_by_hash.setdefault(old[path]["hash"], []).append(path)
    for path in added:
        candidates = gone_by_hash.get(new[path]["hash"])
        if candidates:
            changes.append(Change("renamed", path, candidates.pop(0)))
        else:
            changes.append(Change("added", path))

    still_gone = {path for paths in gone_by_hash.values() for path in paths}
    changes += [Change("removed", path) for path in removed if path in still_gone]
    return sorted(changes, key=lambda change: change.path)


def merge_manifests(base: Manifest, ours: Manifest, theirs: Manifest,
                    their_name: str, kept: dict | None = None) -> tuple[Manifest, list[str]]:
    """Three-way merge. Returns the merged manifest and the paths in conflict.

    `base` is the last version both sides share. For each path:
      * changed on one side only  -> take that side's change;
      * changed the same way      -> nothing to do;
      * changed differently       -> a conflict.
    Audio cannot be merged line by line the way code can, so a conflict never
    throws work away: both files are kept, with theirs saved beside ours as
    "name (from Author).wav". If `kept` is given, it is filled with
    {path: the name their copy was saved under}.
    """
    merged = dict(ours)
    conflicts: list[str] = []
    for path in sorted(base.keys() | ours.keys() | theirs.keys()):
        entries = [side.get(path) for side in (base, ours, theirs)]
        base_hash, our_hash, their_hash = (e["hash"] if e else None for e in entries)
        their_entry = entries[2]
        if our_hash == their_hash or their_hash == base_hash:
            continue  # identical on both sides, or only we touched it
        if our_hash == base_hash:  # only they touched it
            if their_entry is None:
                merged.pop(path, None)
            else:
                merged[path] = their_entry
            continue
        conflicts.append(path)
        if our_hash is None:
            merged[path] = their_entry  # we deleted it, they edited it: keep the edit
        elif their_hash is not None:
            beside = _beside(path, their_name, merged, theirs)
            merged[beside] = their_entry
            if kept is not None:
                kept[path] = beside
        # else: they deleted it and we edited it, so our edit stays
    return merged, conflicts


def _beside(path: str, who: str, *taken: Manifest) -> str:
    """A free file name next to `path`, such as "hat_01 (from Lucas).wav"."""
    folder, _, name = path.rpartition("/")
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:  # no extension, or a dot-file such as .kitignore
        stem, dot, ext = name, "", ""
    who = "".join(c for c in who if c.isalnum() or c in " -_").strip() or "other"
    number = 1
    while True:
        suffix = f" (from {who})" if number == 1 else f" (from {who} {number})"
        candidate = (folder + "/" if folder else "") + stem + suffix + ("." + ext if dot else "")
        if not any(candidate in manifest for manifest in taken):
            return candidate
        number += 1
