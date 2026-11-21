"""File names that would break on Windows are caught before they are shared."""

import sys

import pytest

from kit import names
from kit.app import App
from kit.cli import main
from kit.repo import KitError, Repo

# These tests create files with such names, which Windows itself refuses to do.
# On Windows the same rules are covered by the tests above, which need no files.
makes_bad_files = pytest.mark.skipif(sys.platform == "win32", reason="Windows cannot create these names")


@pytest.mark.parametrize("path, why", [
    ("Drums/What?.wav", "?"),
    ('Vox/"Hey".wav', '"'),
    ("Kicks/kick|hard.wav", "|"),
    ("Loops/120:Am.wav", ":"),
    ("con.wav", "con"),
    ("Aux/hat.wav", "Aux"),
    ("LPT1.txt", "LPT1"),
    ("Snares/snare .wav ", "space or a dot"),
    ("Drafts./kick.wav", "space or a dot"),
    ("tab\there.wav", "\\x09"),
])
def test_names_windows_refuses_are_explained(path, why):
    assert why in names.problem(path)


@pytest.mark.parametrize("path", [
    "Kicks/kick_01.wav", "808s/808 F#.wav", "Drums & Bass/hat (open).wav",
    "Console/hit.wav", "COM10.wav", "Vox/.hidden.wav", "über/ünïcödé.wav",
])
def test_ordinary_names_are_fine(path):
    assert names.problem(path) is None


def test_names_differing_only_in_capitals_are_flagged():
    lines = names.warnings(["Kicks/Kick.wav", "Kicks/kick.wav", "Hats/hat.wav"])
    assert lines == ["Kicks/Kick.wav and Kicks/kick.wav differ only in capital letters; "
                     "Mac and Windows treat them as one file"]


@makes_bad_files
def test_status_and_commit_warn_but_still_save(kit, monkeypatch, capsys):
    monkeypatch.chdir(kit)
    Repo.init(kit, author="Nojan")
    (kit / "Kicks" / "What?.wav").write_bytes(b"HUH" * 10)

    main(["status"])
    assert "! Kicks/What?.wav: Windows does not allow ? in names" in capsys.readouterr().out
    assert main(["commit", "-m", "first"]) == 0                      # the warning does not block
    out = capsys.readouterr().out
    assert "Saved v1" in out and "Kicks/What?.wav" in out and "will not work" in out


@makes_bad_files
def test_the_app_lists_the_warnings(kit):
    Repo.init(kit, author="Nojan")
    assert App(kit).state()["warnings"] == []
    (kit / "Hats" / "AUX.wav").write_bytes(b"A")
    assert App(kit).state()["warnings"] == ["Hats/AUX.wav: Windows keeps the name AUX for itself"]


@makes_bad_files
def test_windows_refuses_before_writing_anything(kit, monkeypatch):
    repo = Repo.init(kit, author="Nojan")
    (kit / "Kicks" / "What?.wav").write_bytes(b"HUH" * 10)
    repo.commit("with a bad name")
    (kit / "Kicks" / "What?.wav").unlink()
    (kit / "Kicks" / "kick_01.wav").unlink()
    repo.commit("both gone")

    monkeypatch.setattr("kit.repo.sys.platform", "win32")          # pretend to be Windows
    with pytest.raises(KitError, match="cannot write Kicks/What\\?.wav on Windows"):
        repo.restore("v1")
    assert not (kit / "Kicks" / "kick_01.wav").exists()             # nothing was half restored
