"""Chopping a loop into one-shots."""

import random
import struct

import pytest

from kit.chop import chop, onsets
from kit.cli import main
from kit.repo import KitError, Repo
from kit.wavfile import open_wav

from sounds_by_kind import KINDS as MAKERS
from test_app import app  # noqa: F401  (the running app, used as a fixture)

RATE = 44100


def drum_loop(bpm=120, seed=0):
    """One bar: kick, hat, snare, hat... on eighth notes. Returns (samples, where each hit starts)."""
    rnd = random.Random(seed)
    sounds = {k: MAKERS[k](rnd) for k in ("Kick", "Snare", "Hat")}
    step = 60 / bpm / 2
    pattern = ["Kick", "Hat", "Snare", "Hat", "Kick", "Kick", "Snare", "Hat"]
    out = [0.0] * int(len(pattern) * step * RATE)
    starts = []
    for n, kind in enumerate(pattern):
        start = int(n * step * RATE)
        starts.append(start)
        gain = {"Kick": 0.9, "Snare": 0.7, "Hat": 0.3}[kind]
        for i, x in enumerate(sounds[kind][:len(out) - start]):
            out[start + i] += gain * x
    top = max(map(abs, out))
    return [x / top * 0.9 for x in out], starts


def write(path, samples, channels=1, width=2, is_float=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if is_float:
        data = b"".join(struct.pack("<f", x) * channels for x in samples)
    else:
        top = (1 << (8 * width - 1)) - 1
        data = b"".join(int(x * top).to_bytes(width, "little", signed=True) * channels for x in samples)
    fmt = struct.pack("<HHIIHH", 3 if is_float else 1, channels, RATE, RATE * channels * width, channels * width, width * 8)
    body = b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(data)) + data
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


def test_each_hit_is_found_just_before_it_starts():
    samples, starts = drum_loop()
    found = onsets(samples, RATE)
    assert len(found) == len(starts)
    for got, real in zip(found, starts):
        assert -0.012 * RATE <= got - real <= 0.005 * RATE     # never more than 5 ms late: the attack is kept


@pytest.mark.parametrize("channels, width, is_float", [(1, 2, False), (2, 3, False), (2, 4, True)])
def test_the_pieces_are_the_loop_cut_up(tmp_path, channels, width, is_float):
    samples, starts = drum_loop()
    loop = write(tmp_path / "Loops" / "Groove.wav", samples, channels, width, is_float)
    pieces = chop(loop, "Loops/Groove.wav")
    assert [p.name for p in pieces] == [f"Groove {n:02d}.wav" for n in range(1, len(starts) + 1)]
    assert pieces[0].parent == tmp_path / "Loops" / "Groove chops"
    frames = 0
    for piece in pieces:
        with open_wav(piece) as wav:
            assert (wav.rate, wav.channels, wav.width, wav.is_float) == (RATE, channels, width, is_float)
            frames += wav.frames
            data = wav.readframes(wav.frames)
        assert data[-channels * width:] == bytes(channels * width) or is_float   # faded to silence: no click
    with open_wav(loop) as wav:
        assert frames == wav.frames - onsets(samples, RATE)[0]    # every frame from the first hit on, once


def test_chopping_again_never_overwrites(tmp_path):
    loop = write(tmp_path / "Groove.wav", drum_loop()[0])
    chop(loop, "Groove.wav")
    again = chop(loop, "Groove.wav")
    assert again[0].parent.name == "Groove chops 2"


def test_what_cannot_be_chopped(tmp_path):
    one_hit = write(tmp_path / "kick.wav", MAKERS["Kick"](random.Random(1)))
    with pytest.raises(KitError, match="fewer than two hits"):
        chop(one_hit, "kick.wav")
    (tmp_path / "loop.mp3").write_bytes(b"not decodable here")
    with pytest.raises(KitError, match="only chop"):
        chop(tmp_path / "loop.mp3", "loop.mp3")
    assert not (tmp_path / "kick chops").exists()


def test_the_command_and_the_app(kit, monkeypatch, capsys, app):
    write(kit / "Loops" / "Groove 120bpm.wav", drum_loop()[0])
    Repo.init(kit, author="Nojan")
    monkeypatch.chdir(kit)
    main(["chop", "Loops/Groove 120bpm.wav"])
    assert "Cut Loops/Groove 120bpm.wav into 8 pieces, in Loops/Groove 120bpm chops/" in capsys.readouterr().out
    message = app.json("/api/chop", {"path": "Loops/Groove 120bpm.wav"})["message"]
    assert "8 pieces, in Loops/Groove 120bpm chops 2/" in message
    paths = {s["path"] for s in app.json("/api/state")["sounds"]}
    assert "Loops/Groove 120bpm chops 2/Groove 120bpm 01.wav" in paths      # new sounds, ready to save
    assert app("/api/chop", {"path": ".kit/HEAD"})[0] == 400
