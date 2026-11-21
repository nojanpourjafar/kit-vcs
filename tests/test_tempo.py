"""Tempo estimated from audio."""

import math
import random
import struct
import wave

import pytest

from kit.audio import tempo
from kit.app import App
from kit.repo import Repo

RATE = 22050


def drums(path, bpm, bars=2, width=2, channels=1, tail=0.0, seed=0):
    """A simple drum loop: a kick on every beat, a quieter hat between, a little noise."""
    rnd = random.Random(seed)
    beat = 60 / bpm
    total = int(RATE * (bars * 4 * beat + tail))
    out = [rnd.gauss(0, 0.01) for _ in range(total)]
    for b in range(bars * 4):
        for start, gain, length, decay in ((b * beat, 0.9, 0.25, 0.08), ((b + 0.5) * beat, 0.3, 0.04, 0.01)):
            first = int(start * RATE)
            for i in range(int(length * RATE)):
                if first + i < total:
                    out[first + i] += gain * math.exp(-i / (decay * RATE)) * math.sin(2 * math.pi * 60 * i / RATE + rnd.random())
    write(path, out, width, channels)


def write(path, samples, width=2, channels=1):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(RATE)
        data = bytearray()
        for x in samples:
            x = max(-1.0, min(1.0, x))
            one = struct.pack("<h", int(x * 32767)) if width == 2 else int(x * 8388607).to_bytes(3, "little", signed=True)
            data += one * channels
        wav.writeframes(bytes(data))


@pytest.mark.parametrize("bpm, bars, width, channels, tail", [
    (90, 2, 2, 1, 0.0),
    (140, 2, 3, 2, 0.0),     # 24-bit stereo
    (128, 1, 2, 1, 0.0),     # one bar, under two seconds
    (100, 2, 2, 1, 0.37),    # not cut to whole bars
    (160, 4, 2, 2, 0.0),
])
def test_the_tempo_of_a_loop_is_found(tmp_path, bpm, bars, width, channels, tail):
    drums(tmp_path / "loop.wav", bpm, bars, width, channels, tail)
    assert tempo(tmp_path / "loop.wav", "loop.wav") == pytest.approx(bpm, abs=1)


def test_very_fast_loops_may_come_out_halved(tmp_path):
    # A known limit, not a wish: at drum-and-bass tempos the beat and half the
    # beat line up almost equally well, and either answer can come out.
    drums(tmp_path / "dnb.wav", 174, bars=4, channels=2)
    assert tempo(tmp_path / "dnb.wav", "dnb.wav") in (pytest.approx(174, abs=1), pytest.approx(87, abs=1))


def test_sounds_without_a_beat_get_no_tempo(tmp_path):
    rnd = random.Random(1)
    write(tmp_path / "808.wav", [0.9 * math.exp(-i / (0.6 * RATE)) * math.sin(2 * math.pi * 50 * i / RATE)
                                 for i in range(int(1.5 * RATE))])
    write(tmp_path / "noise.wav", [rnd.gauss(0, 0.3) for _ in range(3 * RATE)])
    write(tmp_path / "silence.wav", [0.0] * (3 * RATE))
    write(tmp_path / "short.wav", [rnd.gauss(0, 0.3) for _ in range(RATE // 2)])
    write(tmp_path / "pad.wav", [0.5 * math.sin(2 * math.pi * 220 * i / RATE) * (1 + 0.1 * math.sin(i / RATE))
                                 for i in range(4 * RATE)])
    for name in ["808.wav", "noise.wav", "silence.wav", "short.wav", "pad.wav"]:
        assert tempo(tmp_path / name, name) is None, name
    assert tempo(tmp_path / "808.wav", "808.mp3") is None          # only WAV files are read


def test_the_app_estimates_tempo_once(kit, monkeypatch):
    Repo.init(kit, author="Nojan")
    drums(kit / "Kicks" / "groove.wav", 95)
    app = App(kit)
    calls = []
    real = App.tempo.__globals__["tempo"]
    monkeypatch.setitem(App.tempo.__globals__, "tempo", lambda path, name: calls.append(name) or real(path, name))
    assert app.tempo({"path": "Kicks/groove.wav"}) == pytest.approx(95, abs=1)
    assert app.tempo({"path": "Kicks/groove.wav"}) == pytest.approx(95, abs=1)
    assert calls == ["Kicks/groove.wav"]                            # the second answer came from memory
