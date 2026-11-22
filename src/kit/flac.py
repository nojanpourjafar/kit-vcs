"""Decoding FLAC files, in plain Python.

FLAC is lossless: it stores exactly the samples of a WAV file, in about half
the space. It does this by predicting each sample from the ones before it and
storing only the small difference (the "residual"), packed with Rice codes,
which spend few bits on small numbers.

A FLAC file is "fLaC", a few metadata blocks (the first, STREAMINFO, gives the
sample rate, channels and bit depth), then frames of a few thousand samples
each. Every frame stores each channel as a "subframe" in one of four ways:
one constant value, the raw samples, a fixed predictor, or a linear predictor
with its own coefficients (LPC).

Decoding in Python is slow next to C, about a second for a minute of stereo
audio, so callers read only what they need (see MAX_SECONDS in audio.py).
"""

from __future__ import annotations

import struct
from pathlib import Path


class FlacError(ValueError):
    """Not a FLAC file this module can read."""


class Bits:
    """Reads a byte string bit by bit, most significant bit first."""

    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos * 8  # in bits

    def read(self, count: int) -> int:
        if count == 0:
            return 0
        start, end = self.pos, self.pos + count
        if end > len(self.data) * 8:
            raise FlacError("the file ends in the middle of a frame")
        first, last = start >> 3, (end + 7) >> 3
        chunk = int.from_bytes(self.data[first:last], "big")
        self.pos = end
        return (chunk >> ((last << 3) - end)) & ((1 << count) - 1)

    def signed(self, count: int) -> int:
        value = self.read(count)
        return value - (1 << count) if count and value >> (count - 1) else value

    def unary(self) -> int:
        """Count zero bits up to the next one bit (and skip that one)."""
        count = 0
        data, pos = self.data, self.pos
        while True:
            byte_index = pos >> 3
            if byte_index >= len(data):
                raise FlacError("the file ends in the middle of a frame")
            rest = data[byte_index] & (0xFF >> (pos & 7))  # the bits of this byte not read yet
            if rest:
                zeros = (8 - rest.bit_length()) - (pos & 7)  # zeros before the first one bit
                self.pos = pos + zeros + 1
                return count + zeros
            count += 8 - (pos & 7)
            pos = (byte_index + 1) << 3

    def align(self) -> None:
        self.pos = (self.pos + 7) & ~7

    def utf8_number(self) -> None:
        """Skip a frame number, which FLAC writes like a UTF-8 character."""
        first = self.read(8)
        extra = 0
        while first & (0x80 >> extra):
            extra += 1
        for _ in range(max(0, extra - 1)):
            self.read(8)


def id3_length(head: bytes) -> int:
    """How many bytes an ID3v2 tag at the start takes up, or 0 if there is none.

    Some programs put an ID3 tag (title, artist, artwork) in front of a FLAC
    file. It is not part of FLAC, so it is skipped. Its size is stored in 4
    bytes of 7 bits each ("syncsafe"), plus 10 bytes of header and, when the
    footer flag is set, 10 more of footer.
    """
    if len(head) < 10 or head[:3] != b"ID3":
        return 0
    size = (head[6] & 0x7F) << 21 | (head[7] & 0x7F) << 14 | (head[8] & 0x7F) << 7 | (head[9] & 0x7F)
    return 10 + size + (10 if head[5] & 0x10 else 0)


class Flac:
    """An open FLAC file: rate, channels, width (bytes per sample), frames, and readframes(n)."""

    is_float = False

    def __init__(self, path: Path):
        self.data = Path(path).read_bytes()
        start = id3_length(self.data)
        if self.data[start:start + 4] != b"fLaC":
            raise FlacError("not a FLAC file")
        pos, info = start + 4, None
        while True:
            if pos + 4 > len(self.data):
                raise FlacError("the file ends inside its header")
            header = self.data[pos]
            length = int.from_bytes(self.data[pos + 1:pos + 4], "big")
            if header & 0x7F == 0:  # STREAMINFO
                info = self.data[pos + 4:pos + 4 + length]
            pos += 4 + length
            if header & 0x80:  # the last metadata block
                break
        if info is None or len(info) < 18:
            raise FlacError("no STREAMINFO block")
        packed = int.from_bytes(info[10:18], "big")
        self.rate = packed >> 44
        self.channels = ((packed >> 41) & 0x7) + 1
        self.bits = ((packed >> 36) & 0x1F) + 1
        self.frames = packed & ((1 << 36) - 1)  # samples per channel; 0 means unknown
        if not self.rate or self.bits not in (8, 12, 16, 20, 24, 32):
            raise FlacError("unusual FLAC format")
        self.width = 1 if self.bits <= 8 else 2 if self.bits <= 16 else 3 if self.bits <= 24 else 4
        self._pos = pos
        self._buffered = bytearray()  # decoded samples not handed out yet

    # ---- the interface audio.py uses (the same as for WAV and AIFF) -----------

    def readframes(self, count: int) -> bytes:
        """The next `count` frames as little-endian samples, like a WAV file's."""
        want = count * self.channels * self.width
        while len(self._buffered) < want and self._pos < len(self.data):
            self._buffered += self._decode_frame()
        out = bytes(self._buffered[:want])
        del self._buffered[:want]
        return out

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        pass

    # ---- decoding ----------------------------------------------------------

    def _decode_frame(self) -> bytes:
        bits = Bits(self.data, self._pos)
        if bits.read(15) != 0b111111111111100:  # the frame sync code
            raise FlacError("lost track of the frames")
        bits.read(1)  # blocking strategy
        size_code, rate_code = bits.read(4), bits.read(4)
        assignment, size_bits_code = bits.read(4), bits.read(3)
        bits.read(1)
        bits.utf8_number()
        if size_code == 1:
            block = 192
        elif 2 <= size_code <= 5:
            block = 576 << (size_code - 2)
        elif size_code == 6:
            block = bits.read(8) + 1
        elif size_code == 7:
            block = bits.read(16) + 1
        elif size_code >= 8:
            block = 256 << (size_code - 8)
        else:
            raise FlacError("reserved block size")
        if rate_code == 12:
            bits.read(8)
        elif rate_code in (13, 14):
            bits.read(16)
        sample_bits = {0: self.bits, 1: 8, 2: 12, 4: 16, 5: 20, 6: 24, 7: 32}.get(size_bits_code)
        if sample_bits is None:
            raise FlacError("reserved sample size")
        bits.read(8)  # CRC-8 of the header

        channels = assignment + 1 if assignment < 8 else 2
        decoded = []
        for channel in range(channels):
            extra = 1 if (assignment == 8 and channel == 1) or (assignment == 9 and channel == 0) \
                or (assignment == 10 and channel == 1) else 0  # the side channel needs one more bit
            decoded.append(self._subframe(bits, block, sample_bits + extra))
        bits.align()
        bits.read(16)  # CRC-16 of the frame
        self._pos = bits.pos >> 3

        if assignment == 8:  # left and side
            left, side = decoded
            decoded = [left, [l - s for l, s in zip(left, side)]]
        elif assignment == 9:  # side and right
            side, right = decoded
            decoded = [[s + r for s, r in zip(side, right)], right]
        elif assignment == 10:  # mid and side
            mid, side = decoded
            left, right = [], []
            for m, s in zip(mid, side):
                m = (m << 1) | (s & 1)
                left.append((m + s) >> 1)
                right.append((m - s) >> 1)
            decoded = [left, right]
        return self._pack(decoded, sample_bits)

    def _subframe(self, bits: Bits, block: int, sample_bits: int) -> list[int]:
        if bits.read(1):
            raise FlacError("bad subframe")
        kind = bits.read(6)
        wasted = 0
        if bits.read(1):
            wasted = bits.unary() + 1
            sample_bits -= wasted
        if kind == 0:  # one value for the whole block
            samples = [bits.signed(sample_bits)] * block
        elif kind == 1:  # the samples as they are
            samples = [bits.signed(sample_bits) for _ in range(block)]
        elif 8 <= kind <= 12:  # a fixed predictor of order 0 to 4
            order = kind - 8
            samples = [bits.signed(sample_bits) for _ in range(order)]
            samples += self._residual(bits, block, order)
            _restore_fixed(samples, order)
        elif kind >= 32:  # a linear predictor with stored coefficients
            order = kind - 31
            samples = [bits.signed(sample_bits) for _ in range(order)]
            precision = bits.read(4) + 1
            if precision == 16:
                raise FlacError("bad predictor precision")
            shift = bits.signed(5)
            coefficients = [bits.signed(precision) for _ in range(order)]
            samples += self._residual(bits, block, order)
            _restore_lpc(samples, coefficients, shift, order)
        else:
            raise FlacError("reserved subframe type")
        if wasted:
            samples = [s << wasted for s in samples]
        return samples

    def _residual(self, bits: Bits, block: int, order: int) -> list[int]:
        method = bits.read(2)
        if method > 1:
            raise FlacError("reserved residual coding")
        param_bits, escape = (4, 15) if method == 0 else (5, 31)
        partition_order = bits.read(4)
        partitions = 1 << partition_order
        out: list[int] = []
        for p in range(partitions):
            count = (block >> partition_order) - (order if p == 0 else 0)
            k = bits.read(param_bits)
            if k == escape:  # stored plainly, with a given number of bits
                raw = bits.read(5)
                out += [bits.signed(raw) for _ in range(count)]
                continue
            for _ in range(count):
                value = (bits.unary() << k) | bits.read(k)
                out.append((value >> 1) ^ -(value & 1))  # back from the zigzag that makes it positive
        return out

    def _pack(self, channels: list[list[int]], sample_bits: int) -> bytes:
        """Interleave the channels into little-endian samples of `self.width` bytes, as in a WAV."""
        shift = self.width * 8 - sample_bits  # 20-bit audio is stored in 3 bytes, aligned to the top
        if self.width == 1:  # 8-bit WAV is unsigned
            return bytes((s + 128) & 0xFF for frame in zip(*channels) for s in frame)
        fmt = {2: "<h", 4: "<i"}.get(self.width)
        if fmt:
            return b"".join(struct.pack(fmt, s << shift) for frame in zip(*channels) for s in frame)
        return b"".join(((s << shift) & 0xFFFFFF).to_bytes(3, "little") for frame in zip(*channels) for s in frame)


def _restore_fixed(samples: list[int], order: int) -> None:
    """Undo a fixed predictor: each sample is its prediction plus the stored difference."""
    for i in range(order, len(samples)):
        if order == 1:
            samples[i] += samples[i - 1]
        elif order == 2:
            samples[i] += 2 * samples[i - 1] - samples[i - 2]
        elif order == 3:
            samples[i] += 3 * samples[i - 1] - 3 * samples[i - 2] + samples[i - 3]
        elif order == 4:
            samples[i] += 4 * samples[i - 1] - 6 * samples[i - 2] + 4 * samples[i - 3] - samples[i - 4]


def _restore_lpc(samples: list[int], coefficients: list[int], shift: int, order: int) -> None:
    """Undo a linear predictor: prediction = sum(coefficient x earlier sample) >> shift."""
    for i in range(order, len(samples)):
        prediction = 0
        for j, c in enumerate(coefficients):
            prediction += c * samples[i - 1 - j]
        samples[i] += prediction >> shift


def open_flac(path) -> Flac:
    return Flac(Path(path))
