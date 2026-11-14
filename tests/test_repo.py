import pytest

from kit.commits import new_commit
from kit.manifest import dumps, scan
from kit.repo import KitError, Repo


def test_first_commit_and_log(kit):
    repo = Repo.init(kit, author="Nojan")
    saved = repo.commit("first version")

    assert repo.head() == saved.commit.id
    assert saved.new_files == 3
    assert [c.message for c in repo.log()] == ["first version"]
    assert repo.log()[0].author == "Nojan"
    assert repo.status() == []


def test_commit_with_no_changes_is_refused(kit):
    repo = Repo.init(kit)
    repo.commit("first")
    with pytest.raises(KitError):
        repo.commit("again")


def test_renaming_a_sample_stores_no_new_audio(kit):
    repo = Repo.init(kit)
    repo.commit("first")
    (kit / "Kicks" / "kick_01.wav").rename(kit / "Kicks" / "kick_FINAL.wav")

    saved = repo.commit("renamed the kick")

    assert saved.new_files == 0
    assert [c.kind for c in saved.changes] == ["renamed"]


def test_history_is_a_chain_of_parents(kit):
    repo = Repo.init(kit)
    first = repo.commit("first").commit
    (kit / "Hats" / "hat_02.wav").write_bytes(b"open hat")
    second = repo.commit("added a hat").commit

    assert second.parent == first.id
    assert first.parent is None
    assert [c.id for c in repo.log()] == [second.id, first.id]


def test_restore_brings_back_an_old_version_without_rewriting_history(kit):
    repo = Repo.init(kit)
    repo.commit("first")
    original = scan(kit)
    (kit / "Hats" / "hat_02.wav").write_bytes(b"open hat")
    (kit / "Kicks" / "kick_02.wav").unlink()
    (kit / "Hats" / "hat_01.wav").write_bytes(b"changed")
    repo.commit("second")

    repo.restore("v1")

    assert scan(kit) == original
    assert len(repo.log()) == 2            # nothing was thrown away
    assert repo.status() != []             # the rollback shows up as changes to commit


def test_restore_refuses_to_discard_unsaved_work(kit):
    repo = Repo.init(kit)
    repo.commit("first")
    (kit / "Hats" / "hat_02.wav").write_bytes(b"not saved yet")

    with pytest.raises(KitError):
        repo.restore("v1")
    repo.restore("v1", force=True)         # explicit --force is allowed
    assert (kit / "Hats" / "hat_02.wav").exists()   # never-tracked files are left alone


def test_versions_can_be_named_three_ways(kit):
    repo = Repo.init(kit)
    first = repo.commit("first").commit
    (kit / "Hats" / "hat_02.wav").write_bytes(b"open hat")
    second = repo.commit("second").commit

    assert repo.resolve("v1") == first.id
    assert repo.resolve("latest") == second.id
    assert repo.resolve(second.id[:10]) == second.id
    with pytest.raises(KitError):
        repo.resolve("v9")


def test_credits_follow_the_audio_not_the_file_name(kit):
    repo = Repo.init(kit, author="Nojan")
    repo.commit("first")
    (kit / "Hats" / "hat_02.wav").write_bytes(b"open hat")
    repo.commit("hat from a friend", author="Lucas")
    (kit / "Hats" / "hat_02.wav").rename(kit / "Hats" / "renamed_by_nojan.wav")
    repo.commit("tidy names")

    credits = {path: commit.author for path, commit in repo.credits().items()}

    assert credits["Hats/renamed_by_nojan.wav"] == "Lucas"
    assert credits["Kicks/kick_01.wav"] == "Nojan"


def test_an_edited_old_version_is_detected(kit):
    repo = Repo.init(kit)
    commit = repo.commit("first").commit
    path = repo.commits_dir / f"{commit.id}.json"
    path.write_bytes(path.read_bytes().replace(b"first", b"FIRST"))

    with pytest.raises(KitError, match="damaged"):
        repo.load_commit(commit.id)


def test_a_manifest_cannot_write_outside_the_kit_folder(kit, tmp_path):
    repo = Repo.init(kit)
    repo.commit("first")
    audio = repo.store.put_bytes(b"evil")
    manifest = repo.store.put_bytes(dumps({"../escaped.wav": {"hash": audio, "size": 4}}))
    bad = repo.store_commit_bytes(new_commit(repo.head(), manifest, "x", "sneaky").to_bytes())

    with pytest.raises(KitError, match="outside"):
        repo.restore(bad.id, force=True)
    assert not (tmp_path / "escaped.wav").exists()


def test_find_works_from_a_subfolder(kit):
    Repo.init(kit)
    assert Repo.find(kit / "Kicks").folder == kit.resolve()
