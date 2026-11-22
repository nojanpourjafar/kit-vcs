"""What there is to push and pull, seen before pressing either."""

from kit import remote
from kit.app import App
from kit.cli import main

from test_remote import _shared


def test_counts_follow_each_side_saving_and_syncing(kit, tmp_path):
    mine, theirs, rem = _shared(kit, tmp_path)
    assert remote.compare(mine, rem) == {"ahead": 0, "behind": 0, "from": []}

    (kit / "Kicks" / "kick_03.wav").write_bytes(b"THUD" * 10)
    mine.commit("a third kick")
    (kit / "Kicks" / "kick_04.wav").write_bytes(b"BAP" * 10)
    mine.commit("a fourth kick")
    assert remote.compare(mine, rem)["ahead"] == 2              # saved but not shared

    (theirs.folder / "Hats" / "hat_02.wav").write_bytes(b"TIK" * 10)
    theirs.commit("open hat")
    remote.push(theirs, rem)
    assert remote.compare(mine, rem) == {"ahead": 2, "behind": 1, "from": ["Lucas"]}

    remote.pull(mine, rem)                                      # merges: now nothing is missing here
    assert remote.compare(mine, rem)["behind"] == 0
    remote.push(mine, rem)
    assert remote.compare(mine, rem) == {"ahead": 0, "behind": 0, "from": []}


def test_the_app_shows_it_only_for_shared_folders(kit, tmp_path):
    mine, theirs, rem = _shared(kit, tmp_path)
    (theirs.folder / "Hats" / "hat_02.wav").write_bytes(b"TIK" * 10)
    theirs.commit("open hat")
    remote.push(theirs, rem)
    mine.set_remote(str(rem.path))
    assert App(kit).state()["sync"] == {"ahead": 0, "behind": 1, "from": ["Lucas"]}

    mine.set_remote("s3://some-bucket/kit")                     # a bucket is not checked on every redraw
    assert App(kit).state()["sync"] is None


def test_kit_status_says_what_to_do(kit, tmp_path, monkeypatch, capsys):
    mine, theirs, rem = _shared(kit, tmp_path)
    mine.set_remote(str(rem.path))
    (theirs.folder / "Hats" / "hat_02.wav").write_bytes(b"TIK" * 10)
    theirs.commit("open hat")
    remote.push(theirs, rem)
    (kit / "Kicks" / "kick_03.wav").write_bytes(b"THUD" * 10)
    mine.commit("a third kick")

    monkeypatch.chdir(kit)
    main(["status"])
    out = capsys.readouterr().out
    assert "1 new version(s) from Lucas: run kit pull" in out
    assert "1 version(s) not shared yet: run kit push" in out
