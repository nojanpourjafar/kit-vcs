"""Reading WAV files, including the kinds Python's own `wave` module refuses.

A WAV file is a header followed by the samples. The header says how many
channels there are, the sample rate, and what each sample is: an integer of
8, 16, 24 or 32 bits, or a 32- or 64-bit floating-point number.

Python's `wave` module only reads integer samples, and before Python 3.12 not
the "extensible" header that many DAWs write. Sample packs use both, so Kit
reads the header itself.
"""

from __future__ import annotations

import struct
from pathlib import Path

PCM, FLOAT, EXTENSIBLE = 1, 3, 0xFFFE


class WavError(ValueError):
    """Not a WAV file this module can read."""


class Wav:
    """An open WAV file: its format, and `readframes(n)` for the raw sample bytes."""

    def __init__(self, path: Path):
        self._file = open(path, "rb")
        try:
            self._read_header()
        except (struct.error, WavError):
            self._file.close()
            raise

    def _read_header(self) -> None:
        f = self._file
        riff, _, wave_id = struct.unpack("<4sI4s", f.read(12))
        if riff != b"RIFF" or wave_id != b"WAVE":
            raise WavError("not a WAV file")
        fmt = None
        while True:
            header = f.read(8)
            if len(header) < 8:
                raise WavError("no sound data in the file")
            chunk_id, size = struct.unpack("<4sI", header)
            if chunk_id == b"fmt ":
                fmt = f.read(size)
                f.seek(size % 2, 1)  # chunks are padded to an even length
            elif chunk_id == b"data":
                if fmt is None:
                    raise WavError("sound data before the format")
                self._data_start = f.tell()
                end = f.seek(0, 2)
                # Some programs leave the size unset while recording; then the data runs to the end.
                self._data_size = min(size, end - self._data_start)
                f.seek(self._data_start)
                break
            else:
                f.seek(size + size % 2, 1)  # another chunk (LIST, cue, smpl...): skip it

        kind, self.channels, self.rate, _, self.block, bits = struct.unpack("<HHIIHH", fmt[:16])
        if kind == EXTENSIBLE and len(fmt) >= 26:
            kind = struct.unpack("<H", fmt[24:26])[0]  # the first two bytes of the sub-format id
        if kind == PCM and bits in (8, 16, 24, 32):
            self.is_float = False
        elif kind == FLOAT and bits in (32, 64):
            self.is_float = True
        else:
            raise WavError(f"unsupported WAV format {kind} with {bits}-bit samples")
        if not self.channels or not self.rate or self.block != self.channels * bits // 8:
            raise WavError("the WAV header does not add up")
        self.width = bits // 8  # bytes per sample
        self.frames = self._data_size // self.block  # one frame = one sample for every channel
        self._left = self.frames * self.block

    def readframes(self, count: int) -> bytes:
        data = self._file.read(min(count * self.block, self._left))
        self._left -= len(data)
        return data

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> Wav:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def open_wav(path) -> Wav:
    return Wav(Path(path))
