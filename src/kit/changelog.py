"""A changelog: what each version added, renamed, changed and removed, in plain words.

For the people a pack goes to, who do not use Kit: it goes into every exported
zip as "Changes.txt", and the app and `kit changelog` write it on its own.
Newest version first, the way changelogs are read:

    Dark Trap Kit: changes
    ======================

    v3 (1.1) · 27 Nov 2026 · Lucas
    "tighter hats"
      Added
        Hats/hat_tight.wav
      Renamed
        Kicks/kick_punch.wav -> Kicks/kick_01.wav

The text depends only on the versions, so the same version always gives the
same changelog (and the zip keeps its "same version, same bytes" promise).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .manifest import diff
from .repo import KitError, Repo

SECTIONS = (("added", "Added"), ("renamed", "Renamed"), ("modified", "Changed"), ("removed", "Removed"))


def _date(time: str) -> str:
    when = datetime.fromisoformat(time.replace("Z", "+00:00"))
    return f"{when.day} {when:%b %Y}"


def _labels(repo: Repo) -> dict[str, str]:
    """{commit id: "v3"} along the main line, with a version's name added: "v3 (1.1)"."""
    history = repo.log()
    labels = {}
    for i, commit in enumerate(history):
        label = f"v{len(history) - i}"
        names = repo.names_of(commit.id)
        labels[commit.id] = f"{label} ({', '.join(names)})" if names else label
    return labels


def changelog(repo: Repo, ref: str = "latest", since: str | None = None) -> str:
    """The changelog of the versions up to `ref`, newest first; after `since` only, if given."""
    head = repo.resolve(ref)
    stop = repo.resolve(since) if since else None
    labels = _labels(repo)
    entries = []
    commit_id = head
    while commit_id and commit_id != stop:
        commit = repo.load_commit(commit_id)
        lines = [f"{labels.get(commit.id, commit.id[:8])} · {_date(commit.time)} · {commit.author}"]
        if commit.message:
            lines.append(f'"{commit.message}"')
        if commit.merged:
            lines.append("  (brings in versions saved by someone else)")
        new = repo.manifest_of(commit.id)
        if commit.parent is None:
            lines.append(f"  First version: {len(new)} sound{'s' if len(new) != 1 else ''}")
        else:
            changes = diff(repo.manifest_of(commit.parent), new)
            for kind, title in SECTIONS:
                found = [c for c in changes if c.kind == kind]
                if found:
                    lines.append(f"  {title}")
                    lines += [f"    {c.old_path} -> {c.path}" if kind == "renamed" else f"    {c.path}" for c in found]
            if not changes:
                lines.append("  No sounds changed")
        entries.append("\n".join(lines))
        commit_id = commit.parent
    if commit_id != stop:
        raise KitError(f"{since} is not an earlier version of {ref}")
    if not entries:
        raise KitError(f"nothing changed after {since}")
    title = f"{repo.folder.name}: changes"
    return f"{title}\n{'=' * len(title)}\n\n" + "\n\n".join(entries) + "\n"


def write_changelog(repo: Repo, ref: str, folder: Path, since: str | None = None) -> Path:
    """Write the changelog as "<kit> <version> changes.txt" into `folder` (never inside the kit)."""
    from .export import label

    folder = Path(folder).expanduser().resolve()
    if folder == repo.folder or repo.folder in folder.parents:
        raise KitError("the changelog cannot go inside the kit folder; choose a folder outside it")
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{repo.folder.name} {label(repo, repo.resolve(ref))} changes.txt".replace("/", "-")
    out = folder / name
    out.write_text(changelog(repo, ref, since), encoding="utf-8")
    return out
