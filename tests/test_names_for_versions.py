"""Names for versions: "1.0", "Sold on BeatStars"."""

import zipfile

import pytest

from kit.app import App
from kit.cli import main
from kit.export import export
from kit.repo import KitError, Repo


@pytest.fixture
def repo(kit):
    repo = Repo.init(kit, author="Nojan")
    repo.commit("first")
    (kit / "Hats" / "hat_02.wav").write_bytes(b"TIK" * 10)
    repo.commit("open hat")
    return repo


def test_a_name_works_wherever_a_version_does(repo, tmp_path):
    first = repo.resolve("v1")
    repo.name_version("Sold on BeatStars", "v1")
    assert repo.resolve("sold on beatstars") == first           # capital letters do not matter
    assert repo.names_of(first) == ["Sold on BeatStars"]
    out, _, _ = export(repo, "Sold on BeatStars", tmp_path)
    assert out.name == "kit Sold on BeatStars.zip"
    with zipfile.ZipFile(out) as archive:
        assert archive.namelist()[0].startswith("kit Sold on BeatStars/")


def test_one_name_means_one_version(repo):
    repo.name_version("1.0", "v1")
    repo.name_version("1.0", "v1")                              # naming the same version again is fine
    with pytest.raises(KitError, match="already names another version"):
        repo.name_version("1.0", "v2")
    repo.remove_name("1.0")
    repo.name_version("1.0", "v2")                              # after removing, it can be used again
    assert repo.resolve("1.0") == repo.resolve("v2")


def test_names_that_already_mean_something_are_refused(repo):
    for taken in ["v1", "V12", "latest", "HEAD"]:
        with pytest.raises(KitError, match="already means something"):
            repo.name_version(taken, "v1")
    with pytest.raises(KitError, match="no version is named"):
        repo.remove_name("2.0")


def test_the_app_shows_and_changes_names(repo):
    app = App(repo.folder)
    app.act("name-version", {"version": repo.resolve("v2"), "name": "1.0"})
    versions = app.state()["versions"]
    assert [v["names"] for v in versions] == [["1.0"], []]      # newest first
    assert app.version("1.0")["names"] == ["1.0"]
    app.act("unname", {"name": "1.0"})
    assert app.state()["versions"][0]["names"] == []


def test_the_command_line(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo.folder)
    main(["name", "v2", "Dark", "Trap", "1.0"])
    main(["names"])
    main(["log"])
    out = capsys.readouterr().out
    assert "Named" in out and "Dark Trap 1.0            v2" in out and "open hat [Dark Trap 1.0]" in out
    main(["name", "--remove", "Dark", "Trap", "1.0"])
    assert repo.names() == {}
    assert main(["name", "v2"]) == 1                            # a name is missing: a plain message
