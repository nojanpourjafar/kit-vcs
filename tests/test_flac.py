"""FLAC decoding, checked against ffmpeg.

tests/data/flac_samples.zip holds four FLAC files made with ffmpeg from
generated drum loops. (They are zipped because some file transfers add tags to
audio files on the way, which would change them.) Each expected fingerprint is
the SHA-256 of the samples that ffmpeg itself decoded (`ffmpeg -i file.flac
-f s16le -`, or `-f s24le`), so this compares Kit's decoder with an independent
one, sample for sample.
"""

import hashlib
import shutil
import zipfile
from pathlib import Path

import pytest

from kit.audio import describe, peaks, tempo
from kit.flac import FlacError, open_flac


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    folder = tmp_path_factory.mktemp("flac")
    with zipfile.ZipFile(Path(__file__).resolve().parent / "data" / "flac_samples.zip") as archive:
        archive.extractall(folder)
    return folder

DECODED_BY_FFMPEG = {
    # file: (sample rate, channels, bytes per sample, frames, SHA-256 of the samples)
    "loop_100bpm_mono.flac": (11025, 1, 2, 26460, "918bb5fc3c21437f60c53004b41f79539099b702a6ac5679b31307db9b65d236"),
    "stereo_16bit.flac": (22050, 2, 2, 11025, "54b8cd639c763eacd8a342626f7c4c6bee5b8a68703b36f5af8225ebb87a161c"),
    "fixed_predictors.flac": (22050, 2, 2, 11025, "54b8cd639c763eacd8a342626f7c4c6bee5b8a68703b36f5af8225ebb87a161c"),
    "stereo_24bit.flac": (22050, 2, 3, 11025, "da45012e75ef0922dd6c019b49bf35940cf395b626917f76ea16cbad5a0012e7"),
}


@pytest.mark.parametrize("name", sorted(DECODED_BY_FFMPEG))
def test_samples_match_what_ffmpeg_decodes(name, data):
    rate, channels, width, frames, fingerprint = DECODED_BY_FFMPEG[name]
    with open_flac(data / name) as flac:
        assert (flac.rate, flac.channels, flac.width, flac.frames) == (rate, channels, width, frames)
        samples = b""
        while chunk := flac.readframes(1000):           # in pieces, as the waveform code reads
            samples += chunk
    assert hashlib.sha256(samples).hexdigest() == fingerprint


def test_a_flac_loop_gets_its_length_waveform_and_tempo(data):
    path = data / "loop_100bpm_mono.flac"
    assert describe(path, "loop.flac") == "2.40 s, 11.025 kHz, mono"
    levels = peaks(path, "loop.flac")
    assert len(levels) > 80 and max(levels) == 1.0
    assert tempo(path, "loop.flac") == pytest.approx(100, abs=1)


def test_damaged_flac_files_get_nothing(tmp_path, data):
    whole = (data / "stereo_16bit.flac").read_bytes()
    cut = tmp_path / "cut.flac"
    cut.write_bytes(whole[: len(whole) // 2])                # cut off halfway, as by a failed download
    fake = tmp_path / "fake.flac"
    fake.write_bytes(b"fLaC" + b"\x00" * 10)
    for path in (cut, fake):
        assert peaks(path, path.name) is None and tempo(path, path.name) is None
    with pytest.raises(FlacError):
        open_flac(fake)


def test_flac_is_found_by_its_first_bytes_not_its_name(tmp_path, data):
    # Kit's stored copies are named by hash, with no ".flac" on the end.
    stored = tmp_path / "3f9a2c"
    shutil.copy(data / "stereo_16bit.flac", stored)
    assert describe(stored, "Loops/stereo.flac") == "0.50 s, 22.05 kHz, stereo"


def test_an_id3_tag_in_front_is_skipped(tmp_path, data):
    # Some programs (and some file transfers) put an ID3 tag before the FLAC data.
    payload = b"TIT2" + b"\x00\x00\x00\x06" + b"\x00\x00" + b"\x00Loop!" + b"\x00" * 300
    size = len(payload)
    syncsafe = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
    tagged = tmp_path / "tagged.flac"
    tagged.write_bytes(b"ID3\x04\x00\x00" + syncsafe + payload + (data / "stereo_16bit.flac").read_bytes())

    with open_flac(tagged) as flac, open_flac(data / "stereo_16bit.flac") as plain:
        assert flac.readframes(20000) == plain.readframes(20000)
    assert describe(tagged, "tagged.flac") == "0.50 s, 22.05 kHz, stereo"
