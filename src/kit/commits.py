"""Commits: one saved version of a kit.

A commit is a few lines of JSON: which manifest it points to, who saved it,
their message, the time, and the ID of the version before it (its parent).
Following parents backwards gives the kit's whole history.

A commit's ID is the hash of its own JSON. So if anyone edits an old version,
its ID no longer matches and we can tell. History is tamper-evident.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from .hashing import hash_bytes


@dataclass(frozen=True)
class Commit:
    id: str
    parent: str | None  # None for the first version
    manifest: str  # hash of the manifest object
    author: str
    message: str
    time: str  # ISO 8601, UTC

    def to_bytes(self) -> bytes:
        body = {
            "parent": self.parent,
            "manifest": self.manifest,
            "author": self.author,
            "message": self.message,
            "time": self.time,
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def new_commit(parent: str | None, manifest: str, author: str, message: str,
               time: str | None = None) -> Commit:
    time = time or datetime.now(timezone.utc).isoformat(timespec="seconds")
    draft = Commit("", parent, manifest, author, message, time)
    return Commit(hash_bytes(draft.to_bytes()), parent, manifest, author, message, time)


def parse_commit(data: bytes) -> Commit:
    """Rebuild a commit from its stored bytes. The ID is recomputed, never trusted."""
    body = json.loads(data)
    return Commit(
        id=hash_bytes(data),
        parent=body["parent"],
        manifest=body["manifest"],
        author=body["author"],
        message=body["message"],
        time=body["time"],
    )
