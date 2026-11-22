"""Version names travelling through a shared folder (and an S3 bucket)."""

import pytest

from kit import remote
from kit.cli import main

from test_remote import _shared


def test_a_name_given_here_reaches_the_other_side(kit, tmp_path):
    mine, theirs, rem = _shared(kit, tmp_path)
    mine.name_version("1.0", "v1")
    pushed = remote.push(mine, rem)                         # no new versions, only a name
    assert pushed.commits == 0 and pushed.names == 1
    got = remote.pull(theirs, rem)
    assert got.outcome == "up-to-date" and got.names == 1
    assert theirs.resolve("1.0") == mine.resolve("v1")


def test_a_removed_name_stays_removed_and_is_removed_there_too(kit, tmp_path):
    mine, theirs, rem = _shared(kit, tmp_path)
    mine.name_version("1.0", "v1")
    remote.push(mine, rem)
    remote.pull(theirs, rem)

    mine.remove_name("1.0")
    remote.pull(mine, rem)                                  # the shared folder still has it...
    assert mine.names() == {}                               # ...but it does not come back
    remote.push(mine, rem)
    assert rem.read_names() == {}                           # and now it is gone there too
    assert mine.removed_names() == {}                       # nothing left to remember


def test_the_same_name_for_different_versions_is_kept_apart(kit, tmp_path, capsys, monkeypatch):
    mine, theirs, rem = _shared(kit, tmp_path)
    (theirs.folder / "Hats" / "hat_02.wav").write_bytes(b"TIK" * 10)
    theirs.commit("open hat")
    theirs.name_version("final", "latest")
    remote.push(theirs, rem)

    mine.name_version("final", "v1")                        # Nojan used "final" for another version
    mine.set_remote(str(rem.path))
    monkeypatch.chdir(kit)
    main(["pull"])
    assert '"final" names a different version here; each side keeps its own.' in capsys.readouterr().out
    assert mine.resolve("final") == mine.resolve("v1")      # ours is kept
    remote.push(mine, rem)
    assert rem.read_names()["final"] == theirs.resolve("final")   # theirs is kept too


def test_a_damaged_names_file_is_ignored(kit, tmp_path):
    mine, theirs, rem = _shared(kit, tmp_path)
    (rem.path / "names.json").write_text('{"1.0": "not a hash", "x": 5, "../evil": "' + "a" * 64 + '"')
    assert rem.read_names() == {}                           # unreadable JSON: nothing
    (rem.path / "names.json").write_text('{"1.0": "not a hash", "x": 5}')
    assert rem.read_names() == {}                           # nothing that looks like a version
    assert remote.pull(mine, rem).names == 0


def test_names_go_through_a_bucket_too(kit, tmp_path, monkeypatch):
    pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(name, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("KIT_S3_ENDPOINT", raising=False)
    with moto.mock_aws():
        import boto3

        from kit.s3remote import S3Remote

        boto3.client("s3").create_bucket(Bucket="kits")
        bucket = S3Remote("kits", "dark-trap")
        mine, theirs, _ = _shared(kit, tmp_path)
        remote.push(mine, bucket)
        mine.name_version("1.0", "v1")
        assert remote.push(mine, bucket).names == 1
        assert bucket.read_names() == {"1.0": mine.resolve("v1")}
