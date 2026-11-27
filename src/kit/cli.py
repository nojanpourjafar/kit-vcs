"""The `kit` command. Each cmd_* function is one subcommand.

This file only reads arguments and prints. All real work happens in repo.py,
remote.py and report.py, which keeps the logic easy to test and reusable.
"""

from __future__ import annotations

import argparse
import sys
import time
import webbrowser
from pathlib import Path

from . import __version__, names, remote
from .audio import describe
from .report import build_report
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


def _size(n: float) -> str:
    for unit in ("bytes", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _labels(repo: Repo) -> dict[str, str]:
    """Commit ID -> "v1", "v2", ... for the versions on the main line."""
    history = repo.log()
    return {commit.id: f"v{len(history) - i}" for i, commit in enumerate(history)}


def _remote_for(repo: Repo, spec: str | None, sending: bool):
    """Open the remote named on the command line, or the saved default.

    A folder named on the command line is checked first: for sending, the kit
    gets its own folder inside a folder that holds other things; for receiving,
    the shared kit inside the named folder is found.
    """
    if spec:
        spec = remote.place_to_share(spec, repo) if sending else remote.find_shared(spec)
    spec = spec or repo.remote()
    if not spec:
        raise KitError("no remote given; use `kit push <folder or s3://bucket>` "
                       "or save one with `kit remote <folder or s3://bucket>`")
    spec = remote.normalize(spec)
    if repo.remote() is None:
        repo.set_remote(spec)  # the first remote used becomes the default
    return remote.open_remote(spec)


def cmd_init(args) -> None:
    repo = Repo.init(args.folder, author=args.author)
    print(f"Started a Kit repo in {repo.folder} (author: {repo.author})")
    print('Save your first version with: kit commit -m "first version"')


def _print_name_warnings(paths) -> None:
    lines = names.warnings(paths)
    if lines:
        print("These names will not work for someone on Windows (rename them before sharing):")
        for line in lines:
            print(f"  ! {line}")


def _print_conflicts(repo) -> None:
    conflicts = repo.conflicts()
    if conflicts:
        print("Changed on both sides; listen to both, then choose:")
        for c in conflicts:
            print(f"  ! {c['path']}  (theirs: {c['theirs']}, from {c['author']})")
        print("Choose with: kit resolve <path> --keep mine|theirs|both")


def _print_sync(repo) -> None:
    spec = repo.remote()
    if not spec or spec.startswith("s3://"):
        return  # checking a bucket means network requests; `kit pull` will tell
    try:
        sync = remote.compare(repo, remote.open_remote(spec))
    except (OSError, ValueError, KitError):
        print(f"Shared folder {spec} cannot be read right now.")
        return
    if sync["behind"]:
        print(f"{sync['behind']} new version(s) from {' and '.join(sync['from'])}: run kit pull")
    if sync["ahead"]:
        print(f"{sync['ahead']} version(s) not shared yet: run kit push")


def cmd_status(args) -> None:
    repo = Repo.find()
    _print_sync(repo)
    _print_conflicts(repo)
    changes = repo.status()
    history = repo.log()
    if not changes:
        print(f"Nothing changed since v{len(history)}." if history
              else "This repo is empty. Add some sounds, then run kit commit.")
        return
    print(f"Changes since v{len(history)}:" if history else "Not saved yet:")
    _print_changes(changes, locate=lambda path: repo.folder / path)
    _print_name_warnings(repo.working())
    print('Save them with: kit commit -m "what changed"')


def cmd_commit(args) -> None:
    repo = Repo.find()
    saved = repo.commit(args.message, author=args.author)
    number = len(repo.log())
    print(f"Saved v{number} ({saved.commit.id[:8]}): {saved.commit.message}")
    _print_changes(saved.changes, locate=lambda path: repo.folder / path)
    print(f"{_summary(saved.changes)}. Stored {saved.new_files} new file(s), "
          f"{_size(saved.new_bytes)}.")
    _print_name_warnings(repo.manifest_of(saved.commit.id))


def cmd_log(args) -> None:
    repo = Repo.find()
    history = repo.log()
    if not history:
        print("No versions saved yet.")
        return
    for index, commit in enumerate(history):
        when = commit.time[:16].replace("T", " ")
        named = "".join(f" [{name}]" for name in repo.names_of(commit.id))
        print(f"v{len(history) - index:<3} {commit.id[:8]}  {when} UTC  "
              f"{commit.author}: {commit.message}{named}")


def cmd_diff(args) -> None:
    repo = Repo.find()
    changes = repo.diff_versions(args.old, args.new)
    if not changes:
        print("Those two versions hold the same files.")
        return
    newer = repo.manifest_of(repo.resolve(args.new))
    _print_changes(changes, locate=lambda path: repo.store.file_for(newer[path]["hash"]))
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
    labels = _labels(repo)
    width = max(len(path) for path in credits)
    share: dict[str, int] = {}
    for path, commit in credits.items():
        share[commit.author] = share.get(commit.author, 0) + 1
        print(f"  {path:<{width}}  {commit.author} ({labels.get(commit.id, commit.id[:8])})")
    print("Split by number of sounds: " + ", ".join(
        f"{author} {count * 100 // len(credits)}% ({count})"
        for author, count in sorted(share.items(), key=lambda item: -item[1])))


def cmd_author(args) -> None:
    repo = Repo.find()
    if args.name:
        repo.set_author(" ".join(args.name))
        print(f"New versions will be credited to {repo.author}. Saved versions keep their credits.")
    else:
        print(repo.author)


def cmd_name(args) -> None:
    repo = Repo.find()
    if args.remove:
        name = " ".join(args.words)
        repo.remove_name(name)
        print(f"Removed the name {name}.")
        return
    if len(args.words) < 2:
        raise KitError('say which version and the name, like: kit name v3 1.0')
    version, name = args.words[0], " ".join(args.words[1:])
    commit_id = repo.name_version(name, version)
    print(f"Named {commit_id[:8]} {name.strip()}. Use it anywhere a version goes: kit export {name.strip()}")


def cmd_names(args) -> None:
    repo = Repo.find()
    names = repo.names()
    if not names:
        print('No version has a name yet. Name one with: kit name v3 1.0')
        return
    labels = {c.id: f"v{len(repo.log()) - i}" for i, c in enumerate(repo.log())}
    for name, commit_id in sorted(names.items()):
        print(f"  {name:<24} {labels.get(commit_id, commit_id[:8])}")


def cmd_remote(args) -> None:
    repo = Repo.find()
    if args.remote:
        repo.set_remote(remote.place_to_share(args.remote, repo))
        print(f"Default remote set to {repo.remote()}")
    else:
        print(repo.remote() or "No default remote saved yet.")


def cmd_push(args) -> None:
    repo = Repo.find()
    result = remote.push(repo, _remote_for(repo, args.remote, sending=True))
    if result.commits == 0:
        print("The remote already has every version.")
    else:
        print(f"Pushed {result.commits} version(s), {result.objects} object(s).")
    if note := remote.names_note(result, sent=True):
        print(note)


def _print_pull(repo: Repo, result) -> None:
    note = remote.names_note(result, sent=False)
    if result.outcome == "up-to-date":
        print("Already up to date." + (f" {note}" if note else ""))
        return
    print(f"Pulled {result.commits} version(s), {result.objects} object(s).")
    if note:
        print(note)
    _print_changes(result.changes, locate=lambda path: repo.folder / path)
    if result.outcome == "merge":
        print(f"Both sides had new versions, so they were merged as v{len(repo.log())}.")
    if result.conflicts:
        _print_conflicts(repo)
        waiting = {c["path"] for c in repo.conflicts()}
        for path in result.conflicts:
            if path not in waiting:  # one side deleted it, the other changed it
                print(f"  ~ {path}: deleted on one side and changed on the other; the changed one was kept")


def cmd_resolve(args) -> None:
    repo = Repo.find()
    print(repo.settle(args.path, args.keep))
    print('Save the choice with: kit commit -m "chose the kick"')


def cmd_pull(args) -> None:
    repo = Repo.find()
    _print_pull(repo, remote.pull(repo, _remote_for(repo, args.remote, sending=False)))


def cmd_clone(args) -> None:
    spec = remote.find_shared(args.remote)
    folder = args.folder or Path(spec.rstrip("/")).name
    repo, result = remote.clone(spec, folder, author=args.author)
    print(f"Cloned into {repo.folder} (author: {repo.author})")
    _print_pull(repo, result)


def cmd_show(args) -> None:
    repo = Repo.find()
    html = build_report(repo, args.version)
    out = Path(args.output) if args.output else repo.dir / "show.html"
    out.write_text(html, encoding="utf-8")
    print(f"Wrote {out}")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())


def cmd_export(args) -> None:
    from .export import export

    repo = Repo.find()
    out, sounds, total = export(repo, args.version or "latest", Path(args.output or repo.folder.parent))
    print(f"Wrote {out} ({sounds} sounds, {_size(total)})")


def cmd_splits(args) -> None:
    from .splits import write_split_sheet

    repo = Repo.find()
    out = write_split_sheet(repo, args.version or "latest", Path(args.output or repo.folder.parent))
    print(f"Wrote {out}")


def cmd_dupes(args) -> None:
    from .fingerprint import find_duplicates

    folders = [Path(f) for f in args.folders] or [Repo.find().folder]
    for folder in folders:
        if not Path(folder).expanduser().is_dir():
            raise KitError(f"there is no folder at {folder}")

    def progress(done: int, total: int) -> None:
        if sys.stderr.isatty():
            print(f"\rListening: {done}/{total} sounds", end="\n" if done == total else "", file=sys.stderr, flush=True)

    groups = find_duplicates(folders, progress=progress)
    if not groups:
        print("No sound is in more than one place.")
        return
    several = len(folders) > 1
    for group in groups:
        kind = "The same file" if group.exact else "The same sound in different files"
        print(f"{kind}, {len(group.places)} places:")
        for place in group.places:
            print(f"  {place.kit + '/' if several else ''}{place.path}")
    extra = sum(group.extra_bytes for group in groups)
    print(f"{len(groups)} sound(s) are in more than one place; the copies take {_size(extra)}.")


def cmd_like(args) -> None:
    from .fingerprint import most_like

    repo = Repo.find()
    sound = Path(args.sound).expanduser().resolve()
    if not sound.is_relative_to(repo.folder) or not sound.is_file():
        raise KitError(f"there is no sound at {args.sound} in this kit")
    folders = [repo.folder] + [Path(f) for f in args.kits]
    for folder in folders:
        if not Path(folder).expanduser().is_dir():
            raise KitError(f"there is no folder at {folder}")

    def progress(done: int, total: int) -> None:
        if sys.stderr.isatty():
            print(f"\rListening: {done}/{total} sounds", end="\n" if done == total else "", file=sys.stderr, flush=True)

    try:
        found = most_like(folders, repo.folder, sound.relative_to(repo.folder).as_posix(), args.count, progress)
    except ValueError as err:
        raise KitError(str(err)) from None
    if not found:
        print("There is no other sound to compare it with.")
        return
    several = len(folders) > 1
    for place, score in found:
        print(f"{score:6.2f}  {place.kit + '/' if several else ''}{place.path}")


def cmd_check(args) -> None:
    from .kinds import sort_kit
    from .problems import check_kit

    repo = Repo.find()

    def progress(done: int, total: int) -> None:
        if sys.stderr.isatty():
            print(f"\rChecking: {done}/{total} sounds", end="\n" if done == total else "", file=sys.stderr, flush=True)

    working = repo.working()
    loops = {path for path, kind in sort_kit(repo, working=working).items() if kind["kind"] == "Loop"}
    found = check_kit(repo, loops, progress=progress, working=working)
    if args.paths:
        wanted = [p.rstrip("/") for p in args.paths]
        found = {path: probs for path, probs in found.items()
                 if any(path == w or path.startswith(w + "/") for w in wanted)}
    if not found:
        print("No problems found.")
        return
    for path, probs in found.items():
        print(path)
        for problem in probs:
            print(f"  {problem['text']}")
    print(f"{len(found)} sound(s) with problems.")


def cmd_kinds(args) -> None:
    from .audio import KINDS
    from .kinds import sort_kit

    repo = Repo.find()

    def progress(done: int, total: int) -> None:
        if sys.stderr.isatty():
            print(f"\rListening: {done}/{total} sounds", end="\n" if done == total else "", file=sys.stderr, flush=True)

    sorted_ = sort_kit(repo, progress=progress)
    wanted = args.kind.capitalize() if args.kind and args.kind.lower() != "808" else args.kind
    if wanted and wanted not in KINDS:
        raise KitError(f"kinds are: {', '.join(KINDS)}")
    for name in KINDS:
        paths = [path for path, found in sorted_.items() if found["kind"] == name]
        if wanted and name != wanted or not paths:
            continue
        print(f"{name} ({len(paths)}):")
        for path in paths:
            print(f"  {path}{'' if sorted_[path]['from'] == 'name' else '  (by its sound)'}")
    unknown = len(repo.working()) - len(sorted_)
    if unknown and not wanted:
        print(f"{unknown} file(s) of no kind (not audio Kit can read, or silent).")


def cmd_chop(args) -> None:
    from .chop import chop

    repo = Repo.find()
    path = Path(args.path).resolve()
    try:
        name = path.relative_to(repo.folder).as_posix()
    except ValueError:
        raise KitError(f"{args.path} is not inside this kit") from None
    if not path.is_file():
        raise KitError(f"there is no sound at {args.path}")
    files = chop(path, name, min_gap=args.gap / 1000)
    print(f"Cut {name} into {len(files)} pieces, in {files[0].parent.relative_to(repo.folder).as_posix()}/")
    print("They are new sounds in the kit: save a version to keep them.")


def cmd_ui(args) -> None:
    from .app import serve  # imported here so the other commands start instantly

    serve(args.folder, port=args.port, open_browser=not args.no_open, dev=args.dev)


def cmd_app(args) -> None:
    from .desktop import run

    run(args.folder, window=not args.browser, dev=args.dev)


def cmd_verify(args) -> None:
    repo = Repo.find()
    problems = repo.verify()
    if not problems:
        print("Everything checks out: every stored file and version matches its hash.")
        return
    for problem in problems:
        print(f"  ! {problem}")
    raise KitError(f"{len(problems)} problem(s) found")


def cmd_note(args) -> None:
    repo = Repo.find()
    path = args.path.replace("\\", "/")
    if args.text is None:
        note = repo.notes().get(path)
        print(f'{note["text"]}  ({note["by"]})' if note else f"{path} has no note.")
        return
    repo.note(path, args.text)
    print(f"Note on {path} saved; push to share it." if args.text.strip() else f"Note on {path} cleared.")


def cmd_notes(args) -> None:
    notes = Repo.find().notes()
    if not notes:
        print('No notes yet. Add one with: kit note "Loops/dark.wav" "use for the hook"')
    for path, note in notes.items():
        print(f"{path}: {note['text']}  ({note['by']})")


def cmd_compact(args) -> None:
    repo = Repo.find()

    def progress(done: int, total: int) -> None:
        if sys.stderr.isatty():
            print(f"\rCompressing: {done}/{total} files", end="\n" if done == total else "", file=sys.stderr, flush=True)

    files, before, after = repo.store.compact(progress=progress)
    if not files:
        print("Nothing to compress: no stored WAV files that would get smaller.")
        return
    print(f"Compressed {files} WAV file(s) in .kit: {_size(before)} -> {_size(after)} "
          f"(saved {_size(before - after)}, {1 - after / before:.0%}).")
    print("Your sounds in the folder are not touched. Run `kit verify` to check every stored file.")


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

    p = sub.add_parser("remote", help="show or set the default remote")
    p.add_argument("remote", nargs="?", help="a folder path or s3://bucket/prefix")
    p.set_defaults(run=cmd_remote)

    p = sub.add_parser("push", help="send new versions to a remote")
    p.add_argument("remote", nargs="?", help="a folder path or s3://bucket/prefix")
    p.set_defaults(run=cmd_push)

    p = sub.add_parser("pull", help="get new versions from a remote, merging if needed")
    p.add_argument("remote", nargs="?", help="a folder path or s3://bucket/prefix")
    p.set_defaults(run=cmd_pull)

    p = sub.add_parser("name", help="give a version a name, like: kit name v3 1.0")
    p.add_argument("words", nargs="+", help="the version, then the name (or just the name with --remove)")
    p.add_argument("--remove", action="store_true", help="remove a name instead")
    p.set_defaults(run=cmd_name)

    p = sub.add_parser("names", help="list the names given to versions")
    p.set_defaults(run=cmd_names)

    p = sub.add_parser("author", help="show or change the producer name new versions are credited to")
    p.add_argument("name", nargs="*", help="the new name, like: kit author Lil Nojan")
    p.set_defaults(run=cmd_author)

    p = sub.add_parser("resolve", help="settle a sound both sides changed: keep mine, theirs or both")
    p.add_argument("path", help="the sound, as kit status lists it")
    p.add_argument("--keep", required=True, choices=["mine", "theirs", "both"])
    p.set_defaults(run=cmd_resolve)

    p = sub.add_parser("clone", help="make a new kit folder from a remote (fork a pack)")
    p.add_argument("remote", help="a folder path or s3://bucket/prefix")
    p.add_argument("folder", nargs="?", help="where to put it (default: the remote's name)")
    p.add_argument("--author", help="your producer name, used for credits")
    p.set_defaults(run=cmd_clone)

    p = sub.add_parser("export", help="save a version as a plain zip, for people who do not use Kit")
    p.add_argument("version", nargs="?", help="default: the latest version")
    p.add_argument("-o", "--output", help="the folder to put the zip in (default: next to the kit)")
    p.set_defaults(run=cmd_export)

    p = sub.add_parser("chop", help="cut a loop into one-shots, one file per hit")
    p.add_argument("path", help="the loop, e.g. Loops/Groove.wav")
    p.add_argument("--gap", type=float, default=60, help="hits closer than this many ms count as one (default 60)")
    p.set_defaults(run=cmd_chop)

    p = sub.add_parser("kinds", help="sort the sounds into kicks, 808s, snares, claps, hats, percs, melodic and loops")
    p.add_argument("kind", nargs="?", help="list only this kind, e.g. 808")
    p.set_defaults(run=cmd_kinds)

    p = sub.add_parser("dupes", help="find sounds that are in more than one place, even renamed or re-bounced")
    p.add_argument("folders", nargs="*", help="kits or folders to look through (default: this kit)")
    p.set_defaults(run=cmd_dupes)

    p = sub.add_parser("check", help="find clipping, late starts, clicks and silent files")
    p.add_argument("paths", nargs="*", help="only these sounds or folders (default: the whole kit)")
    p.set_defaults(run=cmd_check)

    p = sub.add_parser("like", help="the sounds most like one sound, here and in other kits (smaller number = closer)")
    p.add_argument("sound", help="a sound in this kit")
    p.add_argument("kits", nargs="*", help="other kits or folders to look through too")
    p.add_argument("-n", "--count", type=int, default=10, help="how many to list (default: 10)")
    p.set_defaults(run=cmd_like)

    p = sub.add_parser("splits", help="save a split sheet (PDF): who added which sounds, with a share for each")
    p.add_argument("version", nargs="?", help="default: the latest version")
    p.add_argument("-o", "--output", help="the folder to put the PDF in (default: next to the kit)")
    p.set_defaults(run=cmd_splits)

    p = sub.add_parser("show", help="open a version as a web page with waveforms and players")
    p.add_argument("version", nargs="?", help="default: the latest version")
    p.add_argument("-o", "--output", help="where to write the page (default: .kit/show.html)")
    p.add_argument("--no-open", action="store_true", help="write the page without opening it")
    p.set_defaults(run=cmd_show)

    p = sub.add_parser("ui", help="open Kit as an app in your browser")
    p.add_argument("folder", nargs="?", default=".", help="the kit folder (default: this one)")
    p.add_argument("--port", type=int, default=0, help="port to use (default: any free port)")
    p.add_argument("--no-open", action="store_true", help="start without opening the browser")
    p.add_argument("--dev", action="store_true", help="reload the page when ui.html is saved")
    p.set_defaults(run=cmd_ui)

    p = sub.add_parser("app", help="open Kit in its own window (needs: pip install pywebview)")
    p.add_argument("folder", nargs="?", help="a kit folder to open (default: the home screen)")
    p.add_argument("--browser", action="store_true", help="use the web browser, not a window")
    p.add_argument("--dev", action="store_true", help="reload when ui.html is saved, with developer tools")
    p.set_defaults(run=cmd_app)

    p = sub.add_parser("verify", help="check that nothing stored is damaged or missing")
    p.set_defaults(run=cmd_verify)

    p = sub.add_parser("note", help='show, write or clear (with "") the note on a sound')
    p.add_argument("path")
    p.add_argument("text", nargs="?")
    p.set_defaults(run=cmd_note)

    p = sub.add_parser("notes", help="list the notes on sounds")
    p.set_defaults(run=cmd_notes)

    p = sub.add_parser("compact", help="store saved WAV files as FLAC, about half the space")
    p.set_defaults(run=cmd_compact)

    return parser


class _Progress:
    """One line in the terminal, rewritten as a long first scan reads the kit."""

    def __init__(self):
        self.shown = 0.0

    def __call__(self, files: int, files_total: int, done: int, total: int) -> None:
        now = time.monotonic()
        finished = files == files_total
        if now - self.shown < 0.2 and not finished:
            return  # a few updates a second is plenty
        self.shown = now
        line = f"Reading the kit: {files}/{files_total} files, {_size(done)} of {_size(total)}"
        print("\r" + line, end="\n" if finished else "", file=sys.stderr, flush=True)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if sys.stderr.isatty():
        Repo.on_progress = _Progress()  # only in a real terminal, never in scripts or tests
    try:
        args.run(args)
    except KitError as err:
        print(f"kit: {err}", file=sys.stderr)
        return 1
    return 0
