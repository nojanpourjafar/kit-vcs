"""Sounds both sides changed: remembered after a pull, listened to, then settled."""

import pytest

from kit import remote
from kit.app import App
from kit.cli import main
from kit.repo import KitError

from test_remote import _shared


@pytest.fixture
def clash(kit, tmp_path):
    """Nojan and Lucas both changed the closed hat, then Nojan pulled."""
    mine, theirs, rem = _shared(kit, tmp_path)
    (theirs.folder / "Hats" / "hat_01.wav").write_bytes(b"lucas made it brighter")
    theirs.commit("brighter hat")
    remote.push(theirs, rem)
    (kit / "Hats" / "hat_01.wav").write_bytes(b"nojan made it shorter")
    mine.commit("shorter hat")
    remote.pull(mine, rem)
    return mine


def test_a_pull_remembers_the_sounds_to_choose_between(clash):
    assert clash.conflicts() == [
        {"path": "Hats/hat_01.wav", "theirs": "Hats/hat_01 (from Lucas).wav", "author": "Lucas"}
    ]


@pytest.mark.parametrize("keep, hat, left_beside", [
    ("mine", b"nojan made it shorter", False),
    ("theirs", b"lucas made it brighter", False),
    ("both", b"nojan made it shorter", True),
])
def test_each_choice_changes_the_files_and_closes_the_conflict(clash, keep, hat, left_beside):
    clash.settle("Hats/hat_01.wav", keep)

    hats = clash.folder / "Hats"
    assert (hats / "hat_01.wav").read_bytes() == hat
    assert (hats / "hat_01 (from Lucas).wav").exists() == left_beside
    assert clash.conflicts() == []
    if keep != "both":
        assert clash.status() != []                 # the choice is an ordinary change to save
        clash.commit(f"kept {keep}")
        assert clash.status() == []


def test_settling_by_hand_in_finder_also_closes_it(clash):
    (clash.folder / "Hats" / "hat_01 (from Lucas).wav").unlink()
    assert clash.conflicts() == []


def test_mistakes_are_refused(clash):
    with pytest.raises(KitError, match="no open conflict"):
        clash.settle("Kicks/kick_01.wav", "mine")
    with pytest.raises(KitError, match="keep must be"):
        clash.settle("Hats/hat_01.wav", "neither")
    assert len(clash.conflicts()) == 1


def test_the_command_line_lists_and_settles(clash, monkeypatch, capsys):
    monkeypatch.chdir(clash.folder)
    main(["status"])
    out = capsys.readouterr().out
    assert "! Hats/hat_01.wav  (theirs: Hats/hat_01 (from Lucas).wav, from Lucas)" in out
    assert main(["resolve", "Hats/hat_01.wav", "--keep", "theirs"]) == 0
    assert "Kept Lucas's Hats/hat_01.wav" in capsys.readouterr().out
    assert clash.conflicts() == []


def test_the_app_shows_and_settles(clash):
    app = App(clash.folder)
    assert [c["theirs"] for c in app.state()["conflicts"]] == ["Hats/hat_01 (from Lucas).wav"]
    message = app.act("resolve", {"path": "Hats/hat_01.wav", "keep": "mine"})["message"]
    assert message.startswith("Kept your Hats/hat_01.wav") and "Save a version" in message
    assert app.state()["conflicts"] == []
