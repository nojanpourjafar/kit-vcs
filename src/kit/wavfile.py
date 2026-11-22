"""Reading WAV and AIFF files, including the kinds Python's own modules refuse.

Both formats are a header followed by the samples, stored in "chunks": a
4-letter name, a size, then the contents. The header says how many channels
there are, the sample rate, and what each sample is: an integer of 8, 16, 24
or 32 bits, or a 32- or 64-bit floating-point number.

Python's `wave` module only reads integer WAV samples, and before Python 3.12
not the "extensible" header many DAWs write. Its `aifc` module was removed in
Python 3.13. Sample packs use all of these, so Kit reads the headers itself.

Whatever the file, `readframes` returns samples the same way: little-endian,
with 8-bit samples unsigned as in WAV, so the code that measures them only has
to understand one layout.
"""

from __future__ import annotations

import struct
from pathlib import Path

READABLE = (".wav", ".aif", ".aiff", ".aifc", ".flac")
PCM, FLOAT, EXTENSIBLE = 1, 3, 0xFFFE
_SIGNED_TO_UNSIGNED = bytes((value + 128) % 256 for value in range(256))  # 8-bit AIFF -> 8-bit WAV


class WavError(ValueError):
    """Not a sound file this module can read."""


def can_read(name: str) -> bool:
    return name.lower().endswith(READABLE)


class _Sound:
    """An open sound file: its format, and `readframes(n)` for the raw sample bytes."""

    big_endian = False  # AIFF stores numbers the other way round
    signed_8bit = False

    def __init__(self, path: Path):
        self._file = open(path, "rb")
        try:
            self._read_header()
        except (struct.error, WavError):
            self._file.close()
            raise
        if not self.channels or not self.rate:
            self._file.close()
            raise WavError("the header does not add up")
        self.block = self.channels * self.width  # one frame = one sample for every channel
        self.frames = min(self.frames, self._data_size // self.block)
        self._left = self.frames * self.block

    def _chunks(self, number_format: str):
        """Yield (name, size) for each chunk, leaving the file at the chunk's contents."""
        f = self._file
        while True:
            header = f.read(8)
            if len(header) < 8:
                return
            name, size = struct.unpack(number_format + "4sI", header)
            start = f.tell()
            yield name, size
            f.seek(start + size + size % 2)  # chunks are padded to an even length

    def _data_here(self, size: int) -> None:
        """The samples start here; remember where, and how many bytes there are."""
        self._data_start = self._file.tell()
        end = self._file.seek(0, 2)
        # Some programs leave the size unset while recording; then the data runs to the end.
        self._data_size = min(size, end - self._data_start)

    def readframes(self, count: int) -> bytes:
        self._file.seek(self._data_start + self.frames * self.block - self._left)
        data = self._file.read(min(count * self.block, self._left))
        self._left -= len(data)
        if self.big_endian and self.width > 1:  # reverse the bytes of every sample
            flipped = bytearray(len(data))
            for k in range(self.width):
                flipped[k::self.width] = data[self.width - 1 - k::self.width]
            data = bytes(flipped)
        if self.signed_8bit and self.width == 1:
            data = data.translate(_SIGNED_TO_UNSIGNED)
        return data

    def close(self) -> None:
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class Wav(_Sound):
    def _read_header(self) -> None:
        riff, _, wave_id = struct.unpack("<4sI4s", self._file.read(12))
        if riff != b"RIFF" or wave_id != b"WAVE":
            raise WavError("not a WAV file")
        fmt = None
        for name, size in self._chunks("<"):
            if name == b"fmt ":
                fmt = self._file.read(size)
            elif name == b"data":
                if fmt is None:
                    raise WavError("sound data before the format")
                self._data_here(size)
                break
        else:
            raise WavError("no sound data in the file")

        kind, self.channels, self.rate, _, block, bits = struct.unpack("<HHIIHH", fmt[:16])
        if kind == EXTENSIBLE and len(fmt) >= 26:
            kind = struct.unpack("<H", fmt[24:26])[0]  # the first two bytes of the sub-format id
        if kind == PCM and bits in (8, 16, 24, 32):
            self.is_float = False
        elif kind == FLOAT and bits in (32, 64):
            self.is_float = True
        else:
            raise WavError(f"unsupported WAV format {kind} with {bits}-bit samples")
        self.width = bits // 8  # bytes per sample
        if block != self.channels * self.width:
            raise WavError("the WAV header does not add up")
        self.frames = self._data_size // block


class Aiff(_Sound):
    big_endian = True
    signed_8bit = True

    def _read_header(self) -> None:
        form, _, kind = struct.unpack(">4sI4s", self._file.read(12))
        if form != b"FORM" or kind not in (b"AIFF", b"AIFC"):
            raise WavError("not an AIFF file")
        comm = None
        for name, size in self._chunks(">"):
            if name == b"COMM":
                comm = self._file.read(size)
            elif name == b"SSND":
                offset, _ = struct.unpack(">II", self._file.read(8))  # usually both 0
                self._file.seek(offset, 1)
                self._data_here(size - 8 - offset)
                break
        else:
            raise WavError("no sound data in the file")
        if comm is None:
            raise WavError("sound data before the format")

        self.channels, self.frames, bits = struct.unpack(">hIh", comm[:8])
        self.rate = round(_extended(comm[8:18]))
        compression = comm[18:22] if kind == b"AIFC" else b"NONE"
        if compression in (b"NONE", b"twos") and bits in (8, 16, 24, 32):
            self.is_float = False
        elif compression == b"sowt" and bits in (16, 24, 32):  # integers stored little-endian
            self.is_float, self.big_endian = False, False
        elif compression.lower() in (b"fl32", b"fl64") and bits in (32, 64):
            self.is_float = True
        else:
            raise WavError(f"unsupported AIFF format {compression!r} with {bits}-bit samples")
        self.width = bits // 8
        if compression.lower() == b"fl64":
            self.width = 8


def _extended(raw: bytes) -> float:
    """AIFF writes the sample rate as an 80-bit floating-point number."""
    exponent, mantissa = struct.unpack(">HQ", raw)
    sign = -1 if exponent & 0x8000 else 1
    exponent &= 0x7FFF
    if exponent == 0 and mantissa == 0:
        return 0.0
    return sign * mantissa * 2.0 ** (exponent - 16383 - 63)


def open_wav(path) -> _Sound:
    """Open a WAV, AIFF or FLAC file, telling them apart by their first four bytes."""
    from .flac import id3_length, open_flac  # FLAC is decoded in Python (see flac.py)

    path = Path(path)
    with open(path, "rb") as f:
        head = f.read(10)
        f.seek(id3_length(head))  # an ID3 tag can sit in front of a FLAC file
        magic = f.read(4) if head[:3] == b"ID3" else head[:4]
    if magic == b"FORM":
        return Aiff(path)
    if magic == b"fLaC":
        return open_flac(path)
    return Wav(path)
