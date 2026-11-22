"""MP3 length and format, read from frame headers.

tests/data/mp3_samples.zip holds seven short MP3 files made with ffmpeg (LAME)
from generated sine tones whose exact lengths are known. (They are zipped
because some file transfers add tags to audio files.) Between them they cover
constant and variable bitrates, mono, MPEG-2 and 2.5 sample rates, a file with
no Xing tag, and one with ID3v2 and ID3v1 tags.
"""

import zipfile
from pathlib import Path

import pytest

from kit import mp3
from kit.audio import describe, peaks, tempo

# name: (sample rate, channels, samples in the source, kbps, variable bitrate)
SAMPLES = {
    "cbr320": (44100, 2, 44100, 320, False),
    "vbr": (44100, 2, 44100, None, True),
    "mono48": (48000, 1, 33333, 96, False),
    "mpeg2": (22050, 2, 22050, 64, False),       # MPEG-2: 576 samples a frame
    "mpeg25": (11025, 1, 9000, 32, False),       # MPEG-2.5
    "tagged": (44100, 2, 44100, 192, False),     # ID3v2 in front, ID3v1 at the end
}


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    folder = tmp_path_factory.mktemp("mp3")
    with zipfile.ZipFile(Path(__file__).resolve().parent / "data" / "mp3_samples.zip") as archive:
        archive.extractall(folder)
    return folder


@pytest.mark.parametrize("name", SAMPLES)
def test_the_length_is_exact_with_an_xing_tag(name, data):
    rate, channels, samples, kbps, vbr = SAMPLES[name]
    info = mp3.read(data / f"{name}.mp3")
    assert (info.rate, info.channels, info.vbr) == (rate, channels, vbr)
    assert info.seconds * rate == pytest.approx(samples, abs=0.01)   # to the sample: encoder padding removed
    if kbps:
        assert info.kbps == kbps


def test_without_an_xing_tag_the_frames_are_counted(data):
    info = mp3.read(data / "no_xing.mp3")
    # 40 frames of 1152 samples, padding included: what ffmpeg decodes too.
    assert info.seconds * 44100 == pytest.approx(40 * 1152) and info.kbps == 128 and not info.vbr


def test_a_file_cut_short_is_measured_by_what_is_there(data, tmp_path):
    whole = (data / "cbr320.mp3").read_bytes()
    (tmp_path / "cut.mp3").write_bytes(whole[:len(whole) // 2])
    assert mp3.read(tmp_path / "cut.mp3").seconds == pytest.approx(0.5, abs=0.05)


def test_things_that_are_not_mp3_are_refused(data, tmp_path):
    (tmp_path / "empty.mp3").write_bytes(b"")
    (tmp_path / "text.mp3").write_bytes(b"just text, no frames at all" * 100)
    (tmp_path / "noise.mp3").write_bytes(b"\xff\xfb" + bytes(range(256)) * 40)
    for name in ("empty.mp3", "text.mp3", "noise.mp3", "missing.mp3"):
        assert mp3.read(tmp_path / name) is None, name


def test_a_header_is_read_bit_by_bit():
    # FF FB: sync, MPEG-1, layer III, no CRC. 90: 128 kbps, 44.1 kHz, no padding. C4: mono.
    frame = mp3.header(bytes([0xFF, 0xFB, 0x90, 0xC4]), 0)
    assert (frame.version, frame.rate, frame.channels, frame.kbps) == (1, 44100, 1, 128)
    assert frame.size == 144 * 128000 // 44100 == 417 and frame.samples == 1152
    assert mp3.header(bytes([0xFF, 0xFB, 0x92, 0x44]), 0).size == 418               # padded, stereo
    assert mp3.header(bytes([0xFF, 0xFD, 0x90, 0xC4]), 0) is None                   # layer II
    assert mp3.header(bytes([0xFF, 0xFB, 0xF0, 0xC4]), 0) is None                   # bitrate index 15
    assert mp3.header(bytes([0xFF, 0xFB, 0x9C, 0xC4]), 0) is None                   # sample rate index 3


def test_mp3_files_are_described_like_wav_files(data):
    assert describe(data / "cbr320.mp3", "Loops/dark.mp3") == "1.00 s, 44.1 kHz, stereo, MP3 320 kbps"
    assert describe(data / "mono48.mp3", "one shot.MP3") == "0.69 s, 48 kHz, mono, MP3 96 kbps"
    assert describe(data / "vbr.mp3", "x.mp3").endswith(f"MP3 ~{mp3.read(data / 'vbr.mp3').kbps} kbps")
    assert describe(data / "cbr320.mp3", "dark.wav") is None   # named a WAV, it is not read as an MP3


def test_waveform_and_tempo_are_left_to_the_page(data):
    assert peaks(data / "cbr320.mp3", "dark.mp3") is None
    assert tempo(data / "cbr320.mp3", "dark.mp3") is None
