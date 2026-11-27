"""Finding problems in sounds: clipping, late starts, clicks and silence."""

import math
import random
import struct
import wave
from array import array

import pytest

from kit import problems
from kit.app import App
from kit.cli import main
from kit.problems import check, find
from kit.repo import Repo

from sounds_by_kind import KINDS
from test_tempo import drums

RATE = 44100


def faded(samples, ms=5):
    """The sound with a short fade-out, the way a clean one-shot ends."""
    samples = list(samples)
    n = int(RATE * ms / 1000)
    for i in range(n):
        samples[-n + i] *= 1 - (i + 1) / n
    return samples


def write(path, samples, width=2, channels=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(RATE)
        top = (1 << (8 * width - 1)) - 1
        out.writeframes(b"".join(int(max(-1, min(1, x)) * top).to_bytes(width, "little", signed=True) * channels
                                 for x in samples))
    return path


def kinds_of(samples, loop=False):
    return {problem["kind"] for problem in find([array("d", samples)], RATE, loop)}


@pytest.mark.parametrize("kind", ["Kick", "808", "Snare", "Clap", "Hat", "Perc", "Melodic"])
def test_clean_one_shots_have_no_problems(kind):
    rnd = random.Random(4)
    for _ in range(3):
        assert kinds_of(faded(KINDS[kind](rnd))) == set()


@pytest.mark.parametrize("kind", ["Kick", "808", "Snare", "Clap", "Hat", "Perc", "Melodic"])
def test_each_problem_is_found(kind):
    clean = faded(KINDS[kind](random.Random(5)))
    peak = max(map(abs, clean))
    assert "clipping" in kinds_of([max(-1, min(1, x / peak * 2.5)) for x in clean])
    assert "late" in kinds_of([0.0] * int(RATE * 0.02) + clean)
    assert "click" in kinds_of(clean[: int(len(clean) * 0.1)])


def test_a_single_loud_peak_is_not_clipping():
    sound = faded([0.999 if i == 100 else 0.5 * math.sin(i / 20) for i in range(RATE // 2)])
    assert kinds_of(sound) == set()


def test_a_float_file_past_full_scale_clips():
    found = find([array("d", faded([1.3 * math.sin(i / 20) * math.exp(-i / RATE / 0.1) for i in range(RATE)]))],
                 RATE, False)
    assert [problem["kind"] for problem in found] == ["clipping"]


def test_silence_is_only_reported_as_silence():
    assert kinds_of([0.0] * RATE) == {"silent"}


def test_a_short_gap_before_a_one_shot_is_fine():
    clean = faded(KINDS["Kick"](random.Random(1)))
    assert kinds_of([0.0] * int(RATE * 0.005) + clean) == set()


def test_loops_are_judged_by_their_seam_not_their_start():
    secs = 4.0
    hz = round(220 * secs) / secs  # a whole number of waves, so the end meets the start
    seamless = [0.4 * math.sin(2 * math.pi * hz * t / RATE) for t in range(int(secs * RATE))]
    assert kinds_of(seamless, loop=True) == set()
    assert kinds_of([0.0] * int(RATE * 0.1) + seamless, loop=True) == set()  # a rest at the start is music
    cut = [0.4 * math.sin(2 * math.pi * 223 * t / RATE + 0.7) for t in range(int(secs * RATE * 0.93))]
    assert kinds_of(cut, loop=True) == {"click"}


def test_a_drum_loop_starting_on_its_kick_does_not_click(tmp_path):
    for bpm, seed in ((90, 0), (128, 1), (140, 2), (170, 3)):
        path = tmp_path / f"loop{bpm}.wav"
        drums(path, bpm, seed=seed)
        assert check(path, path.name, loop=True) == []


def test_stereo_24_bit_files_are_read(tmp_path):
    clean = faded(KINDS["808"](random.Random(2)))
    assert check(write(tmp_path / "a.wav", clean, width=3, channels=2), "a.wav") == []
    found = check(write(tmp_path / "b.wav", clean[: len(clean) // 4], width=3, channels=2), "b.wav")
    assert [problem["kind"] for problem in found] == ["click"]


def test_files_kit_cannot_read_are_not_checked(tmp_path):
    (tmp_path / "a.mp3").write_bytes(b"ID3" + b"\0" * 100)
    (tmp_path / "b.wav").write_bytes(b"not audio")
    assert check(tmp_path / "a.mp3", "a.mp3") is None
    assert check(tmp_path / "b.wav", "b.wav") is None


@pytest.fixture
def kit(tmp_path):
    folder = tmp_path / "Pack"
    rnd = random.Random(7)
    write(folder / "Kicks" / "clean.wav", faded(KINDS["Kick"](rnd)))
    write(folder / "Kicks" / "late.wav", [0.0] * 2000 + faded(KINDS["Kick"](rnd)))
    hot = faded(KINDS["Snare"](rnd))
    write(folder / "Snares" / "hot.wav", [x * 3 for x in hot])
    Repo.init(folder, author="Test")
    return folder


def test_kit_check_lists_the_sounds_with_problems(kit, monkeypatch, capsys):
    monkeypatch.chdir(kit)
    main(["check"])
    out = capsys.readouterr().out
    assert "Kicks/late.wav\n  Starts late: 45 ms" in out
    assert "Snares/hot.wav\n  Clips" in out
    assert "clean.wav" not in out
    assert "2 sound(s) with problems." in out
    main(["check", "Kicks"])
    assert "hot.wav" not in capsys.readouterr().out


def test_each_sound_is_checked_once(kit, monkeypatch):
    repo = Repo(kit)
    problems.check_kit(repo, set())
    monkeypatch.setattr(problems, "check", lambda *args: pytest.fail("checked again"))
    assert set(problems.check_kit(repo, set())) == {"Kicks/late.wav", "Snares/hot.wav"}
    assert problems.unchecked(repo, repo.working(), set()) == 0


def test_the_app_shows_problems_after_checking_in_the_background(kit):
    app = App(kit)
    assert all(sound["problems"] == [] for sound in app.state()["sounds"])  # not checked yet
    app.auto_sort = True
    app.sort_soon()
    for _ in range(200):
        if not app.sorting:
            break
        import time
        time.sleep(0.05)
    found = {sound["path"]: sound["problems"] for sound in app.state()["sounds"]}
    assert found["Kicks/clean.wav"] == []
    assert found["Kicks/late.wav"][0].startswith("Starts late")
    assert found["Snares/hot.wav"][0].startswith("Clips")
