"""The changelog: what each version added, renamed, changed and removed."""

import zipfile

import pytest

from kit.app import App
from kit.changelog import changelog, write_changelog
from kit.cli import main
from kit.export import export
from kit.repo import KitError, Repo


@pytest.fixture
def history(kit):
    """v1: three sounds. v2: a hat added, a kick renamed. v3 (named 1.1): a kick changed, the hat removed."""
    repo = Repo.init(kit, author="Nojan")
    repo.commit("first three")
    (kit / "Hats" / "hat_02.wav").write_bytes(b"TSK" * 100)
    (kit / "Kicks" / "kick_01.wav").rename(kit / "Kicks" / "kick_punch.wav")
    repo.commit("a new hat", author="Lucas")
    (kit / "Kicks" / "kick_02.wav").write_bytes(b"BOOM!" * 100)
    (kit / "Hats" / "hat_01.wav").unlink()
    repo.commit("louder kick")
    repo.name_version("1.1", "latest")
    return repo


def test_each_version_lists_its_changes_newest_first(history):
    text = changelog(history)
    assert text.startswith("kit: changes\n============\n\n")
    v3, v2, v1 = text.split("\n\n")[1:]
    assert v3.startswith("v3 (1.1) · ") and v3.splitlines()[0].endswith("· Nojan")
    assert '"louder kick"' in v3
    assert "  Changed\n    Kicks/kick_02.wav" in v3
    assert "  Removed\n    Hats/hat_01.wav" in v3
    assert v2.splitlines()[0].endswith("· Lucas")
    assert "  Added\n    Hats/hat_02.wav" in v2
    assert "  Renamed\n    Kicks/kick_01.wav -> Kicks/kick_punch.wav" in v2
    assert "  First version: 3 sounds" in v1


def test_since_gives_only_the_later_versions(history):
    text = changelog(history, since="v1")
    assert "v3 (1.1)" in text and "v2 · " in text and "First version" not in text
    assert changelog(history, since="v2").count(" · ") == 2  # just v3: one line with two dots
    with pytest.raises(KitError, match="nothing changed"):
        changelog(history, since="latest")
    with pytest.raises(KitError, match="not an earlier version"):
        changelog(history, ref="v1", since="v3")


def test_an_old_version_has_only_its_own_past(history):
    assert changelog(history, ref="v2").split("\n\n")[1].startswith("v2 · ")


def test_the_zip_carries_it_and_stays_the_same_bytes(history, tmp_path):
    out, sounds, _ = export(history, "latest", tmp_path / "a")
    with zipfile.ZipFile(out) as archive:
        assert archive.read("kit 1.1/Changes.txt").decode() == changelog(history)
    assert sounds == 3  # the changelog is not counted as a sound
    again, _, _ = export(history, "latest", tmp_path / "b")
    assert again.read_bytes() == out.read_bytes()


def test_a_sound_called_changes_txt_is_never_replaced(kit, tmp_path):
    (kit / "Changes.txt").write_bytes(b"my own notes")
    repo = Repo.init(kit, author="Nojan")
    repo.commit("first")
    out, _, _ = export(repo, "latest", tmp_path)
    with zipfile.ZipFile(out) as archive:
        assert archive.read("kit v1/Changes.txt") == b"my own notes"


def test_written_beside_the_kit_never_inside(history, kit, tmp_path):
    out = write_changelog(history, "latest", tmp_path)
    assert out.name == "kit 1.1 changes.txt"
    with pytest.raises(KitError, match="inside the kit"):
        write_changelog(history, "latest", kit)


def test_kit_changelog_prints_or_writes(history, kit, monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(kit)
    main(["changelog", "--since", "v2"])
    out = capsys.readouterr().out
    assert out.startswith("kit: changes") and "Removed" in out and "First version" not in out
    main(["changelog", "-o", str(tmp_path)])
    assert (tmp_path / "kit 1.1 changes.txt").is_file()


def test_the_app_saves_it_in_downloads(history, kit, tmp_path, monkeypatch):
    from kit import app as app_module
    monkeypatch.setattr(app_module, "downloads_folder", lambda: tmp_path)
    monkeypatch.setattr(app_module, "reveal", lambda path: None)
    message = App(kit).act("changelog", {"version": "v3", "since": "v1"})["message"]
    assert message.startswith("Saved kit 1.1 changes.txt")
    assert "First version" not in (tmp_path / "kit 1.1 changes.txt").read_text()
