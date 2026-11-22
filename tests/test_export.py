"""Exporting a version as a plain zip."""

import zipfile

import pytest

from kit.app import App
from kit.cli import main
from kit.export import export
from kit.repo import KitError, Repo


@pytest.fixture
def saved(kit):
    """A kit with two versions: v1 has three sounds, v2 adds a fourth."""
    repo = Repo.init(kit, author="Nojan")
    repo.commit("first")
    (kit / "Hats" / "hat_02.wav").write_bytes(b"TSK" * 50)
    repo.commit("another hat")
    return repo


def contents(path):
    with zipfile.ZipFile(path) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def test_a_version_becomes_one_tidy_folder(saved, tmp_path):
    out, sounds, total = export(saved, "v1", tmp_path / "out")

    assert out.name == "kit v1.zip" and sounds == 3 and total == 1100
    assert contents(out) == {
        "kit v1/Hats/hat_01.wav": b"TSS" * 100,
        "kit v1/Kicks/kick_01.wav": b"KICK" * 100,
        "kit v1/Kicks/kick_02.wav": b"BOOM" * 100,
    }                                                         # only v1's sounds, never .kit


def test_the_same_version_always_gives_the_same_bytes(saved, tmp_path):
    first, _, _ = export(saved, "latest", tmp_path / "a")
    second, _, _ = export(saved, "v2", tmp_path / "b")
    assert first.read_bytes() == second.read_bytes()


def test_the_zip_never_lands_inside_the_kit(saved, kit):
    for inside in (kit, kit / "Kicks"):
        with pytest.raises(KitError, match="inside the kit"):
            export(saved, "v1", inside)
    assert not list(kit.rglob("*.zip"))


def test_unknown_versions_are_refused(saved, tmp_path):
    with pytest.raises(KitError):
        export(saved, "v9", tmp_path)


def test_the_command_puts_it_next_to_the_kit(saved, kit, monkeypatch, capsys):
    monkeypatch.chdir(kit)
    assert main(["export"]) == 0
    assert (kit.parent / "kit v2.zip").is_file()
    assert "4 sounds" in capsys.readouterr().out


def test_the_app_saves_it_in_downloads_and_shows_it(saved, kit, tmp_path, monkeypatch):
    downloads, shown = tmp_path / "Downloads", []
    downloads.mkdir()
    monkeypatch.setattr("kit.app.downloads_folder", lambda: downloads)
    monkeypatch.setattr("kit.app.reveal", shown.append)
    message = App(kit).act("export", {"version": "v1"})["message"]
    assert message == "Saved kit v1.zip (3 sounds) in Downloads."
    assert shown == [downloads / "kit v1.zip"]
