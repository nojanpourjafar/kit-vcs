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
