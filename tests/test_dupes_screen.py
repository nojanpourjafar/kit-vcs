"""The Duplicates screen: the same sound in several kits, from the app."""

import random

import pytest

from kit.app import App
from kit.repo import KitError, Repo

from sounds_by_kind import KINDS
from test_problems import faded, write


@pytest.fixture
def two_kits(tmp_path):
    """Two kits sharing a kick (the same file) and a snare (turned down 6 dB in one)."""
    rnd = random.Random(3)
    kick, snare, hat = faded(KINDS["Kick"](rnd)), faded(KINDS["Snare"](rnd)), faded(KINDS["Hat"](rnd))
    mine, theirs = tmp_path / "Mine", tmp_path / "Theirs"
    write(mine / "Kicks" / "kick.wav", kick)
    write(theirs / "Drums" / "Kick copy.wav", kick)
    write(mine / "snare.wav", snare)
    write(theirs / "snare quiet.wav", [x * 0.5 for x in snare])
    write(mine / "hat.wav", hat)
    for folder in (mine, theirs):
        Repo.init(folder, author="Test")
    app = App(theirs)
    app.open(mine)
    return app, mine, theirs


def test_groups_cover_every_kit_opened(two_kits):
    app, mine, theirs = two_kits
    found = app.duplicates()
    assert found["kits"] == 2
    kinds = sorted((group["exact"], sorted((p["kit"], p["path"]) for p in group["places"])) for group in found["groups"])
    assert kinds == [(False, [("Mine", "snare.wav"), ("Theirs", "snare quiet.wav")]),
                     (True, [("Mine", "Kicks/kick.wav"), ("Theirs", "Drums/Kick copy.wav")])]
    assert found["extra"] == sum(group["extra"] for group in found["groups"]) > 0


def test_every_place_can_be_played_and_shown(two_kits, monkeypatch):
    from kit import app as app_module
    app, mine, theirs = two_kits
    shown = []
    monkeypatch.setattr(app_module, "reveal", shown.append)
    for group in app.duplicates()["groups"]:
        for place in group["places"]:
            query = dict(part.split("=", 1) for part in place["source"].split("&"))
            from urllib.parse import unquote
            path, _ = app.locate({key: unquote(value) for key, value in query.items()})
            assert path.read_bytes()[:4] == b"RIFF"
            app.act("reveal", {"path": place["path"], "kit": place["folder"]})
    assert len(shown) == 4


def test_no_kits_yet(tmp_path):
    with pytest.raises(KitError, match="open a kit first"):
        App(None).duplicates()
