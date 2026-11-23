"""Stored WAV files kept as FLAC (`kit compact`), and given back byte for byte."""

import hashlib
import math
import random
import struct
import zipfile

import pytest

from kit import pack, remote
from kit.app import App
from kit.cli import main
from kit.export import export
from kit.objects import ObjectStore
from kit.repo import Repo


def chunk(name: bytes, body: bytes) -> bytes:
    return name + len(body).to_bytes(4, "little") + body + (b"\0" if len(body) % 2 else b"")


def wav(seconds=1.0, channels=2, width=2, rate=44100, tag=1, before=b"", after=b"", noise=0.001, seed=0,
        extra_bytes=b""):
    """A WAV file of a decaying tone, with optional chunks around the samples."""
    rnd = random.Random(seed)
    top = (1 << (width * 8 - 1)) - 1
    samples = bytearray()
    for i in range(int(seconds * rate)):
        x = 0.5 * math.sin(2 * math.pi * 220 * i / rate) * math.exp(-2 * i / rate)
        for c in range(channels):
            value = int(max(-1, min(1, x * (1 - 0.1 * c) + rnd.uniform(-noise, noise))) * top)
            samples += bytes([value + 128]) if width == 1 else value.to_bytes(width, "little", signed=True)
    fmt = struct.pack("<HHIIHH", tag, channels, rate, rate * channels * width, channels * width, width * 8)
    if tag == 0xFFFE:  # "extensible": the real format (PCM) is in a GUID at the end
        fmt += struct.pack("<HHI", 22, width * 8, 3) + struct.pack("<H", 1) + bytes.fromhex("000000001000800000aa00389b71")
    body = b"WAVE" + chunk(b"fmt ", fmt) + before + chunk(b"data", bytes(samples) + extra_bytes) + after
    return b"RIFF" + len(body).to_bytes(4, "little") + body


@pytest.mark.parametrize("data", [
    wav(),
    wav(channels=1, width=3, rate=48000),                                   # 24-bit mono
    wav(seconds=0.5, width=1),                                              # 8-bit
    wav(tag=0xFFFE),                                                        # extensible header
    wav(before=chunk(b"LIST", b"INFOISFT\x05\0\0\0Kit\0\0"), after=chunk(b"smpl", b"\x01" * 36)),
    wav(extra_bytes=b"\x07"),                                               # a stray byte, and the padding after it
], ids=["16-bit", "24-bit", "8-bit", "extensible", "chunks around", "odd length"])
def test_packing_gives_back_the_exact_file(data):
    packed = pack.pack(data)
    assert packed is not None and len(packed) < 0.8 * len(data)
    assert pack.unpack(packed) == data


def test_only_plain_integer_wav_files_are_packed():
    float_wav = wav()[:20] + struct.pack("<H", 3) + wav()[22:]              # the same file, labelled as float
    assert pack.pack(float_wav) is None
    assert pack.pack(b"FORM\0\0\0\x10AIFF" + b"\0" * 100) is None           # AIFF
    assert pack.pack(b"\xff\xfb\x90\xc4" * 100) is None                     # MP3
    assert pack.pack(b"just some text") is None
    assert pack.pack(wav()[:44]) is None                                     # a header with no samples
    with pytest.raises(ValueError):
        pack.unpack(b"not packed")


@pytest.fixture
def store(tmp_path):
    return ObjectStore(tmp_path / ".kit" / "objects")


def put(store, tmp_path, name, data):
    (tmp_path / name).write_bytes(data)
    return store.put_file(tmp_path / name)


def test_the_store_reads_packed_files_like_any_other(store, tmp_path):
    data = wav(seconds=1.5)
    digest = put(store, tmp_path, "loop.wav", data)
    files, before, after = store.compact()
    assert files == 1 and before == len(data) and after < 0.8 * before
    assert not store.path_for(digest).exists() and store.packed_path(digest).exists()
    assert store.has(digest) and store.intact(digest) and list(store.all_hashes()) == [digest]
    assert store.read_bytes(digest) == data
    assert store.file_for(digest).read_bytes() == data                        # unpacked once into .kit/cache
    store.copy_to(digest, tmp_path / "restored" / "loop.wav")
    assert (tmp_path / "restored" / "loop.wav").read_bytes() == data
    assert store.compact() == (0, 0, 0)                                       # nothing left to do
    assert not (tmp_path / ".kit" / "cache").exists()                         # and the cache is cleared


def test_what_would_not_shrink_is_left_alone(store, tmp_path):
    small = put(store, tmp_path, "click.wav", wav(seconds=0.05))              # under 64 KB
    noise = put(store, tmp_path, "noise.wav", wav(noise=0.9))                 # does not compress
    text = put(store, tmp_path, "notes.txt", b"x" * 100_000)                  # not audio
    assert store.compact() == (0, 0, 0)
    assert all(store.path_for(d).exists() for d in (small, noise, text))


def test_a_damaged_packed_file_is_reported(store, tmp_path):
    digest = put(store, tmp_path, "loop.wav", wav())
    store.compact()
    packed = store.packed_path(digest)
    damaged = bytearray(packed.read_bytes())
    damaged[len(damaged) // 2] ^= 0xFF
    packed.write_bytes(bytes(damaged))
    assert not store.intact(digest)


def test_a_whole_kit_works_the_same_once_compacted(tmp_path, monkeypatch):
    folder = tmp_path / "kit"
    (folder / "Loops").mkdir(parents=True)
    first = wav(seconds=1.2, seed=1)
    (folder / "Loops" / "groove.wav").write_bytes(first)
    (folder / "notes.txt").write_text("not audio")
    repo = Repo.init(folder, author="Nojan")
    repo.commit("first")
    (folder / "Loops" / "groove.wav").write_bytes(wav(seconds=1.2, seed=2))
    repo.commit("re-bounced")

    monkeypatch.chdir(folder)
    assert main(["compact"]) == 0
    assert len(list((folder / ".kit" / "objects").rglob("*.kz"))) == 2
    assert repo.verify() == []

    repo.restore("v1")                                                         # back from FLAC, byte for byte
    assert (folder / "Loops" / "groove.wav").read_bytes() == first
    repo.restore("v2", force=True)

    out, _, _ = export(repo, "v1", tmp_path)                                   # an export holds the original too
    with zipfile.ZipFile(out) as archive:
        assert archive.read("kit v1/Loops/groove.wav") == first

    v1 = repo.manifest_of(repo.resolve("v1"))["Loops/groove.wav"]["hash"]      # the app plays old versions
    path, _ = App(folder).locate({"hash": v1, "name": "Loops/groove.wav"})
    assert hashlib.sha256(path.read_bytes()).hexdigest() == v1

    shared = tmp_path / "shared"                                               # sharing sends the original files
    remote.push(repo, remote.open_remote(str(shared)))
    friend, _ = remote.clone(str(shared), tmp_path / "friend", author="Jay")
    friend.restore("v1")
    assert (tmp_path / "friend" / "Loops" / "groove.wav").read_bytes() == first
    assert friend.verify() == []


def test_the_command_says_what_it_saved(tmp_path, monkeypatch, capsys):
    (tmp_path / "loop.wav").write_bytes(wav())
    Repo.init(tmp_path, author="Nojan").commit("first")
    monkeypatch.chdir(tmp_path)
    main(["compact"])
    assert "Compressed 1 WAV file(s)" in capsys.readouterr().out
    main(["compact"])
    assert "Nothing to compress" in capsys.readouterr().out
