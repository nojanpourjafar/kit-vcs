"""Sorting sounds by kind: from their names, or by listening when the names say nothing."""

import random
from collections import Counter

import pytest

from kit.audio import KINDS, kind_of
from kit.cli import main
from kit.kinds import kind_from_name, sort_kit
from kit.repo import Repo

from sounds_by_kind import KINDS as MAKERS
from test_tempo import drums
from test_key import chord_loop, NOTES


def write_wav(path, samples, rate=44100):
    import struct, wave
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, x)) * 32767)) for x in samples))


@pytest.mark.parametrize("path, expected", [
    ("Kicks/Punch.wav", "Kick"), ("BD_Boom.wav", "Kick"), ("808s/Glide.wav", "808"), ("Bass/Sub.wav", "808"),
    ("hh_closed_03.wav", "Hat"), ("OH 01.wav", "Hat"), ("Drums/Snare (tight).wav", "Snare"), ("CLAP_01.wav", "Clap"),
    ("Percs/Rim 2.wav", "Perc"), ("Synths/Lead.wav", "Melodic"), ("Vox/Chop.wav", "Melodic"),
    ("Loops/Dark Bells 140bpm F#m.wav", "Loop"), ("Melody Loop 90bpm.wav", "Loop"),
    ("Sample 0042.wav", None), ("kickstarter.wav", None), ("Drums/thing.wav", None),
])
def test_names_say_what_kind(path, expected):
    assert kind_from_name(path) == expected


def test_each_kind_is_told_apart_by_its_sound():
    # Generated sounds with random variation, 20 of each, from seeds the rules were not set on.
    # A real kit is harder than this: see the README for what that means.
    rnd = random.Random(42)
    results = {name: Counter(kind_of(make(rnd), 44100) for _ in range(20)) for name, make in MAKERS.items()}
    for name, got in results.items():
        assert got[name] >= 18, (name, dict(got))


def test_loops_and_silence(tmp_path):
    import wave, array
    drums(tmp_path / "groove.wav", 128, bars=2)
    with wave.open(str(tmp_path / "groove.wav")) as w:
        groove = array.array("h", w.readframes(w.getnframes()))
    assert kind_of(groove, 22050) == "Loop"                                            # a beat
    assert kind_of(chord_loop(NOTES.index("A"), "min", [0, 5, 2, 6]), 22050) == "Loop"   # chords, no beat
    assert kind_of([0.0] * 44100, 44100) is None
    assert kind_of([], 44100) is None


def test_a_kit_is_sorted_by_name_first_then_by_sound(kit, monkeypatch):
    rnd = random.Random(5)
    write_wav(kit / "Mystery" / "Sample 01.wav", MAKERS["808"](rnd))     # the name says nothing: listened to
    write_wav(kit / "Mystery" / "Sample 02.wav", MAKERS["Hat"](rnd))
    write_wav(kit / "Kicks" / "thing 3.wav", MAKERS["Hat"](rnd))         # sounds like a hat, but its folder says Kick: names win
    repo = Repo.init(kit, author="Nojan")
    found = sort_kit(repo)
    assert found["Mystery/Sample 01.wav"] == {"kind": "808", "from": "sound"}
    assert found["Mystery/Sample 02.wav"] == {"kind": "Hat", "from": "sound"}
    assert found["Kicks/thing 3.wav"] == {"kind": "Kick", "from": "name"}
    assert found["Hats/hat_01.wav"] == {"kind": "Hat", "from": "name"}
    assert (kit / ".kit" / "kinds.json").exists()

    import kit.kinds as kinds_module
    listened = []
    monkeypatch.setattr(kinds_module, "kind", lambda path, name: listened.append(name))
    assert sort_kit(repo) == found and listened == []                   # remembered: nothing listened to again


def test_the_command(kit, monkeypatch, capsys):
    write_wav(kit / "Mystery" / "Sample 01.wav", MAKERS["808"](random.Random(5)))
    Repo.init(kit, author="Nojan")
    monkeypatch.chdir(kit)
    main(["kinds"])
    out = capsys.readouterr().out
    assert "808 (1):\n  Mystery/Sample 01.wav  (by its sound)" in out and "Kick (2):" in out
    main(["kinds", "hat"])
    assert capsys.readouterr().out == "Hat (1):\n  Hats/hat_01.wav\n"
    assert "Loop" in KINDS


def test_the_app_shows_kinds_and_sorts_the_rest_in_the_background(kit):
    import time
    from kit.app import App
    write_wav(kit / "Mystery" / "Sample 01.wav", MAKERS["808"](random.Random(5)))
    repo = Repo.init(kit, author="Nojan")
    repo.commit("first")
    app = App(kit)
    before = {s["path"]: s["kind"] for s in app.state()["sounds"]}
    assert before["Kicks/kick_01.wav"] == "Kick" and before["Mystery/Sample 01.wav"] is None   # not listened to yet

    app.auto_sort = True
    app.sort_soon()
    deadline = time.time() + 20
    while app.sorting and time.time() < deadline:
        time.sleep(0.05)
    after = {s["path"]: s["kind"] for s in app.state()["sounds"]}
    assert after["Mystery/Sample 01.wav"] == "808" and app.state()["sorting"] is False
    assert {s["path"]: s["kind"] for s in app.version("v1")["sounds"]}["Mystery/Sample 01.wav"] == "808"
    app.sort_soon()
    assert app.sorting is False                                         # nothing left to listen to: no thread


def test_sorting_is_off_unless_the_app_turns_it_on(kit):
    from kit.app import App
    write_wav(kit / "Mystery" / "Sample 01.wav", MAKERS["Hat"](random.Random(5)))
    Repo.init(kit, author="Nojan")
    app = App(kit)
    app.sort_soon()
    assert not app.sorting and not (kit / ".kit" / "kinds.json").exists()
