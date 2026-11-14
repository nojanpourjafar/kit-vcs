import pytest

from kit.hashing import hash_bytes
from kit.objects import ObjectStore


def test_same_content_is_stored_once(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    (tmp_path / "a.wav").write_bytes(b"same audio")
    (tmp_path / "b.wav").write_bytes(b"same audio")

    first = store.put_file(tmp_path / "a.wav")
    second = store.put_file(tmp_path / "b.wav")

    assert first == second == hash_bytes(b"same audio")
    assert list(store.all_hashes()) == [first]


def test_round_trip(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    digest = store.put_bytes(b"808")
    store.copy_to(digest, tmp_path / "out" / "808.wav")

    assert (tmp_path / "out" / "808.wav").read_bytes() == b"808"
    assert store.read_bytes(digest) == b"808"


def test_rejects_anything_that_is_not_a_hash(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    with pytest.raises(ValueError):
        store.path_for("../../etc/passwd")
