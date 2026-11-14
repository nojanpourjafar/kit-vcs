"""SHA-256 helpers.

A file's hash is its identity everywhere in this project. Two files with the
same audio have the same hash, whatever they are called and wherever they sit.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

CHUNK = 1 << 20  # read 1 MiB at a time, so a 500 MB stem never sits in memory


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            hasher.update(chunk)
    return hasher.hexdigest()


def is_digest(text: str) -> bool:
    """True for a full SHA-256 hex string. Checked before a hash is used as a file name."""
    return len(text) == 64 and all(c in "0123456789abcdef" for c in text)
