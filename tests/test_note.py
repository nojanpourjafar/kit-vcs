"""The note a one-shot plays, found from its audio."""

import json
import math
import random

import pytest

from kit.app import App
from kit.audio import note
from kit.repo import Repo

from test_app import app  # noqa: F401  (the running app, used as a fixture)
from test_tempo import RATE, drums, write

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def hz(midi, cents=0):
    """The frequency of a MIDI note (69 is the A at 440 Hz), detuned by `cents`."""
    return 440 * 2 ** ((midi - 69 + cents / 100) / 12)


def an_808(path, midi, cents=0, drive=0.0, slide=1.6, channels=1, seconds=1.2):
    """A sine that starts high and drops quickly onto the note, then fades. `drive` distorts it."""
    out, phase = [], 0.0
    for i in range(int(seconds * RATE)):
        t = i / RATE
        phase += 2 * math.pi * hz(midi, cents) * (1 + (slide - 1) * math.exp(-t / 0.015)) / RATE
        x = math.sin(phase) * math.exp(-t / 0.5)
        out.append(math.tanh(x * (1 + 4 * drive)) / math.tanh(1 + 4 * drive) * 0.9 if drive else 0.9 * x)
    write(path, out, channels=channels)


def a_synth(path, midi, harmonics=8):
    """A bright synth note: the note plus its harmonics, with a quick attack and a fade."""
    out = []
    for i in range(int(RATE * 0.8)):
        t = i / RATE
        x = sum(math.sin(2 * math.pi * hz(midi) * k * t) / k for k in range(1, harmonics + 1))
        out.append(0.4 * x * min(1, t / 0.005) * math.exp(-t / 0.6))
    write(path, out, channels=2)


@pytest.mark.parametrize("midi, cents, drive, slide", [
    (29, 0, 0.0, 1.6),     # F1, the low end of an 808
    (30, 0, 0.0, 1.0),     # F#1 with no slide
    (36, 25, 0.0, 2.0),    # C2, a quarter of a semitone sharp, a big slide
    (41, -30, 0.6, 1.6),   # F2, flat and distorted
    (45, 10, 1.5, 1.6),    # A2, heavily distorted: the harmonics are louder than the note
])
def test_the_note_of_an_808_is_found(tmp_path, midi, cents, drive, slide):
    an_808(tmp_path / "808.wav", midi, cents, drive, slide)
    assert note(tmp_path / "808.wav", "808.wav") == NOTES[midi % 12]


@pytest.mark.parametrize("midi, harmonics", [(48, 1), (55, 8), (63, 3), (70, 16), (76, 8)])
def test_the_note_of_a_melodic_one_shot_is_found(tmp_path, midi, harmonics):
    a_synth(tmp_path / "stab.wav", midi, harmonics)
    assert note(tmp_path / "stab.wav", "stab.wav") == NOTES[midi % 12]


def test_sounds_without_one_clear_note_get_none(tmp_path):
    rnd = random.Random(2)
    snare = [(rnd.uniform(-1, 1) * 0.6 + 0.2 * math.sin(2 * math.pi * 190 * i / RATE)) * math.exp(-i / (0.06 * RATE))
             for i in range(int(0.4 * RATE))]
    hat = [rnd.uniform(-0.8, 0.8) * math.exp(-i / (0.015 * RATE)) for i in range(int(0.15 * RATE))]
    # A chord repeats too, at a low period where nothing actually sounds:
    # A, C and E line up every cycle of an F two octaves below.
    chord = [0.3 * sum(math.sin(2 * math.pi * hz(m) * i / RATE) for m in (57, 60, 64)) * math.exp(-i / (0.7 * RATE))
             for i in range(RATE)]
    for name, samples in [("snare.wav", snare), ("hat.wav", hat), ("chord.wav", chord),
                          ("silence.wav", [0.0] * RATE), ("tiny.wav", [0.5, -0.5] * 50)]:
        write(tmp_path / name, samples)
        assert note(tmp_path / name, name) is None, name
    an_808(tmp_path / "808.wav", 33)
    assert note(tmp_path / "808.wav", "808.mp3") is None            # only files Kit can read


def test_the_app_gives_a_note_only_to_sounds_without_a_beat(kit):
    Repo.init(kit, author="Nojan")
    an_808(kit / "Kicks" / "low.wav", 31)                             # G1
    drums(kit / "Kicks" / "groove.wav", 95)
    app = App(kit)
    assert app.listen({"path": "Kicks/low.wav"}) == {"bpm": None, "note": "G"}
    heard = app.listen({"path": "Kicks/groove.wav"})
    assert heard["bpm"] == pytest.approx(95, abs=1) and heard["note"] is None


def test_the_app_finds_a_note_once(kit, monkeypatch):
    Repo.init(kit, author="Nojan")
    an_808(kit / "Kicks" / "low.wav", 31)
    app = App(kit)
    calls = []
    real = App.note.__globals__["note"]
    monkeypatch.setitem(App.note.__globals__, "note", lambda path, name: calls.append(name) or real(path, name))
    assert app.note({"path": "Kicks/low.wav"}) == "G"
    assert app.note({"path": "Kicks/low.wav"}) == "G"
    assert calls == ["Kicks/low.wav"]                                # the second answer came from memory


def test_the_page_asks_one_address_for_both(app, kit):
    Repo.init(kit, author="Nojan")
    an_808(kit / "Kicks" / "low.wav", 31)
    status, body, _ = app("/api/tempo?path=Kicks/low.wav")
    assert status == 200 and json.loads(body) == {"bpm": None, "note": "G"}
