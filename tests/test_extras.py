"""Hash cache, .kitignore, verify, the web page and waveforms."""

import os
import struct
import time
import wave

from kit import manifest
from kit.audio import peaks
from kit.report import build_report
from kit.repo import Repo


def _age(path, seconds=60):
    """Make a file look like it was last modified a while ago."""
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_unchanged_files_are_not_hashed_again(kit, monkeypatch):
    for path in kit.rglob("*.wav"):
        _age(path)
    repo = Repo.init(kit)
    repo.commit("first")
    hashed = []
    real = manifest.hash_file
    monkeypatch.setattr(manifest, "hash_file", lambda p: hashed.append(p.name) or real(p))

    assert repo.status() == []
    assert hashed == []                                   # everything came from the cache

    (kit / "Hats" / "hat_01.wav").write_bytes(b"a different hat")
    assert [c.path for c in repo.status()] == ["Hats/hat_01.wav"]
    assert hashed == ["hat_01.wav"]                       # only the file that changed


def test_a_just_modified_file_is_never_trusted_from_the_cache(kit):
    repo = Repo.init(kit)
    repo.commit("first")
    target = kit / "Hats" / "hat_01.wav"
    stat = target.stat()
    target.write_bytes(b"XYZ" * 100)                      # same size as before
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))   # and the same timestamp

    assert [c.kind for c in repo.status()] == ["modified"]


def test_kitignore_hides_files_and_folders_and_is_itself_tracked(kit):
    (kit / "Drafts").mkdir()
    (kit / "Drafts" / "idea.wav").write_bytes(b"rough")
    (kit / "Kicks" / "kick_01.asd").write_bytes(b"ableton analysis file")
    (kit / ".kitignore").write_text("# scratch work\nDrafts/\n*.asd\n")

    assert sorted(manifest.scan(kit)) == [
        ".kitignore", "Hats/hat_01.wav", "Kicks/kick_01.wav", "Kicks/kick_02.wav"]


def test_verify_finds_damage(kit):
    repo = Repo.init(kit)
    repo.commit("first")
    assert repo.verify() == []

    digest = manifest.scan(kit)["Kicks/kick_01.wav"]["hash"]
    repo.store.path_for(digest).write_bytes(b"bit rot")
    assert repo.verify() == [f"stored file {digest[:8]} is damaged"]

    repo.store.path_for(digest).unlink()
    assert "lost the audio for Kicks/kick_01.wav" in repo.verify()[0]


def _write_wav(path, samples, width=2):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(width)
        wav.setframerate(8000)
        if width == 2:
            wav.writeframes(b"".join(struct.pack("<h", s) for s in samples))
        else:  # 24-bit
            wav.writeframes(b"".join(struct.pack("<i", s << 8)[1:] for s in samples))


def test_waveform_follows_the_loudness_of_the_sound(tmp_path):
    loud_then_quiet = [20000, -20000] * 400 + [2000, -2000] * 400
    _write_wav(tmp_path / "a.wav", loud_then_quiet)
    _write_wav(tmp_path / "b.wav", [s * 256 for s in loud_then_quiet], width=3)

    for name in ("a.wav", "b.wav"):
        levels = peaks(tmp_path / name, name, buckets=8)
        assert len(levels) == 8
        assert levels[0] == 1.0 and levels[-1] < 0.2      # scaled so the loudest slice is 1
    assert peaks(tmp_path / "a.wav", "notes.txt") is None
    (tmp_path / "broken.wav").write_bytes(b"not really audio")
    assert peaks(tmp_path / "broken.wav", "broken.wav") is None


def test_show_builds_a_page_with_a_waveform_and_player_per_sound(tmp_path):
    folder = tmp_path / "My <Kit>"
    folder.mkdir()
    _write_wav(folder / "kick.wav", [15000, -15000] * 200)
    _write_wav(folder / "hat.wav", [8000, -8000] * 50)
    repo = Repo.init(folder, author="Nojan")
    repo.commit("first")
    (folder / "hat.wav").rename(folder / "hat_closed.wav")
    (folder / "kick.wav").unlink()
    (folder / "notes.txt").write_text("tuned to F")
    repo.commit("tidy <up>", author="Lucas")

    page = build_report(repo)

    assert page.startswith("<!DOCTYPE html>")
    assert "My &lt;Kit&gt;" in page and "tidy &lt;up&gt;" in page      # text is escaped
    assert page.count("<svg") == 1 and page.count("<audio") == 1       # one WAV in v2
    assert "was hat.wav" in page and "added by Nojan" in page          # rename keeps the credit
    assert "Removed in v2" in page and "kick.wav" in page
    assert "no waveform" in page                                       # notes.txt
    assert build_report(repo, "v1").count("<audio") == 2
