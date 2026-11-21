"""Make a release in one command: make release VERSION=0.6.0

What it does, stopping at the first problem:

1. checks the version looks like 0.6.0 and is newer than the current one;
2. checks you are on main with nothing uncommitted, so the release is exactly
   what is on GitHub;
3. runs the tests;
4. writes the new version into pyproject.toml and src/kit/__init__.py;
5. asks you to confirm, then commits, tags vX.Y.Z and pushes both.

Pushing the tag starts .github/workflows/release.yml, which builds the apps
and publishes the release. Add --dry-run to see the plan without changing anything.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Each file that holds the version, and the line that holds it.
VERSION_FILES = {
    ROOT / "pyproject.toml": re.compile(r'^version = "(\d+\.\d+\.\d+)"$', re.M),
    ROOT / "src" / "kit" / "__init__.py": re.compile(r'^__version__ = "(\d+\.\d+\.\d+)"$', re.M),
}


class ReleaseError(Exception):
    pass


def parse(version: str) -> tuple[int, int, int]:
    """'0.6.0' -> (0, 6, 0), so versions compare as numbers: 0.10.0 is newer than 0.9.0."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ReleaseError(f"{version!r} is not a version like 0.6.0")
    major, minor, patch = (int(part) for part in version.split("."))
    return major, minor, patch


def current_version() -> str:
    found = {pattern.search(path.read_text()).group(1) for path, pattern in VERSION_FILES.items()}
    if len(found) != 1:
        raise ReleaseError(f"the version files disagree: {sorted(found)}")
    return found.pop()


def write_version(version: str) -> None:
    for path, pattern in VERSION_FILES.items():
        text = path.read_text()
        start, end = pattern.search(text).span(1)
        path.write_text(text[:start] + version + text[end:])


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def check(version: str) -> str:
    """Everything that must be true before releasing. Returns the current version."""
    current = current_version()
    if parse(version) <= parse(current):
        raise ReleaseError(f"{version} is not newer than the current version {current}")
    if git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        raise ReleaseError("switch to the main branch first")
    if git("status", "--porcelain"):
        raise ReleaseError("you have uncommitted changes; commit or undo them first (see git status)")
    if git("tag", "--list", f"v{version}"):
        raise ReleaseError(f"the tag v{version} already exists")
    return current


def run_tests() -> bool:
    return subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT).returncode == 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Release a new version of Kit.")
    parser.add_argument("version", help="the new version, like 0.6.0")
    parser.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
    parser.add_argument("--yes", action="store_true", help="do not ask before pushing")
    args = parser.parse_args(argv)
    try:
        current = check(args.version)
        print(f"Releasing {current} -> {args.version}. Running the tests first...")
        if not run_tests():
            raise ReleaseError("the tests failed, so nothing was released")
        tag = f"v{args.version}"
        steps = [f"set the version to {args.version} in pyproject.toml and src/kit/__init__.py",
                 f'commit "Version {args.version}"', f"tag {tag}", "push the commit and the tag"]
        print("\nThis will:\n" + "\n".join(f"  - {step}" for step in steps))
        if args.dry_run:
            print("\nDry run: nothing was changed.")
            return 0
        if not args.yes and input("\nGo ahead? [y/N] ").strip().lower() != "y":
            print("Stopped; nothing was changed.")
            return 1
        write_version(args.version)
        git("commit", "-am", f"Version {args.version}")
        git("tag", "-a", tag, "-m", f"Kit {args.version}")
        git("push")
        git("push", "origin", tag)
    except ReleaseError as err:
        print(f"release: {err}")
        return 1
    print(f"\nPushed {tag}. GitHub is now building the apps; the release appears at")
    print("https://github.com/nojanpourjafar/kit-vcs/releases in about 5 minutes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
