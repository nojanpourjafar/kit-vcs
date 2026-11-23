"""The FLAC encoder: whatever goes in must come out again, sample for sample."""

import hashlib
import math
import random
import shutil
import struct
import subprocess

import pytest

from kit.flac import open_flac
from kit.flacenc import BitWriter, FlacEncodeError, _crc8, _crc16, _utf8_number, encode


def pcm(frames, channels=2, width=2, kind="tone", seed=0):
    """Interleaved little-endian samples like a WAV file's (8-bit unsigned)."""
    rnd = random.Random(seed)
    top = (1 << (width * 8 - 1)) - 1
    out = bytearray()
    for i in range(frames):
        base = 0.6 * math.sin(2 * math.pi * 110 * i / 44100) * math.exp(-i / 30000)
        for c in range(channels):
            x = base * (1 - 0.1 * c) + (rnd.uniform(-0.9, 0.9) if kind == "noise" else rnd.uniform(-0.0005, 0.0005))  # a little hiss
            value = int(max(-1.0, min(1.0, x)) * top) if kind != "silence" else 0
            if width == 1:
                out.append(value + 128)
            else:
                out += value.to_bytes(width, "little", signed=True)
    return bytes(out)


def round_trip(tmp_path, data, rate, channels, bits):
    encoded = encode(data, rate, channels, bits)
    (tmp_path / "x.flac").write_bytes(encoded)
    with open_flac(tmp_path / "x.flac") as flac:
        assert (flac.rate, flac.channels, flac.bits) == (rate, channels, bits)
        assert flac.frames == len(data) // channels // ((bits + 7) // 8)
        return encoded, flac.readframes(flac.frames)


@pytest.mark.parametrize("frames, channels, width, kind", [
    (10000, 2, 2, "tone"),       # stereo 16-bit: left and right nearly the same, so "side" coding wins
    (9000, 1, 3, "tone"),        # mono 24-bit
    (5000, 2, 1, "tone"),        # 8-bit, which WAV stores unsigned
    (4096 * 2, 2, 2, "tone"),    # exactly two blocks
    (4097, 2, 2, "noise"),       # one sample into a second block; noise does not compress
    (3000, 2, 2, "silence"),     # constant blocks
    (1, 1, 2, "tone"),           # a single sample
    (6000, 4, 2, "tone"),        # more than two channels
])
def test_the_samples_come_back_exactly(tmp_path, frames, channels, width, kind):
    data = pcm(frames, channels, width, kind)
    encoded, decoded = round_trip(tmp_path, data, 44100, channels, width * 8)
    assert decoded == data
    if kind == "tone" and frames > 1000:
        assert len(encoded) < 0.75 * len(data)
    if kind == "silence":
        assert len(encoded) < 200


def test_nothing_at_all(tmp_path):
    encoded, decoded = round_trip(tmp_path, b"", 44100, 2, 16)
    assert decoded == b"" and encoded.startswith(b"fLaC")


def test_the_stored_md5_is_that_of_the_samples():
    data = pcm(5000)
    assert encode(data, 44100, 2, 16)[26:42] == hashlib.md5(data).digest()


def test_float_and_32_bit_audio_is_refused():
    for bits in (32, 64, 4):
        with pytest.raises(FlacEncodeError):
            encode(b"\0" * 64, 44100, 2, bits)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")
def test_ffmpeg_reads_it_and_finds_no_errors(tmp_path):
    data = pcm(20000, kind="tone", seed=3)
    (tmp_path / "x.flac").write_bytes(encode(data, 48000, 2, 16))
    result = subprocess.run(["ffmpeg", "-v", "error", "-err_detect", "crccheck+bitstream+explode",
                             "-i", str(tmp_path / "x.flac"), "-f", "s16le", "-"], capture_output=True)
    assert result.stderr == b"" and result.stdout == data


def test_checksums_match_the_standard_check_values():
    assert _crc8(b"123456789") == 0xF4              # CRC-8, polynomial 0x07
    assert _crc16(b"123456789") == 0xFEE8           # CRC-16/UMTS: polynomial 0x8005, as FLAC uses


@pytest.mark.parametrize("number, expected", [
    (0, b"\x00"), (0x7F, b"\x7f"), (0x80, b"\xc2\x80"), (0x7FF, b"\xdf\xbf"), (0x800, b"\xe0\xa0\x80"),
    (0x10000, b"\xf0\x90\x80\x80"),
])
def test_frame_numbers_are_written_like_utf8(number, expected):
    assert _utf8_number(number) == expected
    if number < 0x10000:
        assert _utf8_number(number) == chr(number).encode("utf-8")


def test_rice_codes():
    out = BitWriter()
    # With k = 1: 0 is "1 0", 1 is "1 1", and 5 (binary 101) is "0 0 1 1":
    # its high part, 2, as two zeros and the closing one, then its low bit.
    out.rice([0, 1, 5], 1)
    assert out.bytes() == bytes([0b10110011])
