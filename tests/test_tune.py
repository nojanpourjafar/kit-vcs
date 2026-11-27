"""Tuning a one-shot to a note, the way a sampler does."""

import math
import random
import wave

import pytest

from kit.app import App
from kit.audio import NOTE_NAMES
from kit.cli import main
from kit.repo import KitError, Repo
from kit.tune import pitch, resample, shift_for, tune

from sounds_by_kind import KINDS
from test_note import an_808
from test_problems import faded, write
from test_tempo import drums


def cents_from(hz, note):
    exact = 69 + 12 * math.log2(hz / 440)
    return abs(((exact - NOTE_NAMES.index(note) + 6) % 12) - 6) * 100


def test_the_shift_goes_to_the_nearest_octave():
    a = 440.0
    assert shift_for(a, "A") == pytest.approx(0)
    assert shift_for(a, "B") == pytest.approx(2)
    assert shift_for(a, "G") == pytest.approx(-2)
    assert shift_for(a, "D#") == pytest.approx(6) or shift_for(a, "D#") == pytest.approx(-6)
    assert shift_for(a * 2 ** (30 / 1200), "A") == pytest.approx(-0.3)  # 30 cents sharp: corrected


def test_resampling_an_octave_up_halves_the_length():
    values = [math.sin(i / 10) for i in range(1000)]
    assert len(resample(values, 1, 2.0)) == 499
    stereo = [v for x in values for v in (x, -x)]
    out = resample(stereo, 2, 0.5)
    assert len(out) == 2 * 1998 and out[0] == -out[1]


@pytest.fixture
def kit(tmp_path):
    folder = tmp_path / "Pack"
    (folder / "808s").mkdir(parents=True)
    an_808(folder / "808s" / "Glide.wav", 31, cents=25)             # G, 25 cents sharp
    an_808(folder / "808s" / "Wide.wav", 38, channels=2, drive=0.5)  # D, stereo, distorted
    drums(folder / "groove 128bpm.wav", 128)
    write(folder / "hat.wav", faded(KINDS["Hat"](random.Random(1))))
    Repo.init(folder, author="Test").commit("first")
    return folder


@pytest.mark.parametrize("note", ["F#", "A", "C", "D#", "Bb"])
def test_a_tuned_808_plays_the_note(kit, note):
    repo = Repo(kit)
    out, played, shift = tune(repo, "808s/Glide.wav", note)
    assert played == "G"
    assert out.name == f"Glide in {note.replace('Bb', 'A#')}.wav"
    assert cents_from(pitch(repo, out.relative_to(kit).as_posix()), note.replace("Bb", "A#")) < 6


def test_stereo_stays_stereo_and_the_original_is_untouched(kit):
    repo = Repo(kit)
    before = (kit / "808s" / "Wide.wav").read_bytes()
    out, played, shift = tune(repo, "808s/Wide.wav", "F")
    assert played == "D" and shift == pytest.approx(3, abs=0.1)
    assert (kit / "808s" / "Wide.wav").read_bytes() == before
    with wave.open(str(out)) as tuned, wave.open(str(kit / "808s" / "Wide.wav")) as original:
        assert tuned.getnchannels() == 2 and tuned.getsampwidth() == original.getsampwidth()
        assert tuned.getnframes() == pytest.approx(original.getnframes() / 2 ** (3 / 12), rel=0.01)  # higher: shorter


def test_in_tune_already_and_refusals(kit):
    repo = Repo(kit)
    tuned, _, _ = tune(repo, "808s/Glide.wav", "C")
    assert tune(repo, tuned.relative_to(kit).as_posix(), "C")[0] is None
    with pytest.raises(KitError, match="loop"):
        tune(repo, "groove 128bpm.wav", "C")
    with pytest.raises(KitError, match="no clear note"):
        tune(repo, "hat.wav", "C")
    with pytest.raises(KitError, match="not a note"):
        tune(repo, "808s/Glide.wav", "H")


def test_kit_tune_and_the_app(kit, monkeypatch, capsys):
    monkeypatch.chdir(kit)
    main(["tune", "808s/Glide.wav", "F#"])
    assert "808s/Glide.wav plays G; tuned down" in capsys.readouterr().out
    message = App(kit).act("tune", {"path": "808s/Wide.wav", "note": "E"})["message"]
    assert message.startswith("Tuned Wide.wav from D up 2.0 semitones: Wide in E.wav.")
