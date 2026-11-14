"""The object store: every file is saved once, under the name of its own hash.

    objects/ab/cdef0123...   <- the file whose SHA-256 starts with "abcdef0123"

This is called content-addressed storage, and it is the same trick Git uses.
What it buys us:
  * the same kick in ten versions of a kit takes the space of one kick;
  * renaming or moving a sample stores nothing new;
  * anyone can check a file is intact by hashing it again.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from .hashing import CHUNK, hash_bytes, hash_file, is_digest


class ObjectStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def path_for(self, digest: str) -> Path:
        # The check matters once hashes arrive from other people (push/pull):
        # without it, a "hash" like "../../x" could point outside the store.
        if not is_digest(digest):
            raise ValueError(f"not a SHA-256 hash: {digest!r}")
        return self.root / digest[:2] / digest[2:]

    def has(self, digest: str) -> bool:
        return self.path_for(digest).is_file()

    def put_file(self, src: Path) -> str:
        """Save a file and return its hash. Does nothing if we already have it."""
        digest = hash_file(src)
        if self.has(digest):
            return digest
        dest = self.path_for(digest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Copy to a temporary name, then rename. A rename is all-or-nothing, so
        # a crash mid-copy can never leave half a file under a real hash.
        tmp = dest.parent / f".tmp-{uuid4().hex}"
        hasher = hashlib.sha256()
        try:
            with open(src, "rb") as fin, open(tmp, "wb") as fout:
                while chunk := fin.read(CHUNK):
                    hasher.update(chunk)
                    fout.write(chunk)
            if hasher.hexdigest() != digest:
                raise OSError(f"{src} changed while it was being saved; try again")
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)
        return digest

    def put_bytes(self, data: bytes) -> str:
        digest = hash_bytes(data)
        if not self.has(digest):
            dest = self.path_for(digest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.parent / f".tmp-{uuid4().hex}"
            try:
                tmp.write_bytes(data)
                os.replace(tmp, dest)
            finally:
                tmp.unlink(missing_ok=True)
        return digest

    def read_bytes(self, digest: str) -> bytes:
        return self.path_for(digest).read_bytes()

    def copy_to(self, digest: str, dest: Path) -> None:
        """Write a stored file back out to `dest` (used when restoring a version)."""
        src = self.path_for(digest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".tmp-{uuid4().hex}")
        try:
            shutil.copyfile(src, tmp)
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)

    def all_hashes(self) -> Iterator[str]:
        if not self.root.is_dir():
            return
        for folder in sorted(self.root.iterdir()):
            if folder.is_dir():
                for item in sorted(folder.iterdir()):
                    if is_digest(folder.name + item.name):
                        yield folder.name + item.name
