"""The S3 remote, tested against moto's in-memory imitation of S3.

Skipped when boto3 or moto is not installed:  pip install boto3 moto
"""

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from kit import remote  # noqa: E402
from kit.manifest import scan  # noqa: E402
from kit.repo import Repo  # noqa: E402
from kit.s3remote import S3Remote  # noqa: E402


@pytest.fixture
def bucket(monkeypatch):
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(name, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("KIT_S3_ENDPOINT", raising=False)
    with moto.mock_aws():
        boto3.client("s3").create_bucket(Bucket="kits")
        yield "kits"


def test_push_and_pull_through_a_bucket(kit, tmp_path, bucket):
    mine = Repo.init(kit, author="Nojan")
    mine.commit("first")
    rem = S3Remote(bucket, "packs/demo")

    assert rem.read_head() is None
    sent = remote.push(mine, rem)
    assert (sent.commits, sent.objects) == (1, 4)
    assert remote.push(mine, rem) == remote.SyncResult(0, 0)

    keys = [o["Key"] for o in boto3.client("s3").list_objects_v2(Bucket=bucket)["Contents"]]
    assert all(key.startswith("packs/demo/") for key in keys)
    assert "packs/demo/HEAD" in keys

    other = tmp_path / "friend"
    other.mkdir()
    theirs = Repo.init(other, author="Lucas")
    got = remote.pull(theirs, remote.open_remote(f"s3://{bucket}/packs/demo"))
    assert got.outcome == "fast-forward"
    assert scan(other) == scan(kit)


def test_two_kits_can_share_one_bucket(kit, tmp_path, bucket):
    mine = Repo.init(kit)
    mine.commit("first")
    remote.push(mine, S3Remote(bucket, "one"))
    assert S3Remote(bucket, "two").read_head() is None
    assert S3Remote(bucket).read_head() is None


def test_packed_files_go_through_a_bucket(tmp_path, bucket):
    from test_compact import wav

    folder = tmp_path / "mine"
    folder.mkdir()
    (folder / "loop.wav").write_bytes(wav())
    mine = Repo.init(folder, author="Nojan")
    mine.commit("a loop")
    mine.store.compact()
    remote.push(mine, S3Remote(bucket, "packed"))
    keys = [o["Key"] for o in boto3.client("s3").list_objects_v2(Bucket=bucket)["Contents"]]
    assert sum(key.endswith(".kz") for key in keys) == 1

    friend, _ = remote.clone(f"s3://{bucket}/packed", tmp_path / "friend")
    assert (tmp_path / "friend" / "loop.wav").read_bytes() == wav()
    assert friend.verify() == []
