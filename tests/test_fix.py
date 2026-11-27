"""Fixing late starts and clicks, in place for saved sounds and beside them for the rest."""

import math
import random
import wave
from array import array

import pytest

from kit.app import App
from kit.cli import main
from kit.fix import fix, repair
from kit.problems import channels_of, check, find
from kit.repo import KitError, Repo

from sounds_by_kind import KINDS
from test_problems import RATE, faded, write


def problem_kinds(path, loop=False):
    return {problem["kind"] for problem in check(path, path.name, loop)}


def frames(path):
    with wave.open(str(path)) as wav:
        return wav.getnframes(), wav.getnchannels(), wav.getsampwidth()


@pytest.fixture
def kit(tmp_path):
    """A saved kit with a late kick and a cut-off 808, and a cut-off snare not saved yet."""
    folder = tmp_path / "Pack"
    rnd = random.Random(3)
    write(folder / "Kicks" / "late.wav", [0.0] * 3000 + faded(KINDS["Kick"](rnd)))
    cut = KINDS["808"](rnd)
    write(folder / "808s" / "cut.wav", cut[: len(cut) // 3], width=3, channels=2)
    repo = Repo.init(folder, author="Test")
    repo.commit("first")
    snare = faded(KINDS["Snare"](rnd))
    write(folder / "Snares" / "new.wav", snare[: len(snare) // 4])
    return folder


def test_a_late_start_is_trimmed_keeping_a_millisecond(kit):
    repo = Repo(kit)
    before = frames(kit / "Kicks" / "late.wav")[0]
    out, done = fix(repo, "Kicks/late.wav", check(kit / "Kicks/late.wav", "late.wav"))
    assert out == kit / "Kicks" / "late.wav"  # saved, so fixed in place
    assert done == ["trimmed the silence at the start"]
    assert problem_kinds(out) == set()
    assert before - frames(out)[0] == pytest.approx(3000 - int(0.001 * RATE), abs=2)


def test_a_cut_off_end_fades_and_the_format_is_kept(kit):
    repo = Repo(kit)
    path = kit / "808s" / "cut.wav"
    before = frames(path)
    fix(repo, "808s/cut.wav", check(path, "cut.wav"))
    assert frames(path) == before  # same length, still 24-bit stereo
    assert problem_kinds(path) == set()


def test_the_original_of_a_saved_sound_stays_in_the_kit(kit):
    repo = Repo(kit)
    saved_hash = repo.working()["Kicks/late.wav"]["hash"]
    fix(repo, "Kicks/late.wav", check(kit / "Kicks/late.wav", "late.wav"))
    changed = repo.working()["Kicks/late.wav"]["hash"]
    assert changed != saved_hash and repo.store.has(saved_hash)


def test_an_unsaved_sound_is_copied_never_overwritten(kit):
    repo = Repo(kit)
    original = (kit / "Snares" / "new.wav").read_bytes()
    out, _ = fix(repo, "Snares/new.wav", check(kit / "Snares/new.wav", "new.wav"))
    assert out == kit / "Snares" / "new fixed.wav"
    assert (kit / "Snares" / "new.wav").read_bytes() == original
    out2, _ = fix(repo, "Snares/new.wav", check(kit / "Snares/new.wav", "new.wav"))
    assert out2.name == "new fixed 2.wav"
    assert problem_kinds(out) == set()


def test_clipping_is_not_fixed(kit):
    repo = Repo(kit)
    hot = faded(KINDS["Snare"](random.Random(1)))
    write(kit / "hot.wav", [x * 4 for x in hot])
    with pytest.raises(KitError, match="clipping cannot be undone"):
        fix(repo, "hot.wav", check(kit / "hot.wav", "hot.wav"))


def test_a_loop_seam_is_faded_at_both_ends_and_keeps_its_length():
    cut = [0.4 * math.sin(2 * math.pi * 223 * t / RATE + 0.7) for t in range(int(4.0 * RATE * 0.93))]  # as in test_problems
    assert {p["kind"] for p in find([array("d", cut)], RATE, True)} == {"click"}
    values = [int(x * 32767) for x in cut]
    fixed = repair(values, RATE, 1, 2, False, {"click"}, loop=True)
    assert len(fixed) == len(values)
    assert find([array("d", (v / 32768 for v in fixed))], RATE, True) == []


def test_kit_fix_fixes_everything_it_can(kit, monkeypatch, capsys):
    monkeypatch.chdir(kit)
    main(["fix"])
    out = capsys.readouterr().out
    assert "Kicks/late.wav: trimmed the silence at the start" in out
    assert "Snares/new.wav -> Snares/new fixed.wav: faded out the end" in out
    assert "Fixed 3 sound(s)." in out
    main(["check", "Kicks", "808s"])
    assert "No problems found." in capsys.readouterr().out


def test_the_app_fixes_one_or_all(kit):
    app = App(kit)
    app.auto_sort = True
    app.sort_soon()
    import time
    for _ in range(200):
        if not app.sorting:
            break
        time.sleep(0.05)
    sounds = {s["path"]: s for s in app.state()["sounds"]}
    assert sounds["Kicks/late.wav"]["fixable"] and sounds["808s/cut.wav"]["fixable"]
    result = app.act("fix", {"path": "Kicks/late.wav"})
    assert result["message"].startswith("Fixed late.wav: trimmed the silence at the start. Before plays the original.")
    result = app.act("fix", {"paths": ["808s/cut.wav", "Snares/new.wav"]})
    assert result["message"].startswith("Fixed 2 sounds.")
    assert (kit / "Snares" / "new fixed.wav").is_file()
