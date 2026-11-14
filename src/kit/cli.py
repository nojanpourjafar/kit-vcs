"""The `kit` command. Each cmd_* function is one subcommand.

This file only reads arguments and prints. All real work happens in repo.py,
which keeps the logic easy to test and reusable by a future web app.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, remote
from .audio import describe
from .repo import KitError, Repo

SYMBOL = {"added": "+", "removed": "-", "modified": "~", "renamed": ">"}


def _print_changes(changes, locate=None) -> None:
    """Print one line per change. `locate(path)` finds the file for audio details."""
    for change in changes:
        line = f"  {SYMBOL[change.kind]} {change.path}"
        if change.kind == "renamed":
            line += f"  (was {change.old_path})"
        if locate and change.kind in ("added", "modified"):
            info = describe(locate(change.path), change.path)
            if info:
                line += f"  ({info})"
        print(line)


def _summary(changes) -> str:
    counts = {}
    for change in changes:
        counts[change.kind] = counts.get(change.kind, 0) + 1
    return ", ".join(f"{n} {kind}" for kind, n in counts.items())


def _size(n: int) -> str:
    for unit in ("bytes", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def cmd_init(args) -> None:
    repo = Repo.init(args.folder, author=args.author)
    print(f"Started a Kit repo in {repo.folder} (author: {repo.author})")
    print('Save your first version with: kit commit -m "first version"')


def cmd_status(args) -> None:
    repo = Repo.find()
    changes = repo.status()
    history = repo.log()
    if not changes:
        print(f"Nothing changed since v{len(history)}." if history
              else "This repo is empty. Add some sounds, then run kit commit.")
        return
    print(f"Changes since v{len(history)}:" if history else "Not saved yet:")
    _print_changes(changes, locate=lambda path: repo.folder / path)
    print('Save them with: kit commit -m "what changed"')


def cmd_commit(args) -> None:
    repo = Repo.find()
    saved = repo.commit(args.message, author=args.author)
    number = len(repo.log())
    print(f"Saved v{number} ({saved.commit.id[:8]}): {saved.commit.message}")
    _print_changes(saved.changes, locate=lambda path: repo.folder / path)
    print(f"{_summary(saved.changes)}. Stored {saved.new_files} new file(s), "
          f"{_size(saved.new_bytes)}.")


def cmd_log(args) -> None:
    repo = Repo.find()
    history = repo.log()
    if not history:
        print("No versions saved yet.")
        return
    for index, commit in enumerate(history):
        when = commit.time[:16].replace("T", " ")
        print(f"v{len(history) - index:<3} {commit.id[:8]}  {when} UTC  "
              f"{commit.author}: {commit.message}")


def cmd_diff(args) -> None:
    repo = Repo.find()
    changes = repo.diff_versions(args.old, args.new)
    if not changes:
        print("Those two versions hold the same files.")
        return
    newer = repo.manifest_of(repo.resolve(args.new))
    _print_changes(changes, locate=lambda path: repo.store.path_for(newer[path]["hash"]))
    print(_summary(changes) + ".")


def cmd_restore(args) -> None:
    repo = Repo.find()
    changes = repo.restore(args.version, force=args.force)
    if not changes:
        print("The folder already matches that version.")
        return
    print(f"Put the folder back to {args.version}:")
    _print_changes(changes)
    print("History is untouched. Commit to keep this as a new version.")


def cmd_credits(args) -> None:
    repo = Repo.find()
    credits = repo.credits()
    if not credits:
        print("No versions saved yet.")
        return
    order = [commit.id for commit in reversed(repo.log())]
    width = max(len(path) for path in credits)
    for path, commit in credits.items():
        print(f"  {path:<{width}}  {commit.author} (v{order.index(commit.id) + 1})")


def cmd_push(args) -> None:
    result = remote.push(Repo.find(), remote.FolderRemote(args.remote))
    print(f"Pushed {result.commits} version(s), {result.objects} object(s).")


def cmd_pull(args) -> None:
    result = remote.pull(Repo.find(), remote.FolderRemote(args.remote))
    print(f"Pulled {result.commits} version(s), {result.objects} object(s).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kit", description="Version control for sample packs and loop kits.")
    parser.add_argument("--version", action="version", version=f"kit {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="start tracking a kit folder")
    p.add_argument("folder", nargs="?", default=".")
    p.add_argument("--author", help="your producer name, used for credits")
    p.set_defaults(run=cmd_init)

    p = sub.add_parser("status", help="show what changed since the last version")
    p.set_defaults(run=cmd_status)

    p = sub.add_parser("commit", help="save the folder as a new version")
    p.add_argument("-m", "--message", required=True)
    p.add_argument("--author", help="credit this version to someone else")
    p.set_defaults(run=cmd_commit)

    p = sub.add_parser("log", help="list every saved version")
    p.set_defaults(run=cmd_log)

    p = sub.add_parser("diff", help="show what changed between two versions")
    p.add_argument("old")
    p.add_argument("new")
    p.set_defaults(run=cmd_diff)

    p = sub.add_parser("restore", help="put the folder back to an earlier version")
    p.add_argument("version", help="v1, v2, ..., latest, or the start of an ID")
    p.add_argument("--force", action="store_true", help="discard unsaved changes")
    p.set_defaults(run=cmd_restore)

    p = sub.add_parser("credits", help="show who first added each sound")
    p.set_defaults(run=cmd_credits)

    p = sub.add_parser("push", help="send new versions to a shared folder")
    p.add_argument("remote", type=Path)
    p.set_defaults(run=cmd_push)

    p = sub.add_parser("pull", help="get new versions from a shared folder")
    p.add_argument("remote", type=Path)
    p.set_defaults(run=cmd_pull)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.run(args)
    except KitError as err:
        print(f"kit: {err}", file=sys.stderr)
        return 1
    except NotImplementedError as err:
        print(f"kit: {err}", file=sys.stderr)
        return 2
    return 0
