"""Encoding FLAC, in plain Python: the reverse of flac.py.

FLAC stores the exact samples of a WAV file in about half the space. For each
block of 4096 samples and each channel:

1. Predict every sample from the ones before it. A "fixed" predictor of
   order 1 guesses the previous sample, order 2 continues the previous slope,
   and so on up to order 4. Each order is just the difference of the order
   below, so all five are cheap to try; the one with the smallest leftovers
   wins.
2. Store only what the prediction got wrong (the residual). In real audio it
   is small, so it is packed with Rice codes: a number's high part in unary
   (that many 0s, then a 1) and its low k bits as they are. The best k is
   about log2 of the typical size, chosen separately for parts of the block.
3. For stereo, also try storing left minus right (the "side") with the left,
   the right, or their average: left and right are usually nearly the same, so
   the side is small.

This encoder leaves out FLAC's other predictor, LPC, which computes its own
coefficients for each block: it saves a few percent more, at much more work
in Python. Any FLAC decoder (ffmpeg, a DAW, flac.py) reads the result.

Speed: about a second for every few seconds of stereo audio, in Python.
"""

from __future__ import annotations

import hashlib
import struct

BLOCK = 4096


class FlacEncodeError(ValueError):
    """Audio this encoder does not take: float samples, or more than 24 bits."""


# ---- bits ---------------------------------------------------------------------

class BitWriter:
    """Collects bits, most significant first, into bytes."""

    def __init__(self):
        self.out = bytearray()
        self.acc = 0      # bits not yet turned into bytes
        self.count = 0    # how many

    def write(self, value: int, bits: int) -> None:
        self.acc = (self.acc << bits) | (value & ((1 << bits) - 1))
        self.count += bits
        if self.count >= 512:
            self._flush()

    def _flush(self) -> None:
        whole = self.count >> 3
        rest = self.count & 7
        self.out += (self.acc >> rest).to_bytes(whole, "big")
        self.acc &= (1 << rest) - 1
        self.count = rest

    def signed(self, value: int, bits: int) -> None:
        self.write(value & ((1 << bits) - 1), bits)

    def rice(self, values: list[int], k: int) -> None:
        """Rice-code zigzagged values: (v >> k) zeros, a one, then the low k bits."""
        mask = (1 << k) - 1
        acc, count = self.acc, self.count
        for u in values:
            q = u >> k
            acc = (acc << (q + 1 + k)) | (1 << k) | (u & mask)
            count += q + 1 + k
            if count >= 512:
                self.acc, self.count = acc, count
                self._flush()
                acc, count = self.acc, self.count
        self.acc, self.count = acc, count

    def align(self) -> None:
        if self.count & 7:
            self.write(0, 8 - (self.count & 7))

    def bytes(self) -> bytes:
        self.align()
        self._flush()
        return bytes(self.out)


def _crc8(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _crc16_table() -> list[int]:
    table = []
    for byte in range(256):
        crc = byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x8005) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        table.append(crc)
    return table


_CRC16 = _crc16_table()


def _crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC16[(crc >> 8) ^ byte]
    return crc


def _utf8_number(n: int) -> bytes:
    """A frame number, written the way UTF-8 writes a character code."""
    if n < 0x80:
        return bytes([n])
    for length, first in ((2, 0xC0), (3, 0xE0), (4, 0xF0), (5, 0xF8), (6, 0xFC), (7, 0xFE)):
        if n < 1 << (6 * (length - 1) + (7 - length)):
            break
    tail = [0x80 | ((n >> (6 * i)) & 0x3F) for i in range(length - 1)]
    return bytes([first | (n >> (6 * (length - 1)))] + tail[::-1])


# ---- choosing how to store a channel -------------------------------------------

def _residuals(samples: list[int]) -> list[list[int]]:
    """The leftovers of the fixed predictors of order 0 to 4. Order n starts at sample n."""
    orders = [samples]
    for _ in range(4):
        last = orders[-1]
        orders.append([b - a for a, b in zip(last, last[1:])])
    return orders


def _rice_plan(residual: list[int], block: int, order: int) -> tuple[int, list[tuple[int, list[int]]], int]:
    """(partition order, [(k, zigzagged values)], total bits) with the fewest bits found."""
    zig = [r << 1 if r >= 0 else ((-r) << 1) - 1 for r in residual]  # 0, -1, 1, -2, 2 ... -> 0, 1, 2, 3, 4 ...
    best = None
    for partition_order in range(0, 7):
        parts = 1 << partition_order
        if block % parts or (block >> partition_order) <= order:
            break
        size = block >> partition_order
        plan, total, start = [], 4, 0
        for p in range(parts):
            count = size - (order if p == 0 else 0)
            values = zig[start:start + count]
            start += count
            mean = sum(values) / max(1, count)
            guess = max(0, int(mean).bit_length() - 1)
            k, bits = min(((k, sum(v >> k for v in values) + count * (k + 1))
                           for k in range(max(0, guess - 1), min(30, guess + 2))), key=lambda kb: kb[1])
            plan.append((k, values))
            total += 5 + bits
        if best is None or total < best[2]:
            best = (partition_order, plan, total)
    return best


def _plan_subframe(samples: list[int], bits: int) -> tuple:
    """The cheapest way to store one channel's block: ("constant",), ("verbatim",) or ("fixed", order, plan)."""
    block = len(samples)
    if all(s == samples[0] for s in samples):
        return ("constant", samples[0]), bits
    verbatim = (("verbatim",), block * bits)
    best = verbatim
    orders = _residuals(samples)
    # Try the order whose leftovers are smallest, and its neighbours.
    sizes = [sum(map(abs, r[4 - n:])) if len(r) > 4 - n else 0 for n, r in enumerate(orders)]
    ranked = sorted(range(5), key=lambda n: sizes[n])[:2]
    for order in ranked:
        if order >= block:
            continue
        partition_order, plan, cost = _rice_plan(orders[order], block, order)
        cost += order * bits
        if cost < best[1]:
            best = (("fixed", order, partition_order, plan), cost)
    return best


def _write_subframe(out: BitWriter, kind: tuple, samples: list[int], bits: int) -> None:
    if kind[0] == "constant":
        out.write(0b00000000, 8)
        out.signed(kind[1], bits)
    elif kind[0] == "verbatim":
        out.write(0b00000010, 8)
        for s in samples:
            out.signed(s, bits)
    else:
        _, order, partition_order, plan = kind
        out.write((0b001000 | order) << 1, 8)
        for s in samples[:order]:
            out.signed(s, bits)
        wide = any(k > 14 for k, _ in plan)  # method 1 allows Rice parameters up to 30
        out.write(1 if wide else 0, 2)
        out.write(partition_order, 4)
        for k, values in plan:
            out.write(k, 5 if wide else 4)
            out.rice(values, k)


# ---- frames and the file --------------------------------------------------------

SIZE_CODES = {8: 1, 12: 2, 16: 4, 20: 5, 24: 6}


def _frame(number: int, channels: list[list[int]], bits: int) -> bytes:
    block = len(channels[0])
    assignment, coded = len(channels) - 1, channels
    plans = None
    if len(channels) == 2:
        left, right = channels
        side = [l - r for l, r in zip(left, right)]
        mid = [(l + r) >> 1 for l, r in zip(left, right)]
        options = {"l": left, "r": right, "s": side, "m": mid}
        cost = {}
        plans = {}
        for name, signal in options.items():
            plans[name] = _plan_subframe(signal, bits + (name == "s"))
            cost[name] = plans[name][1]
        layouts = {0b0001: ("l", "r"), 0b1000: ("l", "s"), 0b1001: ("s", "r"), 0b1010: ("m", "s")}
        assignment = min(layouts, key=lambda a: sum(cost[n] for n in layouts[a]))
        names = layouts[assignment]
        coded = [options[n] for n in names]
        plans = [plans[n] for n in names]

    header = bytearray(b"\xff\xf8")
    size_code = 0b1100 if block == BLOCK else 0b0111
    header.append(size_code << 4)  # sample rate: as in STREAMINFO
    header.append((assignment << 4) | (SIZE_CODES[bits] << 1))
    header += _utf8_number(number)
    if size_code == 0b0111:
        header += (block - 1).to_bytes(2, "big")
    header.append(_crc8(header))

    out = BitWriter()
    for i, signal in enumerate(coded):
        extra = 1 if (assignment == 0b1000 and i == 1) or (assignment == 0b1001 and i == 0) \
            or (assignment == 0b1010 and i == 1) else 0
        kind = plans[i][0] if plans else _plan_subframe(signal, bits)[0]
        _write_subframe(out, kind, signal, bits + extra)
    frame = bytes(header) + out.bytes()
    return frame + _crc16(frame).to_bytes(2, "big")


def encode(pcm: bytes, rate: int, channels: int, bits: int) -> bytes:
    """A FLAC file holding `pcm`: interleaved little-endian integer samples, as in a WAV file.

    8-bit samples are unsigned, as in WAV. `bits` is 8, 12, 16, 20 or 24;
    12- and 20-bit samples sit in the top bits of 2 or 3 bytes.
    """
    if bits not in SIZE_CODES or not 1 <= channels <= 8 or not 1 <= rate < 1 << 20:
        raise FlacEncodeError(f"FLAC here takes 8 to 24 bits and 1 to 8 channels, not {bits} bits, {channels}")
    width = (bits + 7) // 8
    shift = width * 8 - bits
    if width == 1:
        flat = [b - 128 for b in pcm]
    elif width == 2:
        flat = [s >> shift for s in struct.unpack(f"<{len(pcm) // 2}h", pcm[:len(pcm) // 2 * 2])]
    else:
        flat = [int.from_bytes(pcm[i:i + 3], "little", signed=True) >> shift for i in range(0, len(pcm) - 2, 3)]
    total = len(flat) // channels
    flat = flat[:total * channels]

    frames = []
    for number, start in enumerate(range(0, total, BLOCK)):
        block = flat[start * channels:min(total, start + BLOCK) * channels]
        frames.append(_frame(number, [block[c::channels] for c in range(channels)], bits))

    # The MD5 of the samples, as FLAC defines it: little-endian, signed, in whole bytes.
    md5 = hashlib.md5()
    md5.update(b"".join(s.to_bytes(width, "little", signed=True) for s in flat) if width != 2
               else struct.pack(f"<{len(flat)}h", *flat))
    sizes = [len(f) for f in frames] or [0]
    blocks = [min(BLOCK, total - start) for start in range(0, total, BLOCK)] or [BLOCK]
    info = (min(blocks[:-1] or blocks) if len(blocks) > 1 else blocks[0]).to_bytes(2, "big")
    info += max(blocks).to_bytes(2, "big")
    info += min(sizes).to_bytes(3, "big") + max(sizes).to_bytes(3, "big")
    info += ((rate << 44) | ((channels - 1) << 41) | ((bits - 1) << 36) | total).to_bytes(8, "big")
    info += md5.digest()
    return b"fLaC" + bytes([0x80, 0, 0, len(info)]) + info + b"".join(frames)
