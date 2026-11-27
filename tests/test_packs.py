"""Building a pack: chosen sounds as a zip, sorted into folders by kind, with a list of contents."""

import random
import zipfile

import pytest

from kit.app import App
from kit.cli import main
from kit.packs import build, plan
from kit.repo import KitError, Repo

from sounds_by_kind import KINDS
from test_problems import faded, write
from test_tempo import drums


@pytest.fixture
def kit(tmp_path):
    folder = tmp_path / "Dark Trap Kit"
    rnd = random.Random(2)
    write(folder / "Drums" / "Sample 01.wav", faded(KINDS["Kick"](rnd)))      # a kick, heard
    write(folder / "808s" / "Glide F#m.wav", faded(KINDS["808"](rnd)))        # named
    write(folder / "Old" / "hit.wav", faded(KINDS["Snare"](rnd)))
    write(folder / "Snares" / "hit.wav", faded(KINDS["Snare"](rnd)))          # same file name as Old/hit.wav
    folder.mkdir(exist_ok=True)
    drums(folder / "groove 128bpm.wav", 128, bars=2)
    (folder / "notes.txt").write_text("not a sound Kit knows")
    Repo.init(folder, author="Test").commit("first")
    return folder


def files(zip_path):
    with zipfile.ZipFile(zip_path) as archive:
        return archive.namelist(), archive


def test_sounds_go_in_folders_by_kind_and_never_clash(kit):
    items = {item["from"]: item["to"] for item in plan(Repo(kit), [
        "Drums/Sample 01.wav", "808s/Glide F#m.wav", "Old/hit.wav", "Snares/hit.wav", "groove 128bpm.wav", "notes.txt"])}
    assert items["Drums/Sample 01.wav"] == "Kicks/Sample 01.wav"
    assert items["808s/Glide F#m.wav"] == "808s/Glide F#m.wav"
    assert items["groove 128bpm.wav"] == "Loops/groove 128bpm.wav"
    assert items["notes.txt"] == "Other/notes.txt"
    assert sorted([items["Old/hit.wav"], items["Snares/hit.wav"]]) == ["Snares/hit (Snares).wav", "Snares/hit.wav"]


def test_the_zip_holds_the_sounds_a_contents_list_and_the_changelog(kit, tmp_path):
    out, count = build(Repo(kit), ["808s/Glide F#m.wav", "groove 128bpm.wav"], tmp_path)
    assert out.name == "Dark Trap Kit pack.zip" and count == 2
    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
        assert sorted(names) == ["Dark Trap Kit pack/808s/Glide F#m.wav", "Dark Trap Kit pack/Changes.txt",
                                 "Dark Trap Kit pack/Contents.txt", "Dark Trap Kit pack/Loops/groove 128bpm.wav"]
        assert archive.read("Dark Trap Kit pack/808s/Glide F#m.wav") == (kit / "808s" / "Glide F#m.wav").read_bytes()
        text = archive.read("Dark Trap Kit pack/Contents.txt").decode()
    assert "2 sounds" in text
    assert "808s (1)\n  Glide F#m.wav   F# min · " in text and "LUFS" in text
    assert "Loops (1)\n  groove 128bpm.wav   128 BPM" in text


def test_unsaved_sounds_leave_out_the_changelog(kit, tmp_path):
    write(kit / "new.wav", faded(KINDS["Hat"](random.Random(1))))
    out, _ = build(Repo(kit), ["new.wav"], tmp_path, name="Hats only")
    with zipfile.ZipFile(out) as archive:
        assert sorted(archive.namelist()) == ["Hats only/Contents.txt", "Hats only/Hats/new.wav"]


def test_refusals(kit, tmp_path):
    repo = Repo(kit)
    with pytest.raises(KitError, match="star them"):
        build(repo, [], tmp_path)
    with pytest.raises(KitError, match="no sound at"):
        build(repo, ["nope.wav"], tmp_path)
    with pytest.raises(KitError, match="inside the kit"):
        build(repo, ["groove 128bpm.wav"], kit / "out")


def test_kit_pack_takes_folders_or_the_starred_sounds(kit, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(kit)
    assert main(["pack", "-o", str(tmp_path)]) == 1
    assert "no sounds are starred" in capsys.readouterr().err
    main(["pack", "Old", "Snares", "--name", "Snares", "-o", str(tmp_path)])
    assert "(2 sounds, sorted by kind)" in capsys.readouterr().out
    Repo(kit).star("Drums/Sample 01.wav", True)
    main(["pack", "-o", str(tmp_path)])
    with zipfile.ZipFile(tmp_path / "Dark Trap Kit pack.zip") as archive:
        assert "Dark Trap Kit pack/Kicks/Sample 01.wav" in archive.namelist()


def test_the_app_builds_it_in_downloads(kit, tmp_path, monkeypatch):
    from kit import app as app_module
    monkeypatch.setattr(app_module, "downloads_folder", lambda: tmp_path)
    monkeypatch.setattr(app_module, "reveal", lambda path: None)
    message = App(kit).act("build-pack", {"paths": ["Drums/Sample 01.wav", "groove 128bpm.wav"]})["message"]
    assert message == "Saved Dark Trap Kit pack.zip (2 sounds, sorted by kind) in " + tmp_path.name + "."
