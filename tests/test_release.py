"""scripts/release.py, rehearsed on a throwaway repo with a throwaway "GitHub"."""

import importlib.util
import subprocess
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release.py"
spec = importlib.util.spec_from_file_location("release", SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def git(folder, *args):
    return subprocess.run(["git", *args], cwd=folder, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A tiny project at version 0.5.0, pushed to a local stand-in for GitHub."""
    remote = tmp_path / "github.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    folder = tmp_path / "kit"
    (folder / "src" / "kit").mkdir(parents=True)
    (folder / "pyproject.toml").write_text('[project]\nname = "kit-vcs"\nversion = "0.5.0"\n')
    (folder / "src" / "kit" / "__init__.py").write_text('"""Kit."""\n\n__version__ = "0.5.0"\n')
    git(folder, "init", "-b", "main")
    git(folder, "config", "user.name", "Test")
    git(folder, "config", "user.email", "test@example.com")
    git(folder, "add", ".")
    git(folder, "commit", "-m", "start")
    git(folder, "remote", "add", "origin", str(remote))
    git(folder, "push", "-u", "origin", "main")

    monkeypatch.setattr(release, "ROOT", folder)
    patterns = list(release.VERSION_FILES.values())  # same patterns, pointed at the throwaway files
    monkeypatch.setattr(release, "VERSION_FILES", {
        folder / "pyproject.toml": patterns[0],
        folder / "src" / "kit" / "__init__.py": patterns[1],
    })
    monkeypatch.setattr(release, "run_tests", lambda: True)
    return types.SimpleNamespace(folder=folder, remote=remote)


def test_versions_compare_as_numbers():
    assert release.parse("0.10.0") > release.parse("0.9.0")
    for bad in ["0.6", "v0.6.0", "0.6.0-beta", ""]:
        with pytest.raises(release.ReleaseError):
            release.parse(bad)


def test_a_release_commits_tags_and_pushes(project, capsys):
    assert release.main(["0.6.0", "--yes"]) == 0

    assert release.current_version() == "0.6.0"
    assert 'version = "0.6.0"' in (project.folder / "pyproject.toml").read_text()
    assert git(project.remote, "log", "-1", "--format=%s", "main") == "Version 0.6.0"
    assert git(project.remote, "tag", "--list") == "v0.6.0"          # the tag reached "GitHub"
    assert git(project.folder, "status", "--porcelain") == ""


def test_a_dry_run_changes_nothing(project, capsys):
    assert release.main(["0.6.0", "--dry-run"]) == 0
    assert "Dry run" in capsys.readouterr().out
    assert release.current_version() == "0.5.0" and git(project.folder, "tag", "--list") == ""


@pytest.mark.parametrize("setup, version, message", [
    (lambda folder: None, "0.5.0", "not newer"),
    (lambda folder: None, "0.4.9", "not newer"),
    (lambda folder: (folder / "notes.txt").write_text("x"), "0.6.0", "uncommitted"),
    (lambda folder: git(folder, "switch", "-c", "experiment"), "0.6.0", "main branch"),
    (lambda folder: git(folder, "tag", "v0.6.0"), "0.6.0", "already exists"),
])
def test_unsafe_releases_are_refused(project, capsys, setup, version, message):
    setup(project.folder)
    assert release.main([version, "--yes"]) == 1
    assert message in capsys.readouterr().out
    assert git(project.remote, "tag", "--list") == ""                # nothing was pushed


def test_failing_tests_stop_the_release(project, monkeypatch, capsys):
    monkeypatch.setattr(release, "run_tests", lambda: False)
    assert release.main(["0.6.0", "--yes"]) == 1
    assert "tests failed" in capsys.readouterr().out
    assert release.current_version() == "0.5.0"


def test_answering_no_stops_the_release(project, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert release.main(["0.6.0"]) == 1
    assert release.current_version() == "0.5.0" and git(project.folder, "tag", "--list") == ""
