"""The key of a melodic loop, found from its audio."""

import math
import random

import pytest

from kit.app import App
from kit.audio import key_of
from kit.repo import Repo

from test_note import an_808
from test_tempo import RATE, drums, write

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJOR, MINOR = [0, 2, 4, 5, 7, 9, 11], [0, 2, 3, 5, 7, 8, 10]


def hz(midi):
    return 440 * 2 ** ((midi - 69) / 12)


def chord_loop(tonic, mode, progression, bpm=140, bars=2, seed=0, with_drums=False):
    """Four chords (triads on scale degrees), a bass on each chord's root, and a simple melody."""
    rnd = random.Random(seed)
    scale = MAJOR if mode == "maj" else MINOR
    beat = 60 / bpm
    total = int(bars * 4 * beat * RATE)
    out = [0.0] * total
    root = 48 + tonic
    chord_len = bars * 4 * beat / len(progression)
    for c, degree in enumerate(progression):
        notes = [root + scale[(degree + i) % 7] + 12 * ((degree + i) // 7) for i in (0, 2, 4)]
        bass = root - 12 + scale[degree % 7]
        start, length = int(c * chord_len * RATE), int(chord_len * RATE)
        for i in range(length):
            t = i / RATE
            pad = sum(math.sin(2 * math.pi * hz(m) * k * t) / k ** 1.2 for m in notes for k in (1, 2, 3))
            out[start + i] += (0.2 * pad + 0.35 * math.sin(2 * math.pi * hz(bass) * t)) * min(1, t / 0.01)
        for b in range(int(chord_len / beat)):
            m = root + 12 + scale[rnd.choice([degree, degree + 2, degree + 4]) % 7]
            first = start + int(b * beat * RATE)
            for i in range(min(int(beat * RATE * 0.9), total - first)):
                out[first + i] += 0.2 * math.sin(2 * math.pi * hz(m) * i / RATE) * math.exp(-i / RATE * 6)
    if with_drums:
        for b in range(bars * 4):
            for i in range(int(0.12 * RATE)):
                s = int(b * beat * RATE) + i
                out[s] += 0.4 * rnd.uniform(-1, 1) * math.exp(-i / RATE / 0.03)
    top = max(map(abs, out))
    return [x / top * 0.9 for x in out]


@pytest.mark.parametrize("tonic, mode, progression, drums_too", [
    ("C", "maj", [0, 5, 3, 4], False),     # I-vi-IV-V
    ("G", "maj", [0, 4, 5, 3], True),      # I-V-vi-IV, with a hi-hat
    ("A", "min", [0, 5, 2, 6], False),     # i-VI-III-VII: also vi-IV-I-V in C major; the bass decides
    ("F#", "min", [0, 5, 3, 4], True),     # i-VI-iv-v
    ("D#", "min", [0, 5, 2, 6], False),
    ("A#", "maj", [0, 3, 4, 0], False),    # I-IV-V-I
])
def test_the_key_of_a_chord_loop_is_found(tonic, mode, progression, drums_too):
    loop = chord_loop(NOTES.index(tonic), mode, progression, with_drums=drums_too)
    assert key_of(loop, RATE) == f"{tonic} {mode}"


def test_sounds_without_a_key_get_none(tmp_path):
    rnd = random.Random(3)
    assert key_of([rnd.uniform(-0.5, 0.5) for _ in range(RATE * 3)], RATE) is None   # noise
    assert key_of([0.0] * RATE * 3, RATE) is None                                     # silence
    assert key_of([0.1] * (RATE // 2), RATE) is None                                  # too short
    drums(tmp_path / "groove.wav", 128, bars=2)                                       # a tuned kick, nothing else
    from kit.audio import key
    assert key(tmp_path / "groove.wav", "groove.wav") is None


def test_the_app_gives_loops_a_key_and_one_shots_a_note(kit):
    Repo.init(kit, author="Nojan")
    (kit / "Loops").mkdir()
    write(kit / "Loops" / "bells.wav", chord_loop(NOTES.index("F#"), "min", [0, 5, 3, 4]))
    write(kit / "Loops" / "named 140bpm Cmaj.wav", chord_loop(NOTES.index("F#"), "min", [0, 5, 3, 4]))
    an_808(kit / "Kicks" / "low.wav", 31)
    app = App(kit)
    bells = app.listen({"path": "Loops/bells.wav"})
    assert bells["key"] == "F# min" and bells["note"] is None
    assert app.listen({"path": "Loops/named 140bpm Cmaj.wav"}) == {"bpm": None, "key": None, "note": None}  # the name says
    assert app.listen({"path": "Kicks/low.wav"}) == {"bpm": None, "key": None, "note": "G"}


def test_a_bright_one_shot_keeps_its_note(kit):
    # Overtones put energy on other notes (the fifth, the third), but not enough
    # to count as three notes sounding: one note is still a note, not a key.
    Repo.init(kit, author="Nojan")
    tone = [0.4 * sum(math.sin(2 * math.pi * hz(64) * k * i / RATE) / k for k in range(1, 17)) * math.exp(-i / RATE)
            for i in range(2 * RATE)]
    write(kit / "Kicks" / "stab.wav", tone)
    assert App(kit).listen({"path": "Kicks/stab.wav"}) == {"bpm": None, "key": None, "note": "E"}
