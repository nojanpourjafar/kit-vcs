"""Tempo and key read from file names."""

import pytest

from kit.app import App
from kit.repo import Repo
from kit.tags import from_name


@pytest.mark.parametrize("path, bpm, key", [
    ("Loops/Dark Loop 140bpm F#m.wav", 140, "F# min"),
    ("melody_92_BPM_Cmaj.wav", 92, "C maj"),
    ("Pads/A minor - 90 bpm.wav", 90, "A min"),
    ("bpm140_ebm.wav", 140, "Eb min"),
    ("pluck c#minor 160bpm.wav", 160, "C# min"),
    ("Bb_minor_loop.wav", None, "Bb min"),
    ("Keys/D major/chords 75 BPM.wav", 75, "D maj"),     # the key comes from the folder
    ("Loops/140 BPM/hat loop.wav", 140, None),
    ("Loop - Am.wav", None, "A min"),
    ("Snare Fmin 85bpm.wav", 85, "F min"),
])
def test_tempo_and_key_are_read(path, bpm, key):
    assert from_name(path) == {"bpm": bpm, "key": key}


@pytest.mark.parametrize("path", [
    "Drums/kick_01.wav",        # numbers without "bpm" are not tempos
    "Bass/Bass Loop 120.wav",
    "808s/808_F.wav",           # a note without a mode is not a key
    "Drum Loop 1400bpm.wav",    # too many digits
    "Fast 20bpm.wav",           # out of range
    "Am I Dreaming.wav",        # English, not A minor
    "BRAZIL.wav", "Bass.wav", "Drum fill.wav",
])
def test_nothing_is_guessed(path):
    assert from_name(path) == {"bpm": None, "key": None}


def test_the_file_name_wins_over_its_folder():
    assert from_name("Loops/120 BPM/Am/riff 140bpm Cm.wav") == {"bpm": 140, "key": "C min"}


def test_the_app_sends_tempo_and_key(kit):
    Repo.init(kit, author="Nojan")
    (kit / "Loops").mkdir()
    (kit / "Loops" / "Dark Loop 140bpm F#m.wav").write_bytes(b"LOOP")
    sounds = {s["path"]: s for s in App(kit).state()["sounds"]}
    assert sounds["Loops/Dark Loop 140bpm F#m.wav"]["bpm"] == 140
    assert sounds["Loops/Dark Loop 140bpm F#m.wav"]["key"] == "F# min"
    assert sounds["Kicks/kick_01.wav"]["bpm"] is None
