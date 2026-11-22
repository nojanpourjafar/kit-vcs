"""A saved version compared with any older one, not just the one before it."""

import pytest

from kit.repo import KitError, Repo

from test_app import app  # noqa: F401  (the running app, used as a fixture)


def four_versions(kit):
    """v1 has three sounds; each later version changes one thing."""
    repo = Repo.init(kit, author="Nojan")
    repo.commit("first")                                                   # v1
    (kit / "Kicks" / "kick_01.wav").write_bytes(b"LOUDER" * 100)
    repo.commit("louder kick")                                             # v2
    (kit / "Kicks" / "kick_03.wav").write_bytes(b"NEW" * 10)
    repo.commit("one more kick")                                           # v3
    (kit / "Hats" / "hat_01.wav").unlink()
    repo.commit("no hats")                                                 # v4
    return repo


def kinds(version):
    return {c["path"]: c["kind"] for c in version["changes"]}


def test_by_default_a_version_is_compared_with_the_one_before(app, kit):
    four_versions(kit)
    v4 = app.json("/api/version?ref=v4")
    assert v4["against"]["label"] == "v3"
    assert kinds(v4) == {"Hats/hat_01.wav": "removed"}
    assert app.json("/api/version?ref=v1")["against"] is None             # nothing before the first


def test_compared_with_an_older_version_it_shows_everything_since(app, kit):
    repo = four_versions(kit)
    v1 = repo.log()[-1].id
    v4 = app.json(f"/api/version?ref=v4&against={v1}")
    assert v4["against"] == {"id": v1, "label": "v1"}
    assert kinds(v4) == {"Kicks/kick_01.wav": "modified", "Kicks/kick_03.wav": "added",
                         "Hats/hat_01.wav": "removed"}
    assert v4["removed"] == ["Hats/hat_01.wav"]
    sounds = {s["path"]: s for s in v4["sounds"]}
    assert sounds["Kicks/kick_01.wav"]["change"] == "modified"
    assert sounds["Kicks/kick_02.wav"]["change"] is None                  # the same in both
    # The Before button plays the sound as it was in the version compared with.
    status, body, _ = app(f"/api/audio?{sounds['Kicks/kick_01.wav']['before']}&t={app.token}", token="")
    assert body == b"KICK" * 100


def test_versions_can_be_named_the_usual_ways(app, kit):
    repo = four_versions(kit)
    repo.name_version("sent to Jay", "v2")
    assert app.json("/api/version?ref=v4&against=v2")["against"]["label"] == "v2"
    assert kinds(app.json("/api/version?ref=v4&against=sent%20to%20Jay")) == {
        "Kicks/kick_03.wav": "added", "Hats/hat_01.wav": "removed"}
    assert kinds(app.json("/api/version?ref=v3&against=v3")) == {}       # compared with itself
    status, _, _ = app("/api/version?ref=v4&against=v9")
    assert status == 400                                                   # no such version


def test_the_app_method_takes_against_too(kit):
    from kit.app import App
    four_versions(kit)
    assert App(kit).version("v4", against="v2")["against"]["label"] == "v2"
    with pytest.raises(KitError):
        App(kit).version("v4", against="nope")
