"""The producer name that credits use: set once, used everywhere, changeable."""

import pytest

from kit import remote
from kit.app import App
from kit.cli import main
from kit.repo import KitError, Repo, clean_name


def test_names_are_tidied_and_checked():
    assert clean_name("  Lil   Nojan \n") == "Lil Nojan"
    for bad in ["", "   ", "x" * 41]:
        with pytest.raises(KitError):
            clean_name(bad)


def test_the_name_set_on_the_home_screen_is_used_for_new_kits(kit, tmp_path):
    app = App()
    assert app.act("name", {"name": "Lil Nojan"})["message"] == "You'll be credited as Lil Nojan."
    assert app.state()["name"] == "Lil Nojan"

    app.act("open", {"folder": str(kit)})
    assert app.state()["author"] == "Lil Nojan"               # offered when starting to track
    app.act("init", {})
    assert Repo(kit).author == "Lil Nojan"


def test_getting_a_shared_kit_credits_the_name_too(kit, tmp_path):
    mine = Repo.init(kit, author="Nojan")
    mine.commit("first")
    remote.push(mine, remote.FolderRemote(tmp_path / "shared"))
    friend = App()
    friend.act("name", {"name": "Lucas"})
    friend.act("clone", {"remote": str(tmp_path / "shared"), "into": str(tmp_path / "Music")})
    assert Repo(tmp_path / "Music" / "shared").author == "Lucas"


def test_changing_the_name_keeps_old_credits(kit):
    repo = Repo.init(kit, author="nojan_macbook")
    repo.commit("first")
    App(kit).act("name", {"name": "Nojan"})

    (kit / "Hats" / "hat_02.wav").write_bytes(b"TIK" * 10)
    repo.commit("another hat")
    credits = {path: commit.author for path, commit in repo.credits().items()}
    assert credits["Kicks/kick_01.wav"] == "nojan_macbook"      # saved before: unchanged
    assert credits["Hats/hat_02.wav"] == "Nojan"


def test_kit_author_shows_and_changes_it(kit, monkeypatch, capsys):
    Repo.init(kit, author="Nojan")
    monkeypatch.chdir(kit)
    main(["author"])
    assert capsys.readouterr().out.strip() == "Nojan"
    main(["author", "Lil", "Nojan"])
    assert "credited to Lil Nojan" in capsys.readouterr().out
    assert main(["author", " "]) == 1                          # refused, with a plain message
