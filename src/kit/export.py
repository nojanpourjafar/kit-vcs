"""Export a version as a plain zip, for people who do not use Kit.

The zip holds one folder, named like "Dark Trap Kit v3", with that version's
sounds inside, so unzipping gives a tidy folder rather than files everywhere.

The same version always exports to exactly the same bytes: the entries are in
a fixed order and carry the version's own date. Anyone can check that a zip is
the real v3 by exporting v3 themselves and comparing.
"""

from __future__ import annotations

import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from .repo import KitError, Repo

def label(repo: Repo, commit_id: str) -> str:
    """ "v3" for a version on the main line, the start of its ID otherwise."""
    history = repo.log()
    for i, commit in enumerate(history):
        if commit.id == commit_id:
            return f"v{len(history) - i}"
    return commit_id[:8]


def export(repo: Repo, ref: str, folder: Path) -> tuple[Path, int, int]:
    """Write `ref` as "<kit> <version>.zip" into `folder`. Returns (zip path, sounds, bytes)."""
    commit_id = repo.resolve(ref)
    commit = repo.load_commit(commit_id)
    manifest = repo.manifest_of(commit_id)
    if not manifest:
        raise KitError("that version has no files to export")
    folder = Path(folder).expanduser().resolve()
    if folder == repo.folder or repo.folder in folder.parents:
        # Inside the kit, the zip would show up as a new sound in the next version.
        raise KitError("the zip cannot go inside the kit folder; choose a folder outside it")
    folder.mkdir(parents=True, exist_ok=True)

    name = f"{repo.folder.name} {label(repo, commit_id)}"
    out = folder / f"{name}.zip"
    when = datetime.fromisoformat(commit.time.replace("Z", "+00:00"))
    stamp = (max(when.year, 1980),) + tuple(when.timetuple())[1:6]  # zip dates start in 1980
    partial = out.with_name(out.name + ".part")  # written under another name, then renamed
    with zipfile.ZipFile(partial, "w") as archive:
        for path in sorted(manifest):
            entry = manifest[path]
            info = zipfile.ZipInfo(f"{name}/{path}", date_time=stamp)
            info.create_system = 3  # the same bytes whichever system exports it
            info.external_attr = 0o644 << 16  # an ordinary readable file
            # Stored, not compressed: audio barely shrinks anyway, and compressed bytes
            # depend on the compression library's version, which would break the
            # "same version, same bytes" promise between computers.
            info.compress_type = zipfile.ZIP_STORED
            with repo.store.path_for(entry["hash"]).open("rb") as source, \
                    archive.open(info, "w", force_zip64=entry["size"] >= 2**31) as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
    os.replace(partial, out)
    return out, len(manifest), sum(entry["size"] for entry in manifest.values())
