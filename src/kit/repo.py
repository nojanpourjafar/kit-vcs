"""The Repo: one kit folder plus the hidden .kit folder that remembers its history.

    MyKit/
      Kicks/kick_01.wav ...     <- your sounds, exactly as you keep them
      .kit/
        objects/                <- every file ever saved, named by its hash
        commits/<id>.json       <- one small file per saved version
        HEAD                    <- the ID of the latest version
        config.json             <- your name, for credits
"""

from __future__ import annotations

import getpass
import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .commits import Commit, new_commit, parse_commit
from .hashing import is_digest
from .manifest import Change, Manifest, diff, dumps, loads, scan
from .objects import ObjectStore

DIR = ".kit"


class KitError(Exception):
    """A problem the user can fix. The CLI prints the message with no traceback."""


@dataclass(frozen=True)
class Saved:
    """What `commit` reports back."""
    commit: Commit
    changes: list[Change]
    new_files: int  # files that actually had to be stored
    new_bytes: int


class Repo:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.dir = self.folder / DIR
        self.store = ObjectStore(self.dir / "objects")
        self.commits_dir = self.dir / "commits"

    # ---- setup -----------------------------------------------------------

    @classmethod
    def init(cls, folder=".", author: str | None = None) -> "Repo":
        repo = cls(folder)
        if repo.dir.exists():
            raise KitError(f"{repo.folder} is already a Kit repo")
        repo.store.root.mkdir(parents=True)
        repo.commits_dir.mkdir()
        config = {"format": 1, "author": author or _default_author()}
        (repo.dir / "config.json").write_text(json.dumps(config, indent=2))
        return repo

    @classmethod
    def find(cls, start=".") -> "Repo":
        """Find the repo that contains `start`, looking in parent folders too."""
        here = Path(start).resolve()
        for folder in [here, *here.parents]:
            if (folder / DIR).is_dir():
                return cls(folder)
        raise KitError("this folder is not a Kit repo yet; run `kit init` in your kit folder")

    @property
    def author(self) -> str:
        return json.loads((self.dir / "config.json").read_text())["author"]

    # ---- HEAD and commits ------------------------------------------------

    def head(self) -> str | None:
        """ID of the latest saved version, or None if nothing is saved yet."""
        path = self.dir / "HEAD"
        if not path.exists():
            return None
        return path.read_text().strip() or None

    def set_head(self, commit_id: str) -> None:
        _atomic_write(self.dir / "HEAD", commit_id.encode())

    def has_commit(self, commit_id: str) -> bool:
        return is_digest(commit_id) and (self.commits_dir / f"{commit_id}.json").is_file()

    def read_commit_bytes(self, commit_id: str) -> bytes:
        if not self.has_commit(commit_id):
            raise KitError(f"no such version: {commit_id}")
        return (self.commits_dir / f"{commit_id}.json").read_bytes()

    def store_commit_bytes(self, data: bytes) -> Commit:
        """Save a commit from its raw bytes (also how pull will add other people's)."""
        commit = parse_commit(data)
        _atomic_write(self.commits_dir / f"{commit.id}.json", data)
        return commit

    def load_commit(self, commit_id: str) -> Commit:
        commit = parse_commit(self.read_commit_bytes(commit_id))
        if commit.id != commit_id:
            raise KitError(
                f"version {commit_id[:8]} is damaged: its contents no longer match its ID"
            )
        return commit

    def log(self) -> list[Commit]:
        """Every version, newest first, found by walking parent links back from HEAD."""
        history = []
        commit_id = self.head()
        while commit_id:
            commit = self.load_commit(commit_id)
            history.append(commit)
            commit_id = commit.parent
        return history

    def resolve(self, ref: str) -> str:
        """Turn "v3", "latest" or the start of an ID into a full commit ID."""
        ref = ref.strip().lower()
        if self.has_commit(ref):
            return ref
        history = self.log()
        if not history:
            raise KitError("no versions saved yet")
        if ref in ("latest", "head"):
            return history[0].id
        if ref.startswith("v") and ref[1:].isdigit():
            number = int(ref[1:])
            if 1 <= number <= len(history):
                return history[-number].id
            raise KitError(f"there is no {ref}; this repo has v1 to v{len(history)}")
        matches = [c.id for c in history if ref and c.id.startswith(ref)]
        if len(matches) == 1:
            return matches[0]
        raise KitError(f"cannot tell which version {ref!r} means; try v1, v2, ... or `kit log`")

    def manifest_of(self, commit_id: str | None) -> Manifest:
        if commit_id is None:
            return {}
        return loads(self.store.read_bytes(self.load_commit(commit_id).manifest))

    # ---- the commands ----------------------------------------------------

    def status(self) -> list[Change]:
        """What changed in the folder since the latest saved version."""
        return diff(self.manifest_of(self.head()), scan(self.folder))

    def commit(self, message: str, author: str | None = None) -> Saved:
        """Save the folder as it is now as a new version."""
        message = message.strip()
        if not message:
            raise KitError("a version needs a message, like: kit commit -m \"added 5 hats\"")
        parent = self.head()
        working = scan(self.folder)
        changes = diff(self.manifest_of(parent), working)
        if not changes:
            raise KitError("nothing changed since the last version")

        new_files = new_bytes = 0
        for rel, entry in working.items():
            if self.store.has(entry["hash"]):
                continue  # this exact audio is already stored: nothing to copy
            if self.store.put_file(self.folder / rel) != entry["hash"]:
                raise KitError(f"{rel} changed while it was being saved; try again")
            new_files += 1
            new_bytes += entry["size"]

        manifest_hash = self.store.put_bytes(dumps(working))
        commit = new_commit(parent, manifest_hash, author or self.author, message)
        self.store_commit_bytes(commit.to_bytes())
        # HEAD moves last. If anything above fails, the old version is still current.
        self.set_head(commit.id)
        return Saved(commit, changes, new_files, new_bytes)

    def diff_versions(self, ref_a: str, ref_b: str) -> list[Change]:
        return diff(self.manifest_of(self.resolve(ref_a)), self.manifest_of(self.resolve(ref_b)))

    def restore(self, ref: str, force: bool = False) -> list[Change]:
        """Put the folder's files back to how they were in an earlier version.

        History is not rewritten: HEAD stays where it is, so afterwards `status`
        shows the rollback as ordinary changes you can commit as a new version.
        """
        target = self.manifest_of(self.resolve(ref))
        tracked = self.manifest_of(self.head())
        working = scan(self.folder)
        if not force and diff(tracked, working):
            raise KitError(
                "you have unsaved changes; run `kit commit` first, or add --force to discard them"
            )
        for rel in target:
            self._safe_path(rel)  # check every path before touching a single file
        # Only delete files this repo has saved before. Anything else in the
        # folder that we never tracked is left alone.
        for rel in tracked.keys() - target.keys():
            path = self._safe_path(rel)
            path.unlink(missing_ok=True)
            self._remove_empty_folders(path.parent)
        for rel, entry in target.items():
            if working.get(rel, {}).get("hash") != entry["hash"]:
                self.store.copy_to(entry["hash"], self._safe_path(rel))
        return diff(working, target)

    def credits(self) -> dict[str, Commit]:
        """For each file in the latest version, the commit that first brought in that audio.

        Credit follows the audio (its hash), not the file name, so renaming
        someone's loop does not take their credit away.
        """
        first_seen: dict[str, Commit] = {}
        for commit in reversed(self.log()):  # oldest first
            for entry in self.manifest_of(commit.id).values():
                first_seen.setdefault(entry["hash"], commit)
        latest = self.manifest_of(self.head())
        return {path: first_seen[entry["hash"]] for path, entry in sorted(latest.items())}

    # ---- helpers ---------------------------------------------------------

    def _safe_path(self, rel: str) -> Path:
        # Manifests will one day arrive from other people. Never let a path
        # like "../../somewhere" write outside the kit folder.
        parts = PurePosixPath(rel).parts
        if not parts or PurePosixPath(rel).is_absolute() or ".." in parts:
            raise KitError(f"refusing to touch a path outside the kit folder: {rel!r}")
        return self.folder.joinpath(*parts)

    def _remove_empty_folders(self, folder: Path) -> None:
        while folder != self.folder and folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
            folder = folder.parent


def _default_author() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
