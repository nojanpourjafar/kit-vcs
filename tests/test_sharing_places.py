"""Where a kit is shared, and finding it again, when people pick everyday folders."""

import pytest

from kit import remote
from kit.app import App
from kit.cli import main
from kit.remote import FolderRemote, find_shared, place_to_share
from kit.repo import KitError, Repo


@pytest.fixture
def repo(kit):
    repo = Repo.init(kit, author="Nojan")
    repo.commit("first")
    return repo


@pytest.fixture
def drive(tmp_path):
    """A Google Drive folder with other things already in it."""
    drive = tmp_path / "Google Drive"
    (drive / "School").mkdir(parents=True)
    (drive / "resume.pdf").write_bytes(b"%PDF")
    return drive


def test_a_busy_folder_gets_a_folder_for_the_kit(repo, drive):
    assert place_to_share(str(drive), repo) == str(drive / "kit")


def test_an_empty_folder_or_a_shared_kit_is_used_as_it_is(repo, tmp_path):
    empty = tmp_path / "new"
    empty.mkdir()
    (empty / ".DS_Store").write_bytes(b"")                       # hidden files do not count
    assert place_to_share(str(empty), repo) == str(empty)
    assert place_to_share(str(tmp_path / "not there yet"), repo) == str(tmp_path / "not there yet")
    remote.push(repo, FolderRemote(tmp_path / "shared"))
    assert place_to_share(str(tmp_path / "shared"), repo) == str(tmp_path / "shared")


def test_the_kit_cannot_be_shared_into_itself(repo, kit):
    for inside in (kit, kit / "Kicks", kit.parent):              # kit.parent/"kit" is the kit itself
        with pytest.raises(KitError, match="cannot be the kit itself"):
            place_to_share(str(inside), repo)


def test_a_folder_with_the_kits_name_that_is_not_a_shared_kit_is_refused(repo, drive):
    (drive / "kit").mkdir()
    (drive / "kit" / "my own stuff.txt").write_text("hi")
    with pytest.raises(KitError, match="not a shared kit"):
        place_to_share(str(drive), repo)


def test_pushing_to_my_drive_keeps_the_drive_tidy(repo, drive, monkeypatch, capsys):
    monkeypatch.chdir(repo.folder)
    main(["push", str(drive)])
    assert sorted(p.name for p in drive.iterdir()) == ["School", "kit", "resume.pdf"]
    assert repo.remote() == str(drive / "kit")


def test_getting_a_kit_finds_it_inside_the_folder_picked(repo, drive, tmp_path):
    remote.push(repo, FolderRemote(drive / "kit"))
    assert find_shared(str(drive)) == str(drive / "kit")
    friend = App()
    friend.act("clone", {"remote": str(drive), "into": str(tmp_path / "Music")})
    assert friend.state()["name"] == "kit"
    assert (tmp_path / "Music" / "kit" / "Kicks" / "kick_01.wav").is_file()


def test_several_shared_kits_are_listed_to_choose_from(repo, drive):
    remote.push(repo, FolderRemote(drive / "Dark Trap"))
    remote.push(repo, FolderRemote(drive / "Lo-fi"))
    with pytest.raises(KitError, match=r"several shared kits \(Dark Trap, Lo-fi\)"):
        find_shared(str(drive))


def test_the_app_says_where_the_kit_is_shared(repo, drive):
    message = App(repo.folder).act("remote", {"remote": str(drive)})["message"]
    assert message.startswith(f"Sharing in {drive / 'kit'}, a folder of its own")
