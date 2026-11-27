"""scripts/measure.py: the read-only report on a kit folder."""

import importlib.util
import shutil
from pathlib import Path

from test_tempo import drums

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "measure.py"
spec = importlib.util.spec_from_file_location("measure", SCRIPT)
measure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measure)


def snapshot(folder):
    """Every file and folder with its size and modified time, to prove nothing changed."""
    return sorted((str(p.relative_to(folder)), p.is_dir(), p.stat().st_size, p.stat().st_mtime_ns)
                  for p in folder.rglob("*"))


def test_a_kit_is_measured_without_being_touched(tmp_path, capsys):
    kit = tmp_path / "Real Kit"
    (kit / "Loops").mkdir(parents=True)
    drums(kit / "Loops" / "Groove 95 BPM Am.wav", 95)          # the name agrees with the audio
    drums(kit / "Loops" / "Fast 120bpm.wav", 120)
    drums(kit / "Loops" / "no tempo in name.wav", 100)
    (kit / "Loops" / "notes.txt").write_text("for the beat tape")
    shutil.copy(kit / "Loops" / "Fast 120bpm.wav", kit / "Loops" / "Fast 120bpm copy.wav")
    before = snapshot(kit)

    numbers = measure.measure(kit)

    assert snapshot(kit) == before and not (kit / ".kit").exists()  # read only
    assert numbers["files"] == 5 and numbers["audio files"] == 4
    assert numbers["duplicate files"] == 1
    assert numbers["duplicate bytes"] == (kit / "Loops" / "Fast 120bpm.wav").stat().st_size
    assert numbers["bpm in name"] == 3 and numbers["key in name"] == 1
    assert numbers["tempo checked"] == 3 and numbers["tempo results"]["exact"] == 3
    assert numbers["unnamed wav loops estimated"] == 1
    assert numbers["version without changes bytes"] < 1000 < numbers["bytes"]

    assert measure.main([str(kit)]) == 0
    printed = capsys.readouterr().out
    assert "exact (within 1 BPM):  3 (100%)" in printed and "Duplicates: 1 files" in printed


def test_misses_are_listed_so_they_can_be_looked_into(tmp_path):
    kit = tmp_path / "kit"
    kit.mkdir()
    drums(kit / "Mislabelled 90bpm.wav", 130)                   # the name is wrong on purpose
    numbers = measure.measure(kit)
    assert numbers["tempo results"]["other"] == 1
    assert numbers["tempo misses"] == [{"path": "Mislabelled 90bpm.wav", "name says": 90, "estimate": 130.0}]


def test_a_missing_folder_is_a_plain_message(tmp_path):
    try:
        measure.main([str(tmp_path / "nowhere")])
    except SystemExit as stop:
        assert "there is no folder" in str(stop)
    else:
        raise AssertionError("expected a message")


def test_kinds_keys_problems_and_loudness_are_checked_against_names(tmp_path):
    import random
    from sounds_by_kind import KINDS
    from test_problems import faded, write

    kit = tmp_path / "Real Kit"
    rnd = random.Random(6)
    for kind_name, folder in (("Kick", "Kicks"), ("Hat", "Hats"), ("808", "808s")):
        for i in range(2):
            write(kit / folder / f"{i}.wav", faded(KINDS[kind_name](rnd)))
    write(kit / "Kicks" / "late.wav", [0.0] * 3000 + faded(KINDS["Kick"](rnd)))
    (kit / "Loops").mkdir()
    drums(kit / "Loops" / "Groove 95 BPM Am.wav", 95)   # drums: no key heard, though the name has one
    before = snapshot(kit)

    numbers = measure.measure(kit)

    assert snapshot(kit) == before
    kinds = numbers["kind check"]
    assert kinds["checked"] == 8 and kinds["right"] >= 7
    assert numbers["key check"]["checked"] == 1 and numbers["key check"]["results"]["no answer"] == 1
    assert numbers["sound check"]["problems"] == {"late": 1}
    assert numbers["sound check"]["loudness"]["middle"] < 0
    text = measure.report(numbers)
    assert "Kind (Kick, 808" in text and "right" in text and "late" in text and "Loudness (LUFS)" in text
