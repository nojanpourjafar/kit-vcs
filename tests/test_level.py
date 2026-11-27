"""Turning sounds up or down to a loudness, never past -1 dBFS."""

import random
import time

import pytest

from kit.app import App
from kit.cli import main
from kit.level import level
from kit.loudness import measure_file
from kit.repo import KitError, Repo

from sounds_by_kind import KINDS
from test_problems import faded, write


def quieter(samples, db):
    return [x * 10 ** (-db / 20) for x in samples]


@pytest.fixture
def kit(tmp_path):
    """Three kicks at about the same level and one 15 dB quieter, all saved; and an unsaved 808."""
    folder = tmp_path / "Pack"
    rnd = random.Random(5)
    for i in range(3):
        write(folder / "Kicks" / f"kick {i}.wav", quieter(faded(KINDS["Kick"](rnd)), 6))
    write(folder / "Kicks" / "quiet kick.wav", quieter(faded(KINDS["Kick"](rnd)), 21))
    repo = Repo.init(folder, author="Test")
    repo.commit("first")
    write(folder / "808s" / "new.wav", quieter(faded(KINDS["808"](rnd)), 20), width=3, channels=2)
    return folder


def lufs(path):
    return measure_file(path, path.name)["lufs"]


def test_a_sound_is_turned_to_the_level_asked(kit):
    repo = Repo(kit)
    out, gain, result = level(repo, "Kicks/quiet kick.wav", -20.0)
    assert out == kit / "Kicks" / "quiet kick.wav"  # saved, so changed in place
    assert gain > 0
    assert lufs(out) == pytest.approx(-20.0, abs=0.15) == result


def test_turning_up_stops_short_of_clipping(kit):
    repo = Repo(kit)
    out, gain, result = level(repo, "Kicks/quiet kick.wav", 0.0)  # far louder than it can go
    peak = measure_file(out, out.name)["peak"]
    assert peak <= -0.95
    assert result < 0.0


def test_turning_down_and_24_bit_stereo_unsaved(kit):
    repo = Repo(kit)
    before = lufs(kit / "808s" / "new.wav")
    out, gain, result = level(repo, "808s/new.wav", before - 6)
    assert out.name == "new levelled.wav"  # not saved: written beside it
    assert lufs(kit / "808s" / "new.wav") == before
    assert lufs(out) == pytest.approx(before - 6, abs=0.15)


def test_a_sound_already_close_is_left_alone(kit):
    repo = Repo(kit)
    current = lufs(kit / "Kicks" / "kick 0.wav")
    original = (kit / "Kicks" / "kick 0.wav").read_bytes()
    assert level(repo, "Kicks/kick 0.wav", current + 0.2)[0] is None
    assert (kit / "Kicks" / "kick 0.wav").read_bytes() == original


def test_silence_cannot_be_levelled(kit):
    write(kit / "silence.wav", [0.0] * 4410)
    with pytest.raises(KitError, match="silent"):
        level(Repo(kit), "silence.wav", -14)


def test_kit_level_brings_the_far_one_to_its_kind(kit, monkeypatch, capsys):
    monkeypatch.chdir(kit)
    main(["level"])
    out = capsys.readouterr().out
    assert "Turned quiet kick.wav up" in out
    assert "kick 0.wav" not in out  # only the far one
    main(["loudness"])
    assert "much quieter" not in capsys.readouterr().out


def test_kit_level_to_a_number(kit, monkeypatch, capsys):
    monkeypatch.chdir(kit)
    main(["level", "Kicks", "--to", "-18"])
    for path in (kit / "Kicks").glob("*.wav"):
        assert lufs(path) == pytest.approx(-18, abs=0.6)  # "close enough" is within 0.5 LU


def test_the_app_levels_to_the_middle_of_the_kind(kit):
    app = App(kit)
    app.auto_sort = True
    app.sort_soon()
    for _ in range(200):
        if not app.sorting:
            break
        time.sleep(0.05)
    sounds = {s["path"]: s for s in app.state()["sounds"]}
    assert sounds["Kicks/quiet kick.wav"]["level"] == "much quieter"
    middle = sounds["Kicks/quiet kick.wav"]["middle"]
    message = app.act("level", {"path": "Kicks/quiet kick.wav"})["message"]
    assert message.startswith("Turned quiet kick.wav up") and message.endswith("Before plays the original.")
    assert lufs(kit / "Kicks" / "quiet kick.wav") == pytest.approx(middle, abs=0.15)
    with pytest.raises(KitError, match="not 3 measured"):
        app.act("level", {"path": "808s/new.wav"})
