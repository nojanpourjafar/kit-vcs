"""Commits: one saved version of a kit.

A commit is a few lines of JSON: which manifest it points to, who saved it,
their message, the time, and the ID of the version before it (its parent).
Following parents backwards gives the kit's whole history.

A merge is a commit with two parents: `parent` is the local version it was
made on top of, and `merged` is the version that was brought in.

A commit's ID is the hash of its own JSON. So if anyone edits an old version,
its ID no longer matches and we can tell. History is tamper-evident.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .hashing import hash_bytes


@dataclass(frozen=True)
class Commit:
    id: str
    parent: str | None  # None for the first version
    manifest: str  # hash of the manifest object
    author: str
    message: str
    time: str  # ISO 8601, UTC
    merged: str | None = None  # second parent; set only on a merge

    @property
    def parents(self) -> list[str]:
        return [p for p in (self.parent, self.merged) if p]

    def to_bytes(self) -> bytes:
        body = {
            "parent": self.parent,
            "manifest": self.manifest,
            "author": self.author,
            "message": self.message,
            "time": self.time,
        }
        if self.merged:
            # Written only for merges, so ordinary versions keep the exact
            # bytes, and therefore the IDs, they have always had.
            body["merged"] = self.merged
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


def new_commit(parent: str | None, manifest: str, author: str, message: str,
               time: str | None = None, merged: str | None = None) -> Commit:
    time = time or datetime.now(timezone.utc).isoformat(timespec="seconds")
    draft = Commit("", parent, manifest, author, message, time, merged)
    return Commit(hash_bytes(draft.to_bytes()), parent, manifest, author, message, time, merged)


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
        merged=body.get("merged"),
    )


def lineage(tip: str | None, load: Callable[[str], Commit],
            have: Callable[[str], bool] = lambda commit_id: False) -> list[Commit]:
    """Every commit reachable from `tip`, with parents always before children.

    `load` fetches a commit by ID (from this repo or from a remote). `have`
    marks commits to stop at: sync uses it to skip everything the other side
    already holds. Along a merge, the first-parent line comes out first.
    """
    order: list[Commit] = []
    loaded: dict[str, Commit] = {}
    seen: set[str] = set()
    stack: list[tuple[str | None, bool]] = [(tip, False)]
    while stack:
        commit_id, parents_done = stack.pop()
        if parents_done:
            order.append(loaded[commit_id])
            continue
        if commit_id is None or commit_id in seen or have(commit_id):
            continue
        seen.add(commit_id)
        loaded[commit_id] = commit = load(commit_id)
        stack.append((commit_id, True))
        for parent in reversed(commit.parents):
            stack.append((parent, False))
    return order
