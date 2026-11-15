"""Tests for sharing: remotes, push, pull, clone and merging two people's work."""

import pytest

from kit import remote
from kit.manifest import scan
from kit.remote import FolderRemote
from kit.repo import KitError, Repo


def test_folder_remote_stores_objects_commits_and_head(kit, tmp_path):
    repo = Repo.init(kit)
    commit = repo.commit("first").commit
    rem = FolderRemote(tmp_path / "remote")
    digest = scan(kit)["Kicks/kick_01.wav"]["hash"]

    assert rem.read_head() is None
    assert not rem.has_object(digest)

    rem.upload_object(digest, repo.store.path_for(digest))
    rem.write_commit(commit.id, repo.read_commit_bytes(commit.id))
    rem.write_head(commit.id)
    rem.download_object(digest, tmp_path / "back.wav")

    assert rem.has_object(digest)
    assert rem.read_commit(commit.id) == repo.read_commit_bytes(commit.id)
    assert rem.read_head() == commit.id
    assert (tmp_path / "back.wav").read_bytes() == (kit / "Kicks" / "kick_01.wav").read_bytes()


def _second_repo(tmp_path, author="Friend"):
    folder = tmp_path / "friend_kit"
    folder.mkdir()
    return Repo.init(folder, author=author)


def _shared(kit, tmp_path):
    """Nojan's repo, a friend's copy of it, and the remote between them."""
    mine = Repo.init(kit, author="Nojan")
    mine.commit("first")
    rem = FolderRemote(tmp_path / "remote")
    remote.push(mine, rem)
    theirs = _second_repo(tmp_path, author="Lucas")
    remote.pull(theirs, rem)
    return mine, theirs, rem


def test_push_then_pull_copies_the_kit_to_a_friend(kit, tmp_path):
    mine = Repo.init(kit, author="Nojan")
    mine.commit("first")
    rem = FolderRemote(tmp_path / "remote")

    sent = remote.push(mine, rem)
    theirs = _second_repo(tmp_path)
    got = remote.pull(theirs, rem)

    assert sent.commits == got.commits == 1
    assert sent.objects == got.objects == 4        # 3 sounds + 1 manifest
    assert got.outcome == "fast-forward"
    assert scan(theirs.folder) == scan(kit)
    assert theirs.head() == mine.head()
    assert theirs.status() == []


def test_a_second_push_sends_only_what_is_new(kit, tmp_path):
    mine = Repo.init(kit)
    mine.commit("first")
    rem = FolderRemote(tmp_path / "remote")
    remote.push(mine, rem)
    (kit / "Hats" / "hat_02.wav").write_bytes(b"open hat")
    mine.commit("added a hat")

    sent = remote.push(mine, rem)

    assert sent.commits == 1
    assert sent.objects == 2                       # the new hat + the new manifest
    assert remote.push(mine, rem) == remote.SyncResult(0, 0)   # nothing left to send


def test_push_is_refused_when_a_friend_pushed_first(kit, tmp_path):
    mine, theirs, rem = _shared(kit, tmp_path)
    (theirs.folder / "Hats" / "hat_friend.wav").write_bytes(b"friend's hat")
    theirs.commit("friend's hat")
    remote.push(theirs, rem)
    (kit / "Hats" / "hat_mine.wav").write_bytes(b"my hat")
    mine.commit("my hat")

    with pytest.raises(KitError):
        remote.push(mine, rem)                     # would cut the friend's version out
    assert rem.read_head() == theirs.head()


def test_pull_is_refused_with_unsaved_changes(kit, tmp_path):
    mine, theirs, rem = _shared(kit, tmp_path)
    (kit / "Hats" / "hat_02.wav").write_bytes(b"open hat")
    mine.commit("added a hat")
    remote.push(mine, rem)
    (theirs.folder / "scratch.wav").write_bytes(b"not saved yet")

    with pytest.raises(KitError):
        remote.pull(theirs, rem)


def test_pull_when_already_up_to_date_changes_nothing(kit, tmp_path):
    mine, theirs, rem = _shared(kit, tmp_path)
    again = remote.pull(theirs, rem)
    assert (again.commits, again.objects, again.outcome) == (0, 0, "up-to-date")


def test_pull_merges_when_both_sides_added_sounds(kit, tmp_path):
    mine, theirs, rem = _shared(kit, tmp_path)
    (theirs.folder / "Hats" / "hat_lucas.wav").write_bytes(b"lucas hat")
    theirs.commit("a hat from Lucas")
    remote.push(theirs, rem)
    (kit / "Kicks" / "kick_03.wav").write_bytes(b"third kick")
    mine.commit("a third kick")

    got = remote.pull(mine, rem)

    assert got.outcome == "merge" and got.conflicts == []
    assert (kit / "Hats" / "hat_lucas.wav").exists()          # their sound arrived
    assert (kit / "Kicks" / "kick_03.wav").exists()           # ours is still here
    merge = mine.load_commit(mine.head())
    assert merge.merged == theirs.head()                      # a commit with two parents
    assert [c.message for c in mine.log()][1:] == ["a third kick", "first"]

    remote.push(mine, rem)                                    # now the push goes through
    remote.pull(theirs, rem)                                  # and the friend catches up
    assert scan(theirs.folder) == scan(kit)
    assert theirs.head() == mine.head()


def test_when_both_change_the_same_sound_both_versions_are_kept(kit, tmp_path):
    mine, theirs, rem = _shared(kit, tmp_path)
    (theirs.folder / "Hats" / "hat_01.wav").write_bytes(b"lucas made it brighter")
    theirs.commit("brighter hat")
    remote.push(theirs, rem)
    (kit / "Hats" / "hat_01.wav").write_bytes(b"nojan made it shorter")
    mine.commit("shorter hat")

    got = remote.pull(mine, rem)

    assert got.conflicts == ["Hats/hat_01.wav"]
    assert (kit / "Hats" / "hat_01.wav").read_bytes() == b"nojan made it shorter"
    assert (kit / "Hats" / "hat_01 (from Lucas).wav").read_bytes() == b"lucas made it brighter"
    assert mine.status() == []                                # the merge is already saved


def test_a_contributor_keeps_credit_after_a_merge(kit, tmp_path):
    mine, theirs, rem = _shared(kit, tmp_path)
    (theirs.folder / "Hats" / "hat_lucas.wav").write_bytes(b"lucas hat")
    theirs.commit("a hat from Lucas")
    remote.push(theirs, rem)
    (kit / "Kicks" / "kick_03.wav").write_bytes(b"third kick")
    mine.commit("a third kick")
    remote.pull(mine, rem)

    credits = {path: commit.author for path, commit in mine.credits().items()}

    assert credits["Hats/hat_lucas.wav"] == "Lucas"
    assert credits["Kicks/kick_03.wav"] == "Nojan"
    assert credits["Kicks/kick_01.wav"] == "Nojan"


def test_clone_makes_a_working_copy_that_remembers_its_remote(kit, tmp_path):
    mine = Repo.init(kit, author="Nojan")
    mine.commit("first")
    remote.push(mine, FolderRemote(tmp_path / "remote"))

    fork, got = remote.clone(str(tmp_path / "remote"), tmp_path / "fork", author="Lucas")

    assert got.outcome == "fast-forward"
    assert scan(fork.folder) == scan(kit)
    assert fork.author == "Lucas"
    assert fork.remote() == str((tmp_path / "remote").resolve())
    with pytest.raises(KitError):
        remote.clone(str(tmp_path / "remote"), tmp_path / "fork")     # not empty any more
    with pytest.raises(KitError):
        remote.clone(str(tmp_path / "nothing_here"), tmp_path / "other")


def test_a_damaged_download_is_rejected(kit, tmp_path):
    mine = Repo.init(kit)
    mine.commit("first")
    rem = FolderRemote(tmp_path / "remote")
    remote.push(mine, rem)
    digest = scan(kit)["Kicks/kick_01.wav"]["hash"]
    rem.store.path_for(digest).write_bytes(b"corrupted on the way")

    with pytest.raises(KitError, match="did not match"):
        remote.pull(_second_repo(tmp_path), rem)
