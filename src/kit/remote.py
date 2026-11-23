"""Sharing a repo: remotes, push, pull and clone.

A remote is anywhere a copy of the history can live. Two kinds exist:

  * `FolderRemote` is just another folder. Point it at a USB drive or a
    Google Drive / Dropbox synced folder and two producers can share a kit
    with no server at all.
  * `S3Remote` (in s3remote.py) is a bucket on Amazon S3, Cloudflare R2 or
    anything else that speaks the S3 API.

Both offer the same methods, listed in `Remote`, so push and pull are written
once and work with either.

WAV files that are stored compressed (see pack.py) travel compressed too: a
push sends the packed file, about half the size, and a pull checks that it
unpacks to the right hash before keeping it. Everyone sharing a kit then needs
Kit 0.15 or newer, since older versions only know plain files.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .commits import Commit, lineage, parse_commit
from .hashing import is_digest
from .manifest import Change, loads
from .objects import ObjectStore
from .repo import KitError, Repo


class Remote(Protocol):
    """The twelve things any remote must be able to do."""

    def has_object(self, digest: str) -> bool: ...
    def upload_object(self, digest: str, src: Path) -> None: ...
    def download_object(self, digest: str, dest: Path) -> None: ...
    def has_commit(self, commit_id: str) -> bool: ...
    def read_commit(self, commit_id: str) -> bytes: ...
    def write_commit(self, commit_id: str, data: bytes) -> None: ...
    def read_head(self) -> str | None: ...
    def write_head(self, commit_id: str) -> None: ...
    def read_names(self) -> dict[str, str]: ...
    def write_names(self, names: dict[str, str]) -> None: ...
    def upload_packed(self, digest: str, blob: bytes) -> None: ...
    def download_packed(self, digest: str) -> bytes | None: ...


class FolderRemote:
    """A remote stored in a plain folder, laid out like a .kit folder."""

    def __init__(self, path):
        self.path = Path(path)
        self.store = ObjectStore(self.path / "objects")
        self.commits_dir = self.path / "commits"

    def has_object(self, digest: str) -> bool:
        return self.store.has(digest)

    def upload_object(self, digest: str, src: Path) -> None:
        if self.store.put_file(src) != digest:
            raise ValueError(f"{src} does not match the hash it was uploaded as")

    def download_object(self, digest: str, dest: Path) -> None:
        self.store.copy_to(digest, dest)

    def upload_packed(self, digest: str, blob: bytes) -> None:
        self.store.put_packed(digest, blob)

    def download_packed(self, digest: str) -> bytes | None:
        return self.store.packed_bytes(digest)

    def has_commit(self, commit_id: str) -> bool:
        return is_digest(commit_id) and (self.commits_dir / f"{commit_id}.json").is_file()

    def read_commit(self, commit_id: str) -> bytes:
        if not self.has_commit(commit_id):
            raise FileNotFoundError(f"the remote has no version {commit_id}")
        return (self.commits_dir / f"{commit_id}.json").read_bytes()

    def write_commit(self, commit_id: str, data: bytes) -> None:
        if not is_digest(commit_id):
            raise ValueError(f"not a commit ID: {commit_id!r}")
        self.commits_dir.mkdir(parents=True, exist_ok=True)
        self._write(self.commits_dir / f"{commit_id}.json", data)

    def read_head(self) -> str | None:
        path = self.path / "HEAD"
        if not path.exists():
            return None
        return path.read_text().strip() or None

    def write_head(self, commit_id: str) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        self._write(self.path / "HEAD", commit_id.encode())

    def read_names(self) -> dict[str, str]:
        return _parse_names((self.path / "names.json").read_bytes() if (self.path / "names.json").exists() else None)

    def write_names(self, names: dict[str, str]) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        self._write(self.path / "names.json", json.dumps(dict(sorted(names.items())), indent=2).encode())

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)


def _parse_names(data: bytes | None) -> dict[str, str]:
    """A remote's names file, read defensively: it comes from other people."""
    try:
        names = json.loads(data) if data else {}
    except ValueError:
        return {}
    if not isinstance(names, dict):
        return {}
    return {k: v for k, v in names.items() if isinstance(k, str) and isinstance(v, str) and is_digest(v)}


def _same_name(name: str, names: dict[str, str]) -> str | None:
    """The spelling of `name` in `names`, ignoring capital letters, or None."""
    return next((existing for existing in names if existing.lower() == name.lower()), None)


def names_note(result: SyncResult, sent: bool) -> str:
    """One sentence about version names after a push or pull, or "" when there is nothing to say."""
    parts = []
    if result.names:
        parts.append(f"{'Shared' if sent else 'Got'} {result.names} version name(s).")
    for name in result.name_clashes:
        parts.append(f'"{name}" names a different version {"in the shared folder" if sent else "here"}; '
                     f"each side keeps its own.")
    return " ".join(parts)


def share_names(repo: Repo, remote: Remote) -> tuple[int, list[str]]:
    """After a push: send new names, and removals. Returns (changes made, clashing names)."""
    theirs = remote.read_names()
    before = dict(theirs)
    clashes = []
    removed = repo.removed_names()
    for name, commit_id in removed.items():
        same = _same_name(name, theirs)
        if same is not None and theirs[same] == commit_id:
            del theirs[same]  # still meaning what we removed: remove it there too
    for name, commit_id in repo.names().items():
        same = _same_name(name, theirs)
        if same is None:
            if remote.has_commit(commit_id):
                theirs[name] = commit_id
        elif theirs[same] != commit_id:
            clashes.append(name)  # someone used the name for another version first
    if theirs != before:
        remote.write_names(theirs)
    repo._save_names(repo.names(), {})  # every removal is now shared (or meant something else there)
    changes = len(theirs.keys() ^ before.keys()) + sum(theirs[k] != before[k] for k in theirs.keys() & before.keys())
    return changes, clashes


def take_names(repo: Repo, remote: Remote) -> tuple[int, list[str]]:
    """After a pull: take the shared folder's names. Returns (names added, clashing names)."""
    mine, removed = repo.names(), repo.removed_names()
    added, clashes = 0, []
    for name, commit_id in remote.read_names().items():
        same = _same_name(name, mine)
        if same is not None:
            if mine[same] != commit_id:
                clashes.append(name)  # we use the name for another version; ours stays
            continue
        gone = _same_name(name, removed)
        if gone is not None and removed[gone] == commit_id:
            continue  # removed here; the next push removes it there
        if repo.has_commit(commit_id):
            mine[name] = commit_id
            added += 1
    if added:
        repo._save_names(mine, removed)
    return added, clashes


def normalize(spec: str) -> str:
    """Folder remotes are saved as absolute paths, so they work from any directory."""
    return spec if spec.startswith("s3://") else str(Path(spec).expanduser().resolve())


def is_shared_kit(folder: Path) -> bool:
    """A folder Kit has pushed to: it has a HEAD file and a commits folder."""
    return (folder / "HEAD").is_file() and (folder / "commits").is_dir()


def _empty(folder: Path) -> bool:
    """Missing, or holding nothing but hidden files such as .DS_Store."""
    return not folder.exists() or not any(not item.name.startswith(".") for item in folder.iterdir())


def place_to_share(spec: str, repo: Repo) -> str:
    """Where to share `repo` when the user picks the folder `spec`.

    People pick the folder they share things in (their Google Drive, a "kits"
    folder) rather than a new empty one. Kit's own files (objects, commits,
    HEAD) would then land among their other files, so the kit gets a folder of
    its own inside: picking "Google Drive" shares "Dark Trap Kit" as
    "Google Drive/Dark Trap Kit". An empty folder, or one that is already a
    shared kit, is used as it is.
    """
    if spec.startswith("s3://"):
        return spec
    chosen = Path(normalize(spec))
    target = chosen if is_shared_kit(chosen) or _empty(chosen) else chosen / repo.folder.name
    if target == repo.folder or repo.folder in target.parents:
        raise KitError("the shared folder cannot be the kit itself or inside it; choose a folder outside the kit")
    if not (is_shared_kit(target) or _empty(target)):
        raise KitError(f"{target} already exists and is not a shared kit; choose another folder")
    return str(target)


def find_shared(spec: str) -> str:
    """The shared kit at `spec`, or the only one directly inside it.

    Someone getting a kit often picks the folder around it ("Google Drive")
    rather than the kit's own folder, so one level down is searched too.
    """
    if spec.startswith("s3://"):
        return spec
    chosen = Path(normalize(spec))
    if is_shared_kit(chosen) or not chosen.is_dir():
        return str(chosen)
    found = sorted(item for item in chosen.iterdir() if item.is_dir() and is_shared_kit(item))
    if len(found) == 1:
        return str(found[0])
    if found:
        names = ", ".join(item.name for item in found)
        raise KitError(f"{chosen} holds several shared kits ({names}); choose one of them")
    return str(chosen)  # nothing here; clone or pull will say so


def open_remote(spec: str) -> Remote:
    """Turn what the user typed into a remote: `s3://bucket/prefix` or a folder path."""
    if spec.startswith("s3://"):
        from .s3remote import S3Remote  # imported late: boto3 is optional

        bucket, _, prefix = spec[len("s3://"):].partition("/")
        if not bucket:
            raise KitError("an S3 remote looks like s3://bucket-name/optional/folder")
        return S3Remote(bucket, prefix)
    return FolderRemote(Path(spec).expanduser())


@dataclass(frozen=True)
class SyncResult:
    commits: int  # versions sent or received
    objects: int  # objects sent or received (audio files AND manifests)
    outcome: str = ""  # pull only: "up-to-date", "fast-forward" or "merge"
    changes: list[Change] = field(default_factory=list)  # pull only: what changed in the folder
    conflicts: list[str] = field(default_factory=list)  # pull only: paths where both versions were kept
    names: int = 0  # version names sent (push) or received (pull)
    name_clashes: list[str] = field(default_factory=list)  # names that mean another version on the other side


def compare(repo: Repo, remote: Remote) -> dict:
    """How far apart the kit and the remote are, without changing either.

    {"ahead": versions here the remote lacks (to push),
     "behind": versions on the remote this kit lacks (to pull),
     "from": who saved those}. Only small version files are read, never audio.
    """
    ahead = len(lineage(repo.head(), repo.load_commit, have=remote.has_commit))
    remote_head = remote.read_head()
    incoming = lineage(remote_head, lambda commit_id: parse_commit(remote.read_commit(commit_id)),
                       have=repo.has_commit) if remote_head else []
    return {"ahead": ahead, "behind": len(incoming), "from": sorted({c.author for c in incoming})}


def push(repo: Repo, remote: Remote) -> SyncResult:
    """Send the versions the remote is missing, uploading only audio it lacks."""
    head = repo.head()
    if head is None:
        raise KitError("nothing to push yet; save a version with `kit commit` first")

    # The remote's latest version must be part of our history. If it is not,
    # someone else pushed versions we have never seen, and moving the remote's
    # HEAD to ours would cut their work out of the history.
    remote_head = remote.read_head()
    if remote_head is not None and remote_head not in repo.ancestors(head):
        raise KitError(
            "the remote has versions this repo does not; run `kit pull` first, then push again"
        )

    # A remote that has a commit also has everything before it (see the upload
    # order below), so the walk can stop at the first commit it already holds.
    missing = lineage(head, repo.load_commit, have=remote.has_commit)
    objects = 0
    for commit in missing:  # parents before children
        manifest = repo.manifest_of(commit.id)
        needed = [commit.manifest, *(entry["hash"] for entry in manifest.values())]
        for digest in dict.fromkeys(needed):  # each hash once, order kept
            if not remote.has_object(digest):
                packed = repo.store.packed_bytes(digest)
                if packed is not None:
                    remote.upload_packed(digest, packed)  # half the upload, and the same file
                else:
                    remote.upload_object(digest, repo.store.file_for(digest))
                objects += 1
        # A commit is written only after everything it points to. If the
        # upload dies halfway, the remote holds some extra objects but never a
        # version with pieces missing.
        remote.write_commit(commit.id, repo.read_commit_bytes(commit.id))

    # HEAD moves last, for the same reason. Note that a folder remote cannot
    # lock: if two people push in the same second, the later write wins. A
    # hosted service would make this step a compare-and-swap.
    if remote_head != head:
        remote.write_head(head)
    names, clashes = share_names(repo, remote)  # after HEAD: every version they name is there by now
    return SyncResult(len(missing), objects, names=names, name_clashes=clashes)


def pull(repo: Repo, remote: Remote) -> SyncResult:
    """Fetch versions from the remote and bring the kit folder up to date.

    If both sides have saved new versions since they last synced, the two
    histories are merged (see Repo.merge).
    """
    remote_head = remote.read_head()
    if remote_head is None:
        return SyncResult(0, 0, "up-to-date")
    if repo.status() and remote_head not in repo.ancestors(repo.head()):
        raise KitError("you have unsaved changes; run `kit commit` first, then pull again")

    raw: dict[str, bytes] = {}

    def load_remote(commit_id: str) -> Commit:
        raw[commit_id] = remote.read_commit(commit_id)
        commit = parse_commit(raw[commit_id])
        if commit.id != commit_id:
            raise KitError(f"the remote's version {commit_id[:8]} is damaged")
        return commit

    incoming = lineage(remote_head, load_remote, have=repo.has_commit)
    objects = 0
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "download"

        def fetch(digest: str) -> None:
            nonlocal objects
            if repo.store.has(digest):
                return
            packed = remote.download_packed(digest)
            if packed is not None:
                try:
                    repo.store.put_packed(digest, packed)  # checked: it must unpack to its hash
                except ValueError as err:
                    raise KitError(f"a downloaded file did not match its hash ({digest[:8]}): {err}") from None
                objects += 1
                return
            remote.download_object(digest, scratch)
            # put_file hashes what arrived, so a corrupted or tampered
            # download is caught here instead of ending up in the kit.
            if repo.store.put_file(scratch) != digest:
                raise KitError(f"a downloaded file did not match its hash ({digest[:8]})")
            objects += 1

        for commit in incoming:  # parents before children
            fetch(commit.manifest)
            for entry in loads(repo.store.read_bytes(commit.manifest)).values():
                fetch(entry["hash"])
            repo.store_commit_bytes(raw[commit.id])

    result = repo.merge(remote_head)
    names, clashes = take_names(repo, remote)
    return SyncResult(len(incoming), objects, result.kind, result.changes, result.conflicts, names, clashes)


def clone(spec: str, folder, author: str | None = None) -> tuple[Repo, SyncResult]:
    """Make a new kit folder from a remote. This is also how a pack is forked."""
    spec = find_shared(spec)
    remote = open_remote(spec)
    if remote.read_head() is None:
        raise KitError(f"there is nothing to clone at {spec}")
    folder = Path(folder)
    if folder.exists() and any(folder.iterdir()):
        raise KitError(f"{folder} already exists and is not empty")
    folder.mkdir(parents=True, exist_ok=True)
    repo = Repo.init(folder, author=author)
    repo.set_remote(normalize(spec))
    return repo, pull(repo, remote)
