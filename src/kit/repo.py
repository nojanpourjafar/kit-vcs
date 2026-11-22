"""The Repo: one kit folder plus the hidden .kit folder that remembers its history.

    MyKit/
      Kicks/kick_01.wav ...     <- your sounds, exactly as you keep them
      .kit/
        objects/                <- every file ever saved, named by its hash
        commits/<id>.json       <- one small file per saved version
        HEAD                    <- the ID of the latest version
        config.json             <- author name and default remote
        index.json              <- cache of file hashes, safe to delete
"""

from __future__ import annotations

import getpass
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import names
from .commits import Commit, lineage, new_commit, parse_commit
from .hashing import hash_file, is_digest
from .manifest import Change, Manifest, diff, dumps, loads, merge_manifests, scan
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


@dataclass(frozen=True)
class Merged:
    """What `merge` reports back."""
    kind: str  # "up-to-date", "fast-forward" or "merge"
    commit: Commit | None = None  # the merge commit, when one was made
    changes: list[Change] = field(default_factory=list)  # what changed in the folder
    conflicts: list[str] = field(default_factory=list)  # paths where both versions were kept


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
        repo._save_config({"format": 1, "author": author or _default_author()})
        return repo

    @classmethod
    def find(cls, start=".") -> "Repo":
        """Find the repo that contains `start`, looking in parent folders too."""
        here = Path(start).resolve()
        for folder in [here, *here.parents]:
            if (folder / DIR).is_dir():
                return cls(folder)
        raise KitError("this folder is not a Kit repo yet; run `kit init` in your kit folder")

    def _config(self) -> dict:
        return json.loads((self.dir / "config.json").read_text())

    def _save_config(self, config: dict) -> None:
        _atomic_write(self.dir / "config.json", json.dumps(config, indent=2).encode())

    @property
    def author(self) -> str:
        return self._config()["author"]

    def remote(self) -> str | None:
        """The saved default remote, as the user typed it."""
        return self._config().get("remote")

    def set_remote(self, spec: str) -> None:
        self._save_config({**self._config(), "remote": spec})

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
        """Save a commit from its raw bytes (also how pull adds other people's)."""
        commit = parse_commit(data)
        _atomic_write(self.commits_dir / f"{commit.id}.json", data)
        return commit

    def load_commit(self, commit_id: str) -> Commit:
        try:
            commit = parse_commit(self.read_commit_bytes(commit_id))
        except (ValueError, KeyError):
            commit = None
        if commit is None or commit.id != commit_id:
            raise KitError(
                f"version {commit_id[:8]} is damaged: its contents no longer match its ID"
            )
        return commit

    def log(self) -> list[Commit]:
        """The main line of versions, newest first.

        After a merge this follows the first parent, so v1, v2, v3 ... always
        number the versions saved in this repo. Versions brought in by a merge
        hang off the merge commit and are reached through `ancestors`.
        """
        history = []
        commit_id = self.head()
        while commit_id:
            commit = self.load_commit(commit_id)
            history.append(commit)
            commit_id = commit.parent
        return history

    def ancestors(self, commit_id: str | None) -> set[str]:
        """IDs of a version and everything before it, across merges."""
        return {commit.id for commit in lineage(commit_id, self.load_commit)}

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
        everything = self.ancestors(history[0].id)
        matches = [commit_id for commit_id in everything if ref and commit_id.startswith(ref)]
        if len(matches) == 1:
            return matches[0]
        raise KitError(f"cannot tell which version {ref!r} means; try v1, v2, ... or `kit log`")

    def manifest_of(self, commit_id: str | None) -> Manifest:
        if commit_id is None:
            return {}
        return loads(self.store.read_bytes(self.load_commit(commit_id).manifest))

    # ---- the commands ----------------------------------------------------

    def working(self) -> Manifest:
        """The folder as it is right now, saved or not."""
        return self._scan()

    def status(self) -> list[Change]:
        """What changed in the folder since the latest saved version."""
        return diff(self.manifest_of(self.head()), self._scan())

    def commit(self, message: str, author: str | None = None) -> Saved:
        """Save the folder as it is now as a new version."""
        message = message.strip()
        if not message:
            raise KitError("a version needs a message, like: kit commit -m \"added 5 hats\"")
        parent = self.head()
        working = self._scan()
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
        working = self._scan()
        if not force and diff(tracked, working):
            raise KitError(
                "you have unsaved changes; run `kit commit` first, or add --force to discard them"
            )
        self._write_files(target, working, tracked)
        return diff(working, target)

    def merge(self, their_id: str) -> Merged:
        """Combine another version (already fetched into this repo) with ours.

        Three outcomes:
          * they have nothing new          -> "up-to-date";
          * we have nothing new            -> "fast-forward": just move to theirs;
          * both sides saved new versions  -> "merge": a commit with two parents.
        """
        our_id = self.head()
        ours = self.manifest_of(our_id)
        working = self._scan()
        if diff(ours, working):
            raise KitError("you have unsaved changes; run `kit commit` first, then try again")

        mine = self.ancestors(our_id)
        if their_id in mine:
            return Merged("up-to-date")
        theirs = self.manifest_of(their_id)
        if our_id is None or our_id in self.ancestors(their_id):
            self._write_files(theirs, working, ours)
            self.set_head(their_id)
            return Merged("fast-forward", changes=diff(ours, theirs))

        # The last version both sides share is where the two histories split.
        base_id = next(
            (c.id for c in reversed(lineage(their_id, self.load_commit)) if c.id in mine), None
        )
        their_author = self.load_commit(their_id).author
        kept: dict[str, str] = {}
        merged, conflicts = merge_manifests(self.manifest_of(base_id), ours, theirs, their_author, kept)
        self._write_files(merged, working, ours)
        commit = new_commit(
            our_id, self.store.put_bytes(dumps(merged)), self.author,
            f"Merged versions from {their_author}", merged=their_id,
        )
        self.store_commit_bytes(commit.to_bytes())
        self.set_head(commit.id)
        if kept:  # remember them, so they can be listened to and settled later
            self._save_conflicts(self._load_conflicts() + [
                {"path": path, "theirs": beside, "author": their_author} for path, beside in sorted(kept.items())
            ])
        return Merged("merge", commit, diff(ours, merged), conflicts)

    # ---- conflicts ----------------------------------------------------------

    def conflicts(self) -> list[dict]:
        """Sounds both sides changed, still waiting for a choice.

        Each is {"path": yours, "theirs": their copy beside it, "author": who}.
        One that was settled by hand (a file deleted or renamed in Finder) drops
        off the list by itself.
        """
        open_ = [c for c in self._load_conflicts()
                 if (self.folder / c["path"]).is_file() and (self.folder / c["theirs"]).is_file()]
        if open_ != self._load_conflicts():
            self._save_conflicts(open_)
        return open_

    def settle(self, path: str, keep: str) -> str:
        """Settle a conflict: keep "mine", "theirs", or "both". Returns what was done.

        Like `restore`, this only changes files. The choice becomes an ordinary
        change that is shared by saving a version.
        """
        conflict = next((c for c in self.conflicts() if c["path"] == path), None)
        if conflict is None:
            raise KitError(f"{path} has no open conflict")
        mine, theirs = self._safe_path(conflict["path"]), self._safe_path(conflict["theirs"])
        if keep == "mine":
            theirs.unlink()
            done = f"Kept your {path}; {conflict['author']}'s copy was removed."
        elif keep == "theirs":
            os.replace(theirs, mine)  # their audio takes the original name
            done = f"Kept {conflict['author']}'s {path}; yours was replaced."
        elif keep == "both":
            done = f"Kept both {path} and {conflict['theirs']}."
        else:
            raise KitError('keep must be "mine", "theirs" or "both"')
        self._save_conflicts([c for c in self._load_conflicts() if c["path"] != path])
        return done

    def _load_conflicts(self) -> list[dict]:
        try:
            data = json.loads((self.dir / "conflicts.json").read_text())
        except (OSError, ValueError):
            return []
        return [c for c in data if isinstance(c, dict) and {"path", "theirs", "author"} <= c.keys()]

    def _save_conflicts(self, conflicts: list[dict]) -> None:
        _atomic_write(self.dir / "conflicts.json", json.dumps(conflicts, indent=2).encode())

    def credits(self, ref: str | None = None) -> dict[str, Commit]:
        """For each file in a version, the commit that first brought in that audio.

        Credit follows the audio (its hash), not the file name, so renaming
        someone's loop does not take their credit away. Merged-in versions are
        included, so a contributor keeps credit for what they added.
        """
        tip = self.resolve(ref) if ref else self.head()
        first_seen: dict[str, Commit] = {}
        for commit in lineage(tip, self.load_commit):  # oldest first
            for entry in loads(self.store.read_bytes(commit.manifest)).values():
                first_seen.setdefault(entry["hash"], commit)
        latest = self.manifest_of(tip)
        return {path: first_seen[entry["hash"]] for path, entry in sorted(latest.items())}

    def verify(self) -> list[str]:
        """Re-hash everything in .kit and report anything damaged or missing."""
        problems = []
        for digest in self.store.all_hashes():
            if hash_file(self.store.path_for(digest)) != digest:
                problems.append(f"stored file {digest[:8]} is damaged")
        for path in sorted(self.commits_dir.glob("*.json")):
            try:
                commit = self.load_commit(path.stem)
            except KitError as err:
                problems.append(str(err))
                continue
            if not self.store.has(commit.manifest):
                problems.append(f"version {commit.id[:8]} has lost its list of files")
                continue
            for rel, entry in loads(self.store.read_bytes(commit.manifest)).items():
                if not self.store.has(entry["hash"]):
                    problems.append(f"version {commit.id[:8]} has lost the audio for {rel}")
        return problems

    # ---- helpers ---------------------------------------------------------

    def _scan(self) -> Manifest:
        """Scan the folder, reusing hashes of files that have not changed."""
        index = self.dir / "index.json"
        try:
            cache = json.loads(index.read_text())
        except (OSError, ValueError):
            cache = {}
        if not isinstance(cache, dict):
            cache = {}
        before = dict(cache)
        manifest = scan(self.folder, cache)
        if cache != before:
            try:
                _atomic_write(index, json.dumps(cache).encode())
            except OSError:
                pass  # the cache is only a speed-up
        return manifest

    def _write_files(self, target: Manifest, working: Manifest, tracked: Manifest) -> None:
        """Make the folder match `target`."""
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

    def _safe_path(self, rel: str) -> Path:
        # Manifests arrive from other people. Never let a path like
        # "../../somewhere" write outside the kit folder.
        parts = PurePosixPath(rel).parts
        if not parts or PurePosixPath(rel).is_absolute() or ".." in parts:
            raise KitError(f"refusing to touch a path outside the kit folder: {rel!r}")
        if sys.platform == "win32" and (reason := names.problem(rel)):
            # Checked before any file is written, so the folder is never left half updated.
            raise KitError(f"cannot write {rel} on Windows: {reason}. Ask whoever added it to rename it.")
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
