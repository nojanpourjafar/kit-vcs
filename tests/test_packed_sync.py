"""Compressed WAV files travel compressed through push and pull."""

import pytest

from kit import remote
from kit.repo import KitError, Repo

from test_compact import wav


@pytest.fixture
def compacted(tmp_path):
    """A kit of two saved loops, stored compressed, plus a text file that stays plain."""
    folder = tmp_path / "mine"
    (folder / "Loops").mkdir(parents=True)
    (folder / "Loops" / "a.wav").write_bytes(wav(seed=1))
    (folder / "Loops" / "b.wav").write_bytes(wav(seed=2))
    (folder / "notes.txt").write_text("plain text stays plain")
    repo = Repo.init(folder, author="Nojan")
    repo.commit("two loops")
    assert repo.store.compact()[0] == 2
    return repo


def sizes(folder):
    return sum(path.stat().st_size for path in (folder / "objects").rglob("*") if path.is_file())


def test_a_push_sends_packed_files(compacted, tmp_path):
    shared = tmp_path / "shared"
    result = remote.push(compacted, remote.open_remote(str(shared)))
    assert result.objects == 4                                             # manifest, two loops, the text file
    assert len(list((shared / "objects").rglob("*.kz"))) == 2
    plain = sum(len(wav(seed=s)) for s in (1, 2))
    assert sizes(shared) < 0.7 * plain                                      # about half what plain WAVs would take


def test_a_friend_gets_the_exact_files(compacted, tmp_path):
    shared = tmp_path / "shared"
    remote.push(compacted, remote.open_remote(str(shared)))
    friend, got = remote.clone(str(shared), tmp_path / "friend", author="Jay")
    assert got.objects == 4
    assert (tmp_path / "friend" / "Loops" / "a.wav").read_bytes() == wav(seed=1)
    assert (tmp_path / "friend" / "notes.txt").read_text() == "plain text stays plain"
    assert len(list((tmp_path / "friend" / ".kit" / "objects").rglob("*.kz"))) == 2   # kept packed there too
    assert friend.verify() == []


def test_plain_and_packed_files_mix(tmp_path):
    # A kit pushed before compression existed holds plain files; a newer push adds packed ones.
    folder = tmp_path / "mine"
    folder.mkdir()
    (folder / "old.wav").write_bytes(wav(seed=1))
    repo = Repo.init(folder, author="Nojan")
    repo.commit("old")
    shared = remote.open_remote(str(tmp_path / "shared"))
    remote.push(repo, shared)                                              # plain
    (folder / "new.wav").write_bytes(wav(seed=2))
    repo.commit("new")
    repo.store.compact()
    remote.push(repo, shared)                                              # only the new loop, packed
    assert len(list((tmp_path / "shared" / "objects").rglob("*.kz"))) == 1
    friend, _ = remote.clone(str(tmp_path / "shared"), tmp_path / "friend")
    assert (tmp_path / "friend" / "old.wav").read_bytes() == wav(seed=1)
    assert (tmp_path / "friend" / "new.wav").read_bytes() == wav(seed=2)


def test_a_damaged_packed_file_is_refused(compacted, tmp_path):
    shared = tmp_path / "shared"
    remote.push(compacted, remote.open_remote(str(shared)))
    damaged = next((shared / "objects").rglob("*.kz"))
    data = bytearray(damaged.read_bytes())
    data[len(data) // 2] ^= 0xFF
    damaged.write_bytes(bytes(data))
    with pytest.raises(KitError, match="did not match its hash"):
        remote.clone(str(shared), tmp_path / "friend")


def test_a_packed_file_under_the_wrong_name_is_refused(compacted, tmp_path):
    shared = remote.open_remote(str(tmp_path / "shared"))
    digest = next(d for d in compacted.store.all_hashes() if compacted.store.packed_bytes(d))
    other = next(d for d in compacted.store.all_hashes() if d != digest)
    with pytest.raises(ValueError, match="did not unpack to its hash"):
        shared.upload_packed(other, compacted.store.packed_bytes(digest))
    assert not shared.has_object(other)
