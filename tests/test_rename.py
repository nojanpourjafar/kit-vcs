"""Naming sounds whose names say nothing after what Kit heard in them."""

import random

import pytest

from kit.app import App
from kit.cli import main
from kit.kinds import kind_from_name
from kit.rename import apply, suggest
from kit.repo import KitError, Repo
from kit.tags import from_name

from sounds_by_kind import KINDS
from test_problems import faded, write
from test_tempo import drums


def entry():
    return {"hash": "x", "size": 1}


def test_names_use_the_kind_and_what_was_heard():
    working = {p: entry() for p in ("Sample 0042.wav", "Audio 7.wav", "untitled.aif", "Pack/take 3.wav")}
    heard = {"Sample 0042.wav": "Kick", "Audio 7.wav": "808", "untitled.aif": "Loop", "Pack/take 3.wav": "Melodic"}
    sounds = {"Sample 0042.wav": {}, "Audio 7.wav": {"note": "F#"},
              "untitled.aif": {"bpm": 139.6, "key": "F# min"}, "Pack/take 3.wav": {"key": "C maj"}}
    plan = {m["from"]: m["to"] for m in suggest(working, heard, sounds.get)}
    assert plan == {"Sample 0042.wav": "Kick 01.wav", "Audio 7.wav": "808 01 F#.wav",
                    "untitled.aif": "Loop 01 140bpm F#m.aif", "Pack/take 3.wav": "Pack/Melodic 01 Cmaj.wav"}


def test_new_names_read_back_as_the_same_kind_tempo_and_key():
    assert kind_from_name("Loop 01 140bpm F#m.wav") == "Loop"
    assert from_name("Loop 01 140bpm F#m.wav") == {"bpm": 140, "key": "F# min"}
    assert from_name("Melodic 01 Cmaj.wav")["key"] == "C maj"
    for kind in ("Kick", "808", "Snare", "Clap", "Hat", "Perc", "Melodic"):
        assert kind_from_name(f"{kind} 01.wav") == kind


def test_numbers_carry_on_and_never_collide():
    working = {p: entry() for p in ("Drums/Kick 01.wav", "Drums/Kick 03.wav", "Drums/a.wav", "Drums/b.wav", "c.wav")}
    heard = {"Drums/a.wav": "Kick", "Drums/b.wav": "Kick", "c.wav": "Kick"}
    plan = {m["from"]: m["to"] for m in suggest(working, heard, lambda path: {})}
    assert plan == {"Drums/a.wav": "Drums/Kick 02.wav", "Drums/b.wav": "Drums/Kick 04.wav", "c.wav": "Kick 01.wav"}


def test_a_name_or_folder_that_already_says_the_kind_is_enough():
    working = {p: entry() for p in ("Kicks/a.wav", "groove 92bpm.wav", "hh_01.wav")}
    assert suggest(working, {p: "Kick" for p in working}, lambda path: {}) == []


def test_sounds_kit_heard_no_kind_in_are_left_alone():
    assert suggest({"noise.wav": entry()}, {}, lambda path: {}) == []


def test_a_tempo_or_key_in_the_old_name_is_kept():
    working = {"take 3 Am.wav": entry()}
    plan = suggest(working, {"take 3 Am.wav": "Melodic"}, lambda path: {"key": "C maj"})
    assert plan[0]["to"] == "Melodic 01 Am.wav"


def test_apply_checks_every_move_before_making_any(tmp_path):
    (tmp_path / "a.wav").write_bytes(b"a")
    (tmp_path / "b.wav").write_bytes(b"b")
    (tmp_path / "Kick 02.wav").write_bytes(b"taken")
    with pytest.raises(KitError, match="already a file"):
        apply(tmp_path, [{"from": "a.wav", "to": "Kick 01.wav"}, {"from": "b.wav", "to": "Kick 02.wav"}])
    assert (tmp_path / "a.wav").exists()  # nothing was moved
    with pytest.raises(KitError, match="same folder"):
        apply(tmp_path, [{"from": "a.wav", "to": "../a.wav"}])
    assert apply(tmp_path, [{"from": "a.wav", "to": "Kick 01.wav"}]) == 1
    assert (tmp_path / "Kick 01.wav").read_bytes() == b"a"


@pytest.fixture
def kit(tmp_path):
    folder = tmp_path / "Pack"
    rnd = random.Random(4)
    write(folder / "Sample 0042.wav", faded(KINDS["Kick"](rnd)))
    write(folder / "Sample 0043.wav", faded(KINDS["Hat"](rnd)))
    folder.mkdir(exist_ok=True)
    drums(folder / "untitled.wav", 128, bars=2)
    write(folder / "Kicks" / "named.wav", faded(KINDS["Kick"](rnd)))
    Repo.init(folder, author="Test").commit("first")
    return folder


def test_kit_rename_shows_first_and_renames_with_yes(kit, monkeypatch, capsys):
    monkeypatch.chdir(kit)
    main(["rename"])
    out = capsys.readouterr().out
    assert "Sample 0042.wav  ->  Kick 01" in out  # a kick can carry its note too
    assert "Sample 0043.wav  ->  Hat 01.wav" in out
    assert "untitled.wav  ->  Loop 01 128bpm" in out
    assert "would be renamed" in out and (kit / "Sample 0042.wav").exists()
    main(["rename", "--yes"])
    assert list(kit.glob("Kick 01*.wav")) and not (kit / "Sample 0042.wav").exists()
    assert "named.wav" not in out


def test_a_renamed_sound_keeps_its_credit(kit):
    repo = Repo(kit)
    first = repo.credits()["Sample 0042.wav"]
    apply(kit, [{"from": "Sample 0042.wav", "to": "Kick 01.wav"}])
    repo.commit("named by sound")
    assert repo.credits()["Kick 01.wav"] == first


def test_the_app_previews_then_renames(kit):
    app = App(kit)
    sounds = {s["path"]: s for s in app.state()["sounds"]}
    plan = app.rename_plan()
    assert {m["from"] for m in plan} == {"Sample 0042.wav", "Sample 0043.wav", "untitled.wav"}
    assert (kit / "Sample 0042.wav").exists()  # a preview renames nothing
    assert app.state()["sounds"] and {s["path"]: s["unnamed"] for s in app.state()["sounds"]}["Sample 0042.wav"]
    message = app.act("rename-heard", {"plan": plan})["message"]
    assert message.startswith("Renamed 3 sounds")
    assert not any(s["unnamed"] for s in app.state()["sounds"])
