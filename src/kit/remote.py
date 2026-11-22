"""Sharing a repo: remotes, push, pull and clone.

A remote is anywhere a copy of the history can live. Two kinds exist:

  * `FolderRemote` is just another folder. Point it at a USB drive or a
    Google Drive / Dropbox synced folder and two producers can share a kit
    with no server at all.
  * `S3Remote` (in s3remote.py) is a bucket on Amazon S3, Cloudflare R2 or
    anything else that speaks the S3 API.

Both offer the same eight methods, listed in `Remote`, so push and pull are
written once and work with either.
"""

from __future__ import annotations

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
    """The eight things any remote must be able to do."""

    def has_object(self, digest: str) -> bool: ...
    def upload_object(self, digest: str, src: Path) -> None: ...
    def download_object(self, digest: str, dest: Path) -> None: ...
    def has_commit(self, commit_id: str) -> bool: ...
    def read_commit(self, commit_id: str) -> bytes: ...
    def write_commit(self, commit_id: str, data: bytes) -> None: ...
    def read_head(self) -> str | None: ...
    def write_head(self, commit_id: str) -> None: ...


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

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)


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
                remote.upload_object(digest, repo.store.path_for(digest))
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
    return SyncResult(len(missing), objects)


def pull(repo: Repo, remote: Remote) -> SyncResult:
    """Fetch versions from the remote and bring the kit folder up to date.

    If both sides have saved new versions since they last synced, the two
    histories are merged (see Repo.merge).
    """
    remote_head = remote.read_head()
    if remote_head is None:
        return SyncResult(0, 0, "up-to-date")
    if repo.status():
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
    return SyncResult(len(incoming), objects, result.kind, result.changes, result.conflicts)


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
