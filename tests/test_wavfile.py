"""Kit's own WAV reader: the formats Python's wave module refuses, and odd files."""

import math
import random
import struct

import pytest

from kit.audio import describe, peaks, tempo
from kit.wavfile import WavError, open_wav

RATE = 22050
GUID_TAIL = bytes.fromhex("000000001000800000aa00389b71")  # the rest of the standard sub-format id


def loop(bpm=100, bars=2, seed=0):
    """A drum loop as numbers from -1 to 1: a kick on every beat, a hat between."""
    rnd = random.Random(seed)
    beat = 60 / bpm
    total = int(RATE * bars * 4 * beat)
    out = [rnd.gauss(0, 0.01) for _ in range(total)]
    for b in range(bars * 4):
        for start, gain, length, decay in ((b * beat, 0.9, 0.25, 0.08), ((b + 0.5) * beat, 0.3, 0.04, 0.01)):
            first = int(start * RATE)
            for i in range(int(length * RATE)):
                if first + i < total:
                    out[first + i] += gain * math.exp(-i / (decay * RATE)) * math.sin(2 * math.pi * 60 * i / RATE + rnd.random())
    return [max(-1.0, min(1.0, x)) for x in out]


def wav_bytes(samples, kind="float", bits=32, channels=1, extensible=False, extra=b"", data_size=None):
    """A WAV file built by hand: header, any extra chunks, then the samples."""
    width = bits // 8
    if kind == "float":
        one = lambda x: struct.pack("<f" if bits == 32 else "<d", x)
        tag = 3
    else:
        top = 2 ** (bits - 1) - 1
        one = lambda x: int(x * top).to_bytes(width, "little", signed=True)
        tag = 1
    data = b"".join(one(x) * channels for x in samples)
    fmt = struct.pack("<HHIIHH", 0xFFFE if extensible else tag, channels, RATE, RATE * channels * width, channels * width, bits)
    if extensible:
        fmt += struct.pack("<HHI", 22, bits, 0) + struct.pack("<H", tag) + GUID_TAIL
    elif kind == "float":
        fmt += struct.pack("<H", 0)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + extra
    body += b"data" + struct.pack("<I", len(data) if data_size is None else data_size) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


@pytest.mark.parametrize("kind, bits, channels, extensible", [
    ("float", 32, 1, False),   # the common one in sample packs, refused by Python's wave module
    ("float", 64, 2, False),
    ("float", 32, 2, True),    # extensible header, float samples
    ("int", 24, 2, True),      # extensible header, 24-bit samples (refused by wave before Python 3.12)
    ("int", 16, 1, False),
])
def test_waveform_length_and_tempo_for_every_format(tmp_path, kind, bits, channels, extensible):
    path = tmp_path / "loop.wav"
    path.write_bytes(wav_bytes(loop(100), kind, bits, channels, extensible))
    with open_wav(path) as wav:
        assert (wav.rate, wav.channels, wav.width, wav.is_float) == (RATE, channels, bits // 8, kind == "float")
    layout = "mono" if channels == 1 else "stereo"
    assert describe(path, "loop.wav") == f"4.80 s, 22.05 kHz, {layout}"
    levels = peaks(path, "loop.wav")
    assert len(levels) > 80 and max(levels) == 1.0
    assert tempo(path, "loop.wav") == pytest.approx(100, abs=1)


def test_other_chunks_are_skipped(tmp_path):
    # LIST (tags) with an odd size, which is padded with one byte, and a cue chunk.
    extra = b"LIST" + struct.pack("<I", 5) + b"INFOx" + b"\0" + b"cue " + struct.pack("<I", 4) + b"\0\0\0\0"
    path = tmp_path / "tagged.wav"
    path.write_bytes(wav_bytes([0.5, -0.5] * 2000, extra=extra))
    assert describe(path, "tagged.wav") == "0.18 s, 22.05 kHz, mono"
    assert peaks(path, "tagged.wav")[0] == 1.0


def test_a_data_size_left_unset_runs_to_the_end_of_the_file(tmp_path):
    path = tmp_path / "recorded.wav"
    path.write_bytes(wav_bytes([0.25] * RATE, data_size=0xFFFFFFFF))
    with open_wav(path) as wav:
        assert wav.frames == RATE


def test_files_that_are_not_readable_wavs_get_nothing(tmp_path):
    adpcm = wav_bytes([0.1] * 100, kind="int", bits=16)
    adpcm = adpcm.replace(struct.pack("<HH", 1, 1), struct.pack("<HH", 2, 1), 1)  # a compressed WAV format
    for name, data in [("adpcm.wav", adpcm), ("text.wav", b"not audio at all"), ("cut.wav", wav_bytes([0.1] * 100)[:30]),
                       ("empty.wav", b"")]:
        (tmp_path / name).write_bytes(data)
        assert describe(tmp_path / name, name) is None, name
        assert peaks(tmp_path / name, name) is None, name
        assert tempo(tmp_path / name, name) is None, name
        with pytest.raises((WavError, struct.error)):
            open_wav(tmp_path / name)
