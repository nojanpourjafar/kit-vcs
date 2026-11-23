"""The object store: every file is saved once, under the name of its own hash.

    objects/ab/cdef0123...   <- the file whose SHA-256 starts with "abcdef0123"

This is called content-addressed storage, and it is the same trick Git uses.
What it buys us:
  * the same kick in ten versions of a kit takes the space of one kick;
  * renaming or moving a sample stores nothing new;
  * anyone can check a file is intact by hashing it again.

WAV files can also be stored compressed, as FLAC (`kit compact`):

    objects/ab/cdef0123....kz   <- the same file, packed (see pack.py)

A packed file is still named by the hash of the original, and reading it
gives back the original bytes. Everything else in Kit asks the store for a
file by hash and does not need to know which way it is kept.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import struct
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from . import pack
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

    def packed_path(self, digest: str) -> Path:
        path = self.path_for(digest)
        return path.with_name(path.name + ".kz")

    def has(self, digest: str) -> bool:
        return self.path_for(digest).is_file() or self.packed_path(digest).is_file()

    def file_for(self, digest: str) -> Path:
        """A path to the stored file as it was saved, for anything that reads files.

        A packed file is unpacked once into .kit/cache and read from there.
        """
        raw = self.path_for(digest)
        if raw.is_file():
            return raw
        cached = self.root.parent / "cache" / digest
        if not cached.is_file():
            _write_atomically(cached, self.read_bytes(digest))
        return cached

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
        raw = self.path_for(digest)
        try:
            return raw.read_bytes()
        except FileNotFoundError:
            pass  # packed, perhaps just now by `compact` running in the background
        try:
            return pack.unpack(self.packed_path(digest).read_bytes())
        except (ValueError, IndexError, struct.error) as err:  # whatever a damaged FLAC stream trips over
            raise OSError(f"stored file {digest[:8]} is damaged: {err}") from err

    def intact(self, digest: str) -> bool:
        """Whether the stored file still hashes to its name (`kit verify`)."""
        try:
            raw = self.path_for(digest)
            return (hash_file(raw) if raw.is_file() else hash_bytes(self.read_bytes(digest))) == digest
        except (OSError, ValueError):
            return False

    def copy_to(self, digest: str, dest: Path) -> None:
        """Write a stored file back out to `dest` (used when restoring a version)."""
        src = self.path_for(digest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".tmp-{uuid4().hex}")
        try:
            try:
                shutil.copyfile(src, tmp)
            except FileNotFoundError:  # stored packed: unpack it instead
                tmp.write_bytes(self.read_bytes(digest))
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)

    def all_hashes(self) -> Iterator[str]:
        if not self.root.is_dir():
            return
        for folder in sorted(self.root.iterdir()):
            if folder.is_dir():
                names = {item.name.removesuffix(".kz") for item in folder.iterdir()}
                for name in sorted(names):
                    if is_digest(folder.name + name):
                        yield folder.name + name

    # ---- compression -------------------------------------------------------

    def compact(self, min_size: int = 64 * 1024, progress=None, lock=None) -> tuple[int, int, int]:
        """Store WAV files as FLAC where that saves space. Returns (files, bytes before, bytes after).

        A file is packed only if unpacking gives back exactly its hash, and
        the original is removed only after the packed copy is safely written,
        so stopping halfway (or a crash) loses nothing. Files under `min_size`
        are left alone: the saving is too small to be worth unpacking them.
        `progress(done, total)` is called as it goes.

        `lock`, if given, is held only while a packed copy replaces an original,
        not while encoding (seconds per file), so the app can keep working while
        this runs in the background.
        """
        candidates = [d for d in self.all_hashes() if self.path_for(d).is_file()
                      and self.path_for(d).stat().st_size >= min_size and _looks_like_wav(self.path_for(d))]
        files = before = after = 0
        for done, digest in enumerate(candidates, 1):
            raw = self.path_for(digest)
            try:
                data = raw.read_bytes()
            except FileNotFoundError:
                continue  # packed by another run meanwhile
            packed = pack.pack(data)
            if packed is not None and len(packed) < 0.9 * len(data) and hash_bytes(pack.unpack(packed)) == digest:
                with lock or contextlib.nullcontext():
                    _write_atomically(self.packed_path(digest), packed)
                    try:
                        raw.unlink()
                    except OSError:
                        pass  # open in another program (Windows): both copies stay; the next run removes it
                files, before, after = files + 1, before + len(data), after + len(packed)
            if progress:
                progress(done, len(candidates))
        shutil.rmtree(self.root.parent / "cache", ignore_errors=True)  # unpacked copies, made again when needed
        return files, before, after


def _looks_like_wav(path: Path) -> bool:
    with path.open("rb") as f:
        head = f.read(12)
    return head[:4] == b"RIFF" and head[8:12] == b"WAVE"


def _write_atomically(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".tmp-{uuid4().hex}")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
