"""Storing WAV files as FLAC inside .kit, and getting the exact same file back.

Every stored file is named by the SHA-256 of its bytes, and that hash is what
versions, credits and sync all rely on. So a compressed copy is only useful if
it turns back into the original file byte for byte, header included.

A WAV file is a header (format, maybe a BPM or loop chunk from the DAW), the
samples, and sometimes more chunks after them. Only the samples compress, so
a packed file keeps the bytes before and after them as they are, and the
samples as FLAC:

    "KITZ" 1 | length, the bytes before the samples | length, the bytes after | FLAC

Unpacking puts the three back together. A file is only ever stored packed
after unpacking it gave back the original hash (see ObjectStore.compact).
"""

from __future__ import annotations

import struct

from .flac import Flac
from .flacenc import encode

MAGIC = b"KITZ\x01"


def wav_layout(data: bytes) -> tuple[int, int, int, int, int] | None:
    """(where the samples start, their length, rate, channels, bytes per sample)
    for a WAV file of whole integer samples of 1 to 3 bytes, or None for anything else."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    pos, fmt = 12, None
    while pos + 8 <= len(data):
        name, size = data[pos:pos + 4], int.from_bytes(data[pos + 4:pos + 8], "little")
        body = pos + 8
        if name == b"fmt " and size >= 16 and body + 16 <= len(data):
            tag, channels, rate = struct.unpack_from("<HHI", data, body)
            align = struct.unpack_from("<H", data, body + 12)[0]
            if tag == 0xFFFE and size >= 40 and body + 26 <= len(data):  # "extensible": the real tag is inside
                tag = struct.unpack_from("<H", data, body + 24)[0]
            fmt = (tag, channels, rate, align)
        elif name == b"data":
            if fmt is None:
                return None
            tag, channels, rate, align = fmt
            if tag != 1 or not channels or align % channels or align // channels not in (1, 2, 3):
                return None  # float samples, or 32-bit: FLAC cannot hold them exactly here
            length = min(size, len(data) - body) // align * align  # whole frames only; the rest stays as bytes
            return body, length, rate, channels, align // channels
        pos = body + size + (size & 1)  # chunks are padded to an even length
    return None


def pack(data: bytes) -> bytes | None:
    """The packed form of a WAV file, or None if it is not one that can be packed."""
    layout = wav_layout(data)
    if layout is None:
        return None
    start, length, rate, channels, width = layout
    if length == 0 or rate >= 1 << 20 or channels > 8:
        return None
    before, after = data[:start], data[start + length:]
    flac = encode(data[start:start + length], rate, channels, width * 8)
    return MAGIC + len(before).to_bytes(4, "big") + before + len(after).to_bytes(4, "big") + after + flac


def unpack(blob: bytes) -> bytes:
    """The original file from its packed form. Raises ValueError if it is not one."""
    if not blob.startswith(MAGIC):
        raise ValueError("not a packed file")
    pos = len(MAGIC)
    size = int.from_bytes(blob[pos:pos + 4], "big")
    before = blob[pos + 4:pos + 4 + size]
    pos += 4 + size
    size = int.from_bytes(blob[pos:pos + 4], "big")
    after = blob[pos + 4:pos + 4 + size]
    flac = Flac(data=blob[pos + 4 + size:])
    return before + flac.readframes(flac.frames) + after
