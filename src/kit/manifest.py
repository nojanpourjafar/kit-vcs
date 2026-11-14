"""Manifests: a snapshot of a kit folder, written as "path -> hash".

    {"Kicks/kick_01.wav": {"hash": "ab12...", "size": 30914}, ...}

A manifest holds no audio, only names and hashes, so it is tiny. Comparing two
manifests tells you exactly what changed between two versions of a kit.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .hashing import hash_file

Manifest = dict[str, dict]

# Clutter that operating systems drop into folders. Dot-files and dot-folders
# (.DS_Store, .git, our own .kit) are skipped by a separate rule in scan().
SKIP_NAMES = {"Thumbs.db", "desktop.ini"}


def scan(folder: Path) -> Manifest:
    """Hash every file in a kit folder and return its manifest."""
    folder = Path(folder)
    manifest: Manifest = {}
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith(".") or name in SKIP_NAMES:
                continue
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                continue
            # Always forward slashes, so a kit saved on Windows restores on a Mac.
            rel = path.relative_to(folder).as_posix()
            manifest[rel] = {"hash": hash_file(path), "size": path.stat().st_size}
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
