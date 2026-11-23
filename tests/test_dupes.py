"""Finding the same sound in more than one place, even renamed or re-bounced."""

import math
import random
import shutil

import pytest

from kit import fingerprint as fp_module
from kit.cli import main
from kit.fingerprint import find_duplicates, fingerprint, same_sound
from kit.flacenc import encode
from kit.repo import Repo

RATE = 44100


def write(path, samples, rate=RATE, width=2, channels=1, gain=1.0):
    import wave
    path.parent.mkdir(parents=True, exist_ok=True)
    top = (1 << (8 * width - 1)) - 1
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(b"".join(int(max(-1, min(1, x * gain)) * top).to_bytes(width, "little", signed=True) * channels
                                 for x in samples))
    return path


def kick(hz=50, decay=0.2, rate=RATE):
    return [0.9 * math.sin(2 * math.pi * (hz * i / rate + 3.6 * (1 - math.exp(-i / rate / 0.03)))) * math.exp(-i / rate / decay)
            for i in range(int(0.5 * rate))]


def snare(seed=1, body=200):
    rnd = random.Random(seed)
    return [(rnd.uniform(-1, 1) * 0.6 + 0.4 * math.sin(2 * math.pi * body * i / RATE)) * math.exp(-i / RATE / 0.07)
            for i in range(int(0.35 * RATE))]


def fp(path):
    return fingerprint(path, path.name)


def test_copies_of_one_sound_match(tmp_path):
    original = fp(write(tmp_path / "kick.wav", kick()))
    copies = [
        write(tmp_path / "24.wav", kick(), width=3),                     # re-bounced at 24-bit
        write(tmp_path / "quiet.wav", kick(), gain=0.4),                 # turned down 8 dB
        write(tmp_path / "stereo.wav", kick(), channels=2),
        write(tmp_path / "48k.wav", kick(rate=48000), rate=48000),       # the same sound at 48 kHz
    ]
    flac = tmp_path / "kick.flac"
    with __import__("wave").open(str(tmp_path / "kick.wav")) as wav:
        flac.write_bytes(encode(wav.readframes(wav.getnframes()), RATE, 1, 16))
    for copy in [*copies, flac]:
        assert same_sound(original, fp(copy)), copy.name


def test_different_sounds_do_not_match(tmp_path):
    sounds = [write(tmp_path / f"{n}.wav", s) for n, s in [
        ("kick", kick()), ("lower", kick(hz=44)), ("longer", kick(decay=0.3)), ("short", kick(decay=0.1)),
        ("snare", snare()), ("other snare", snare(seed=7, body=240))]]
    prints = [fp(s) for s in sounds]
    for i in range(len(prints)):
        for j in range(i + 1, len(prints)):
            assert not same_sound(prints[i], prints[j]), (sounds[i].name, sounds[j].name)


def test_silence_mp3s_and_tiny_files_have_no_fingerprint(tmp_path):
    assert fp(write(tmp_path / "silent.wav", [0.0] * RATE)) is None
    assert fp(write(tmp_path / "tiny.wav", [0.5] * 50)) is None
    shutil.copy(tmp_path / "silent.wav", tmp_path / "x.mp3")
    assert fp(tmp_path / "x.mp3") is None


@pytest.fixture
def two_kits(tmp_path):
    a, b = tmp_path / "Dark Trap", tmp_path / "Drill Pack"
    write(a / "Drums" / "Kick Punch.wav", kick())
    write(a / "Drums" / "Snare.wav", snare())
    (b / "Snares").mkdir(parents=True)
    shutil.copy(a / "Drums" / "Snare.wav", b / "Snares" / "snr 01.wav")                # the same file, renamed
    write(b / "Kick 24bit.wav", kick(), width=3)                         # the same kick, re-bounced
    write(b / "Other Kick.wav", kick(hz=62, decay=0.1))
    (b / "notes.txt").write_text("not audio")
    Repo.init(a, author="Nojan")
    Repo.init(b, author="Lucas")
    return a, b


def test_duplicates_across_two_kits(two_kits):
    a, b = two_kits
    groups = find_duplicates([a, b])
    found = [(g.exact, sorted(f"{p.kit}/{p.path}" for p in g.places)) for g in groups]
    assert sorted(found) == [
        (False, ["Dark Trap/Drums/Kick Punch.wav", "Drill Pack/Kick 24bit.wav"]),
        (True, ["Dark Trap/Drums/Snare.wav", "Drill Pack/Snares/snr 01.wav"]),
    ]
    snare_group = next(g for g in groups if g.exact)
    assert snare_group.extra_bytes == (a / "Drums" / "Snare.wav").stat().st_size


def test_fingerprints_are_kept_for_next_time(two_kits, monkeypatch):
    a, b = two_kits
    find_duplicates([a, b])
    assert (a / ".kit" / "fingerprints.json").exists() and (b / ".kit" / "fingerprints.json").exists()
    made = []
    real = fp_module.fingerprint
    monkeypatch.setattr(fp_module, "fingerprint", lambda path, name: made.append(name) or real(path, name))
    find_duplicates([a, b])
    assert made == []                                                     # every one came from the saved fingerprints


def test_plain_folders_are_read_but_never_written(tmp_path):
    loose = tmp_path / "Downloads pack"
    write(loose / "kick.wav", kick())
    write(loose / "kick copy.wav", kick(), gain=0.5)
    groups = find_duplicates([loose])
    assert len(groups) == 1 and not groups[0].exact
    assert sorted(p.name for p in loose.iterdir()) == ["kick copy.wav", "kick.wav"]


def test_the_command(two_kits, monkeypatch, capsys):
    a, b = two_kits
    monkeypatch.chdir(a)
    main(["dupes", str(a), str(b)])
    out = capsys.readouterr().out
    assert "The same file, 2 places:\n  Dark Trap/Drums/Snare.wav\n  Drill Pack/Snares/snr 01.wav" in out
    assert "The same sound in different files, 2 places:" in out and "2 sound(s) are in more than one place" in out
    main(["dupes"])                                                       # this kit alone: nothing twice in it
    assert "No sound is in more than one place." in capsys.readouterr().out
