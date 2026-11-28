"""Reversing sounds, and stretching loops to another tempo at the same pitch."""

import wave

import pytest

from kit.app import App
from kit.audio import tempo
from kit.cli import main
from kit.repo import KitError, Repo
from kit.stretch import reverse, stretch
from kit.tune import pitch

from test_note import an_808
from test_tempo import drums


@pytest.fixture
def kit(tmp_path):
    folder = tmp_path / "Pack"
    folder.mkdir()
    drums(folder / "Groove 140bpm.wav", 140, bars=2)
    drums(folder / "Wide 128bpm.wav", 128, bars=2, channels=2, width=3)
    an_808(folder / "808.wav", 33)
    Repo.init(folder, author="Test").commit("first")
    return folder


def frames(path):
    with wave.open(str(path)) as w:
        return w.getnframes(), w.getnchannels(), w.getsampwidth()


@pytest.mark.parametrize("source, now, new", [("Groove 140bpm.wav", 140, 150), ("Groove 140bpm.wav", 140, 110),
                                              ("Wide 128bpm.wav", 128, 100)])
def test_a_stretched_loop_plays_at_the_new_tempo(kit, source, now, new):
    out, was = stretch(Repo(kit), source, new)
    assert was == now
    assert out.name == f"{source.split()[0]} {new}bpm.wav"  # the old tempo in the name is replaced
    assert tempo(out, out.name) == pytest.approx(new, abs=1)
    before, after = frames(kit / source), frames(out)
    assert after[1:] == before[1:]  # same channels and bit depth
    assert after[0] == pytest.approx(before[0] * now / new, abs=2)  # exactly the new length


def test_stretching_keeps_the_pitch(kit):
    repo = Repo(kit)
    out, _ = stretch(repo, "808.wav", 150, 120)
    assert pitch(repo, out.relative_to(kit).as_posix()) == pytest.approx(pitch(repo, "808.wav"), rel=0.005)


def test_stretch_refusals(kit):
    repo = Repo(kit)
    with pytest.raises(KitError, match="too far"):
        stretch(repo, "Groove 140bpm.wav", 50)
    with pytest.raises(KitError, match="already at"):
        stretch(repo, "Groove 140bpm.wav", 140)
    with pytest.raises(KitError, match="cannot tell the tempo"):
        stretch(repo, "808.wav", 120)


def test_reverse_plays_backwards_and_keeps_the_format(kit):
    out = reverse(Repo(kit), "Wide 128bpm.wav")
    assert out.name == "Wide 128bpm reversed.wav"
    with wave.open(str(kit / "Wide 128bpm.wav")) as a, wave.open(str(out)) as b:
        assert (a.getnframes(), a.getnchannels(), a.getsampwidth()) == (b.getnframes(), b.getnchannels(), b.getsampwidth())
        first, last = a.readframes(1), None
        b.setpos(b.getnframes() - 1)
        last = b.readframes(1)
    assert first == last


def test_from_the_app_and_the_terminal(kit, monkeypatch, capsys):
    app = App(kit)
    assert app.act("reverse", {"path": "808.wav"})["message"].startswith("Made 808 reversed.wav")
    message = app.act("stretch", {"path": "Groove 140bpm.wav", "bpm": 150})["message"]
    assert message.startswith("Stretched Groove 140bpm.wav from 140 to 150 BPM at the same pitch: Groove 150bpm.wav")
    monkeypatch.chdir(kit)
    main(["stretch", "Groove 140bpm.wav", "120"])
    assert "Stretched from 140 to 120 BPM: Groove 120bpm.wav" in capsys.readouterr().out
    main(["reverse", "Groove 140bpm.wav"])
    assert "Groove 140bpm reversed.wav" in capsys.readouterr().out
