"""Sharing a repo: remotes, push and pull.

BUILT: `FolderRemote`, a remote that is just another folder. Point it at a USB
drive or a Google Drive / Dropbox synced folder and two producers can share a
repo with no server at all. A cloud bucket (S3, Cloudflare R2) comes later as
a second class with the same eight methods.

YOURS TO WRITE: `push` and `pull`, at the bottom. They are the heart of the
project and the part interviewers will ask about. The tests in
tests/test_remote.py describe exactly what they must do.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .hashing import is_digest
from .objects import ObjectStore
from .repo import Repo


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


@dataclass(frozen=True)
class SyncResult:
    commits: int  # versions sent or received
    objects: int  # objects sent or received (audio files AND manifests)


# ---------------------------------------------------------------------------
# Yours to write. Each needs roughly 25 lines. Everything you need already
# exists on `repo` (see repo.py) and `remote` (the eight methods above).
# ---------------------------------------------------------------------------


def push(repo: Repo, remote: Remote) -> SyncResult:
    """Send the versions the remote is missing, uploading only audio it lacks.

    Work through these questions in order. Each one is a step of the function.

    1. Which of my versions does the remote lack?
       The remote's HEAD is its latest version. If you walk your own history
       backwards from your HEAD (commit.parent), where do you stop?

    2. What if you reach your very first version and never met the remote's
       HEAD? What does that say about who has saved what, and why would
       carrying on destroy someone's work? (Raise KitError and tell the
       user to pull first. Merging two histories is version 3.)

    3. For one version, what must exist on the remote before that version is
       usable there? (Look at what a Commit points to, and what that points to.)
       Which of those things can you skip uploading, and how do you know?

    4. In what order do you upload versions, oldest first or newest first?
       And why must `remote.write_head` be the very last call? Picture your
       laptop dying halfway through, then a friend pulling.

    Useful pieces: repo.head(), repo.load_commit(id), repo.manifest_of(id),
    repo.read_commit_bytes(id), repo.store.path_for(hash).

    Return SyncResult(commits=..., objects=...). If there was nothing to send,
    return SyncResult(0, 0).
    """
    raise NotImplementedError("push is not built yet: it is yours to write, in kit/remote.py")


def pull(repo: Repo, remote: Remote) -> SyncResult:
    """Fetch versions from the remote and bring the kit folder up to date.

    1. What should happen if the folder has unsaved changes? (repo.status())

    2. Which remote versions do I lack? Walk the REMOTE's history backwards
       from remote.read_head(), using remote.read_commit and
       commits.parse_commit, until you meet your own HEAD.
       Two special cases to think through:
         - your HEAD is None (a brand-new repo);
         - you never meet your own HEAD. Either you are already ahead of the
           remote (nothing to do) or the histories split (raise KitError).
           How can you tell those two apart?

    3. For each new version, oldest first: get its manifest and any audio you
       lack into repo.store, then save the commit with repo.store_commit_bytes.
       Hint: remote.download_object writes to a path. A file inside
       tempfile.TemporaryDirectory() plus repo.store.put_file gets it stored
       and re-checks its hash for free. Why is that re-check worth having?

    4. Update the files, then move HEAD: repo.restore(new_head_id), then
       repo.set_head(new_head_id). Read restore() and work out why that order
       is right and the reverse would leave stale files behind.

    Return SyncResult(commits=..., objects=...).
    """
    raise NotImplementedError("pull is not built yet: it is yours to write, in kit/remote.py")
