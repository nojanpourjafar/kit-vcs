"""Starred sounds: your own favourites, never shared."""

import json

import pytest

from kit import remote
from kit.repo import KitError, Repo

from test_app import app  # noqa: F401  (the running app, used as a fixture)


def starred(app):
    return {s["path"] for s in app.json("/api/state")["sounds"] if s["starred"]}


def test_a_sound_can_be_starred_and_unstarred(app, kit):
    Repo.init(kit, author="Nojan")
    assert starred(app) == set()
    assert app("/api/star", {"path": "Kicks/kick_01.wav"})[0] == 200
    app("/api/star", {"path": "Hats/hat_01.wav", "on": True})
    assert starred(app) == {"Kicks/kick_01.wav", "Hats/hat_01.wav"}
    app("/api/star", {"path": "Hats/hat_01.wav", "on": False})
    assert starred(app) == {"Kicks/kick_01.wav"}
    assert json.loads((kit / ".kit" / "stars.json").read_text()) == ["Kicks/kick_01.wav"]


def test_only_sounds_in_the_folder_can_be_starred(app, kit):
    Repo.init(kit, author="Nojan")
    status, body, _ = app("/api/star", {"path": "Kicks/missing.wav"})
    assert status == 400 and "no sound" in json.loads(body)["error"]
    assert app("/api/star", {"path": "../outside.wav"})[0] == 400
    assert app("/api/star", {"path": "Kicks/missing.wav", "on": False})[0] == 200   # unstarring is always fine


def test_starring_is_not_a_change_to_save(kit):
    repo = Repo.init(kit, author="Nojan")
    repo.commit("first")
    repo.star("Kicks/kick_01.wav")
    assert repo.status() == []


def test_a_star_follows_a_renamed_sound(app, kit):
    Repo.init(kit, author="Nojan").commit("first")
    app("/api/star", {"path": "Kicks/kick_01.wav"})
    (kit / "Kicks" / "kick_01.wav").rename(kit / "Kicks" / "Big Kick.wav")
    assert starred(app) == {"Kicks/Big Kick.wav"}
    app.json("/api/commit", {"message": "renamed the kick"})
    assert starred(app) == {"Kicks/Big Kick.wav"}                            # and it stays there


def test_stars_are_never_pushed(kit, tmp_path):
    repo = Repo.init(kit, author="Nojan")
    repo.commit("first")
    repo.star("Kicks/kick_01.wav")
    shared = tmp_path / "shared"
    remote.push(repo, remote.open_remote(str(shared)))
    friend, _ = remote.clone(str(shared), tmp_path / "friend", author="Jay")
    assert friend.stars() == set()
    assert not any(path.name == "stars.json" for path in shared.rglob("*"))


def test_a_broken_stars_file_means_no_stars(kit):
    repo = Repo.init(kit, author="Nojan")
    (kit / ".kit" / "stars.json").write_text("not json")
    assert repo.stars() == set()
    (kit / ".kit" / "stars.json").write_text('{"a": 1}')
    assert repo.stars() == set()
    repo.star("Kicks/kick_01.wav")
    assert repo.stars() == {"Kicks/kick_01.wav"}


def test_stars_need_a_kit(kit):
    from kit.app import App
    with pytest.raises(KitError):
        App(kit).act("star", {"path": "Kicks/kick_01.wav"})
