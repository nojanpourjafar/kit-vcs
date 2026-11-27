"""Loudness in LUFS (ITU-R BS.1770-4), and sounds far louder or quieter than the rest of their kind."""

import math
import random
from array import array

import pytest

from kit import loudness
from kit.app import App
from kit.cli import main
from kit.loudness import _biquads, apart, measure, measure_file, middle
from kit.repo import Repo

from sounds_by_kind import KINDS
from test_problems import faded, write


def tone(db, seconds=3.0, rate=48000, hz=997):
    gain = 10 ** (db / 20)
    return array("d", (gain * math.sin(2 * math.pi * hz * i / rate) for i in range(int(seconds * rate))))


def test_the_filters_at_48k_are_the_ones_in_the_standard():
    shelf, high_pass = _biquads(48000)
    assert shelf == pytest.approx((1.53512485958697, -2.69169618940638, 1.19839281085285,
                                   -1.69065929318241, 0.73248077421585), abs=1e-8)
    assert high_pass == pytest.approx((1.0, -2.0, 1.0, -1.99004745483398, 0.99007225036621), abs=1e-8)


@pytest.mark.parametrize("rate", [44100, 48000, 96000])
def test_a_stereo_tone_at_minus_23_dbfs_is_minus_23_lufs(rate):
    # The standard's own check (EBU Tech 3341, case 1): a 997 Hz sine at -23 dBFS in both channels.
    left = tone(-23, rate=rate)
    assert measure([left, left], rate)["lufs"] == pytest.approx(-23.0, abs=0.1)


def test_a_mono_tone_is_3_lu_quieter_than_the_same_in_stereo():
    left = tone(-20)
    assert measure([left], 48000)["lufs"] == pytest.approx(measure([left, left], 48000)["lufs"] - 3.0, abs=0.1)


def test_silence_around_a_sound_barely_changes_its_loudness():
    # 2 s of sound in 6 s of file: a plain average would be 4.8 dB quieter. The
    # gates leave the silent blocks out; only blocks half over the edges remain.
    sound = tone(-18, seconds=2)
    padded = array("d", [0.0] * 96000) + sound + array("d", [0.0] * 96000)
    assert measure([padded], 48000)["lufs"] == pytest.approx(measure([sound], 48000)["lufs"], abs=1.0)


def test_the_peak_is_the_loudest_sample():
    assert measure([tone(-6)], 48000)["peak"] == pytest.approx(-6.0, abs=0.05)


def test_short_one_shots_and_silence():
    rnd = random.Random(2)
    hat = array("d", KINDS["Hat"](rnd)[: 4410])  # 0.1 s, shorter than one 400 ms block
    assert -60 < measure([hat], 44100)["lufs"] < 0
    assert measure([array("d", [0.0] * 44100)], 44100) is None


def test_the_middle_and_far_off():
    assert middle([-10, -12, -30]) == -12
    assert middle([-10, -12]) == -11
    assert middle([]) is None
    assert apart(-5, -12) == "much louder"
    assert apart(-19, -12) == "much quieter"
    assert apart(-14, -12) is None
    assert apart(-5, None) is None


@pytest.fixture
def kit(tmp_path):
    """Four kicks, one of them turned down by 12 dB, and a hat."""
    folder = tmp_path / "Pack"
    rnd = random.Random(5)
    for i in range(3):
        write(folder / "Kicks" / f"kick {i}.wav", faded(KINDS["Kick"](rnd)))
    write(folder / "Kicks" / "quiet kick.wav", [x * 10 ** (-12 / 20) for x in faded(KINDS["Kick"](rnd))])
    write(folder / "Hats" / "hat.wav", faded(KINDS["Hat"](rnd)))
    Repo.init(folder, author="Test")
    return folder


def test_each_sound_is_measured_once(kit, monkeypatch):
    repo = Repo(kit)
    first = loudness.measure_kit(repo)
    assert len(first) == 5
    monkeypatch.setattr(loudness, "measure_file", lambda *args: pytest.fail("measured again"))
    assert loudness.measure_kit(repo) == first
    assert loudness.unmeasured(repo, repo.working()) == 0


def test_turning_a_sound_down_12_db_makes_it_12_lu_quieter(kit, tmp_path):
    rnd = random.Random(9)
    sound = faded(KINDS["808"](rnd))
    loud = measure_file(write(tmp_path / "a.wav", sound), "a.wav")["lufs"]
    quiet = measure_file(write(tmp_path / "b.wav", [x * 10 ** (-12 / 20) for x in sound]), "b.wav")["lufs"]
    assert loud - quiet == pytest.approx(12, abs=0.2)


def test_kit_loudness_lists_by_kind_and_marks_the_quiet_one(kit, monkeypatch, capsys):
    monkeypatch.chdir(kit)
    main(["loudness"])
    out = capsys.readouterr().out
    assert "Kick (middle" in out and "Hat\n" in out
    quiet_line = next(line for line in out.splitlines() if "quiet kick.wav" in line)
    assert quiet_line.endswith("<- much quieter")
    assert not any(line.endswith("much louder") for line in out.splitlines())


def test_the_app_shows_loudness_after_measuring_in_the_background(kit):
    app = App(kit)
    assert all(sound["lufs"] is None for sound in app.state()["sounds"])
    app.auto_sort = True
    app.sort_soon()
    import time
    for _ in range(200):
        if not app.sorting:
            break
        time.sleep(0.05)
    sounds = {sound["path"]: sound for sound in app.state()["sounds"]}
    assert all(isinstance(sound["lufs"], float) for sound in sounds.values())
    assert sounds["Kicks/quiet kick.wav"]["level"] == "much quieter"
    assert sounds["Kicks/kick 0.wav"]["level"] is None
    assert sounds["Hats/hat.wav"]["level"] is None  # one hat: nothing to compare it with
