"""Notes on sounds: written by anyone sharing the kit, carried by push and pull."""

import json

import pytest

from kit import remote
from kit.cli import main
from kit.repo import MAX_NOTE, KitError, Repo, clean_notes

from test_app import app  # noqa: F401  (the running app, used as a fixture)


def text_of(repo):
    return {path: note["text"] for path, note in repo.notes().items()}


def test_a_note_can_be_written_changed_and_cleared(kit):
    repo = Repo.init(kit, author="Nojan")
    repo.commit("first")
    repo.note("Kicks/kick_01.wav", "  use   for the hook ")
    assert repo.notes()["Kicks/kick_01.wav"] == {**repo.notes()["Kicks/kick_01.wav"], "text": "use for the hook",
                                                "by": "Nojan"}
    repo.note("Kicks/kick_01.wav", "too boomy")
    assert text_of(repo) == {"Kicks/kick_01.wav": "too boomy"}
    repo.note("Kicks/kick_01.wav", "")
    assert repo.notes() == {}
    assert repo.status() == []                                         # notes are not changes to save


def test_notes_are_checked(kit):
    repo = Repo.init(kit, author="Nojan")
    with pytest.raises(KitError, match="no sound"):
        repo.note("Kicks/missing.wav", "hi")
    with pytest.raises(KitError):
        repo.note("../outside.wav", "hi")
    with pytest.raises(KitError, match="up to"):
        repo.note("Kicks/kick_01.wav", "x" * (MAX_NOTE + 1))
    repo.note("Kicks/missing.wav", "")                                  # clearing is always fine


def test_the_later_change_wins(kit):
    repo = Repo.init(kit, author="Nojan")
    repo.note("Kicks/kick_01.wav", "mine")
    older = {"Kicks/kick_01.wav": {"text": "older", "by": "Jay", "time": "2000-01-01T00:00:00.000000+00:00"}}
    newer = {"Kicks/kick_01.wav": {"text": "newer", "by": "Jay", "time": "2999-01-01T00:00:00.000000+00:00"}}
    assert repo.take_notes(older) == 0 and text_of(repo) == {"Kicks/kick_01.wav": "mine"}
    assert repo.take_notes(newer) == 1 and repo.notes()["Kicks/kick_01.wav"]["by"] == "Jay"


def test_notes_from_elsewhere_are_read_carefully():
    good = {"text": "ok", "by": "Jay", "time": "2026-01-01T00:00:00+00:00"}
    assert clean_notes({"a.wav": good, "b.wav": {"text": 5}, 7: good, "c.wav": "text"}) == {"a.wav": good}
    assert clean_notes(["not", "a", "dict"]) == {}
    assert clean_notes({"a.wav": {**good, "text": "x" * (MAX_NOTE + 1)}}) == {}


def test_notes_travel_with_push_and_pull(kit, tmp_path):
    mine = Repo.init(kit, author="Nojan")
    mine.commit("first")
    mine.note("Kicks/kick_01.wav", "use for the hook")
    shared = tmp_path / "shared"
    remote.push(mine, remote.open_remote(str(shared)))
    assert "use for the hook" in (shared / "notes.json").read_text()

    friend, _ = remote.clone(str(shared), tmp_path / "friend", author="Jay")
    assert text_of(friend) == {"Kicks/kick_01.wav": "use for the hook"}
    friend.note("Kicks/kick_01.wav", "")                               # Jay clears it
    friend.note("Hats/hat_01.wav", "too bright")
    remote.push(friend, remote.open_remote(str(shared)))               # notes go even with no new version

    remote.pull(mine, remote.open_remote(str(shared)))
    assert text_of(mine) == {"Hats/hat_01.wav": "too bright"}          # the clearing reached me too


def test_a_note_follows_a_renamed_sound(app, kit):
    Repo.init(kit, author="Nojan").commit("first")
    app.json("/api/note", {"path": "Kicks/kick_01.wav", "text": "punchy"})
    (kit / "Kicks" / "kick_01.wav").rename(kit / "Kicks" / "Punch.wav")
    sounds = {s["path"]: s for s in app.json("/api/state")["sounds"]}
    assert sounds["Kicks/Punch.wav"]["note"]["text"] == "punchy"
    assert text_of(Repo(kit)) == {"Kicks/Punch.wav": "punchy"}


def test_the_page_writes_and_shows_notes(app, kit):
    Repo.init(kit, author="Nojan").commit("first")
    answer = app.json("/api/note", {"path": "Hats/hat_01.wav", "text": "open hat later"})
    assert answer["note"]["text"] == "open hat later" and answer["note"]["by"] == "Nojan"
    sounds = {s["path"]: s for s in app.json("/api/state")["sounds"]}
    assert sounds["Hats/hat_01.wav"]["note"]["text"] == "open hat later" and sounds["Kicks/kick_01.wav"]["note"] is None
    version = {s["path"]: s for s in app.json("/api/version?ref=v1")["sounds"]}
    assert version["Hats/hat_01.wav"]["note"]["text"] == "open hat later"   # old versions show it too
    assert app.json("/api/note", {"path": "Hats/hat_01.wav", "text": ""})["note"] is None


def test_the_commands(kit, monkeypatch, capsys):
    Repo.init(kit, author="Nojan")
    monkeypatch.chdir(kit)
    main(["note", "Kicks/kick_01.wav", "use for the hook"])
    assert "saved" in capsys.readouterr().out
    main(["note", "Kicks/kick_01.wav"])
    assert "use for the hook  (Nojan)" in capsys.readouterr().out
    main(["notes"])
    assert "Kicks/kick_01.wav: use for the hook" in capsys.readouterr().out
    main(["note", "Kicks/kick_01.wav", ""])
    assert "cleared" in capsys.readouterr().out
    assert json.loads((kit / ".kit" / "notes.json").read_text())["Kicks/kick_01.wav"]["text"] == ""
