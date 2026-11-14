"""Tests for sharing.

The FolderRemote tests pass today. The four marked `yours_to_write` describe
what push and pull must do. They show as "xfailed" until you write the two
functions in kit/remote.py, then as "xpassed". When all four xpass,
delete the `@yours_to_write` lines so they become ordinary tests.
"""

import pytest

from kit import remote
from kit.manifest import scan
from kit.remote import FolderRemote
from kit.repo import KitError, Repo

yours_to_write = pytest.mark.xfail(
    raises=NotImplementedError, reason="push and pull are yours to write, in remote.py")


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


@yours_to_write
def test_push_then_pull_copies_the_kit_to_a_friend(kit, tmp_path):
    mine = Repo.init(kit, author="Nojan")
    mine.commit("first")
    rem = FolderRemote(tmp_path / "remote")

    sent = remote.push(mine, rem)
    theirs = _second_repo(tmp_path)
    got = remote.pull(theirs, rem)

    assert sent.commits == got.commits == 1
    assert sent.objects == got.objects == 4        # 3 sounds + 1 manifest
    assert scan(theirs.folder) == scan(kit)
    assert theirs.head() == mine.head()
    assert theirs.status() == []


@yours_to_write
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


@yours_to_write
def test_push_is_refused_when_a_friend_pushed_first(kit, tmp_path):
    mine = Repo.init(kit)
    mine.commit("first")
    rem = FolderRemote(tmp_path / "remote")
    remote.push(mine, rem)
    theirs = _second_repo(tmp_path)
    remote.pull(theirs, rem)

    (theirs.folder / "Hats" / "hat_friend.wav").write_bytes(b"friend's hat")
    theirs.commit("friend's hat")
    remote.push(theirs, rem)
    (kit / "Hats" / "hat_mine.wav").write_bytes(b"my hat")
    mine.commit("my hat")

    with pytest.raises(KitError):
        remote.push(mine, rem)                     # would wipe out the friend's version
    assert rem.read_head() == theirs.head()


@yours_to_write
def test_pull_is_refused_with_unsaved_changes(kit, tmp_path):
    mine = Repo.init(kit)
    mine.commit("first")
    rem = FolderRemote(tmp_path / "remote")
    remote.push(mine, rem)
    theirs = _second_repo(tmp_path)
    remote.pull(theirs, rem)
    (kit / "Hats" / "hat_02.wav").write_bytes(b"open hat")
    mine.commit("added a hat")
    remote.push(mine, rem)

    (theirs.folder / "scratch.wav").write_bytes(b"not saved yet")

    with pytest.raises(KitError):
        remote.pull(theirs, rem)
