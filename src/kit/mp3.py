"""The length and format of an MP3 file, read from its frame headers.

An MP3 file is a run of frames. Each starts with a 4-byte header: 11 bits set
to 1 (the "sync"), then the MPEG version, the layer, the bitrate, the sample
rate, and whether the sound is mono. A frame always holds the same number of
samples (1152 for MPEG-1, 576 for MPEG-2 and 2.5), so the length of the sound
is the number of frames times that, divided by the sample rate.

Counting frames means walking the file header by header: each header gives
its frame's size, which leads to the next header. Most encoders also write an
"Xing" (or "Info") frame first that already holds the count, and LAME and
FFmpeg add how many silent samples the encoder put at the start and end. With
those, the length is exact to the sample.

This reads headers only. Decoding the sound itself (for a waveform) is left
to the page: every web view decodes MP3. Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .flac import id3_length

# Bitrates in kbps for layer III, by bitrate index (0 means "free", 15 is invalid).
BITRATES = {
    1: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],  # MPEG-1
    2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],      # MPEG-2 and 2.5
}
SAMPLE_RATES = {1: [44100, 48000, 32000], 2: [22050, 24000, 16000], 25: [11025, 12000, 8000]}
VERSIONS = {0b11: 1, 0b10: 2, 0b00: 25}  # the 2 header bits; 0b01 is reserved


@dataclass
class Header:
    version: int        # 1, 2, or 25 for MPEG-2.5
    rate: int
    channels: int
    kbps: int
    size: int           # bytes in this frame, header included

    @property
    def samples(self) -> int:
        return 1152 if self.version == 1 else 576

    @property
    def side_info(self) -> int:
        """Bytes between the header and the frame's audio data (where an Xing tag sits)."""
        if self.version == 1:
            return 17 if self.channels == 1 else 32
        return 9 if self.channels == 1 else 17


@dataclass
class Mp3Info:
    rate: int
    channels: int
    seconds: float
    kbps: int           # the average bitrate
    vbr: bool


def header(data: bytes, at: int) -> Header | None:
    """The layer III frame header at `at`, or None if there is not a valid one there."""
    if at + 4 > len(data):
        return None
    b1, b2, b3 = data[at + 1], data[at + 2], data[at + 3]
    if data[at] != 0xFF or b1 & 0xE0 != 0xE0:
        return None  # no sync
    version = VERSIONS.get((b1 >> 3) & 0b11)
    layer = (b1 >> 1) & 0b11
    bitrate_index, rate_index = b2 >> 4, (b2 >> 2) & 0b11
    if version is None or layer != 0b01 or bitrate_index in (0, 15) or rate_index == 3:
        return None  # reserved values, another layer, or a "free" bitrate we cannot size
    kbps = BITRATES[1 if version == 1 else 2][bitrate_index]
    rate = SAMPLE_RATES[version][rate_index]
    padding = (b2 >> 1) & 1
    size = (144 if version == 1 else 72) * kbps * 1000 // rate + padding
    channels = 1 if b3 >> 6 == 0b11 else 2
    return Header(version, rate, channels, kbps, size)


def _first_frame(data: bytes, start: int) -> int | None:
    """Where the first real frame begins: a header followed by another header.

    Requiring two in a row avoids being fooled by bytes that happen to look
    like a header, in junk before the audio or inside cover art.
    """
    at = start
    limit = min(len(data) - 4, start + 64 * 1024)
    while at < limit:
        at = data.find(b"\xff", at, limit)
        if at < 0:
            return None
        first = header(data, at)
        if first is not None:
            second = header(data, at + first.size)
            if second is not None and (second.version, second.rate) == (first.version, first.rate):
                return at
        at += 1
    return None


def _xing(data: bytes, at: int, first: Header) -> tuple[int, int, int] | None:
    """(frames, encoder delay, end padding) from an Xing/Info tag in the first frame, or None.

    None too when the tag cannot be trusted: a file cut short (a download that
    stopped) still carries the full count, so the tag's byte count is checked
    against what is really there.
    """
    tag = at + 4 + first.side_info
    if data[tag:tag + 4] not in (b"Xing", b"Info"):
        return None
    flags = int.from_bytes(data[tag + 4:tag + 8], "big")
    if not flags & 1:
        return None  # the tag does not hold the frame count
    frames = int.from_bytes(data[tag + 8:tag + 12], "big")
    if flags & 2 and len(data) - at < 0.98 * int.from_bytes(data[tag + 12:tag + 16], "big"):
        return None  # cut short: count the frames that are there instead
    delay = padding = 0
    # LAME's extension, also written by FFmpeg, follows a full Xing tag
    # (flags, frames, bytes, a 100-byte table, quality: 120 bytes in all).
    lame = tag + 120
    if flags & 0b1111 == 0b1111 and data[lame:lame + 4] in (b"LAME", b"Lavc", b"Lavf", b"L3.9"):
        packed = int.from_bytes(data[lame + 21:lame + 24], "big")
        delay, padding = packed >> 12, packed & 0xFFF
    return frames, delay, padding


def read(path: Path) -> Mp3Info | None:
    """The format and length of an MP3 file, or None if it is not one."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    start = _first_frame(data, id3_length(data[:10]))
    if start is None:
        return None
    first = header(data, start)
    xing = _xing(data, start, first)
    if xing is not None:
        frames, delay, padding = xing
        seconds = max(0, frames * first.samples - delay - padding) / first.rate
        vbr = data[start + 4 + first.side_info] == ord("X")  # LAME and FFmpeg write "Info" for constant bitrates
        if vbr and frames:
            end = len(data) - (128 if data[-128:-125] == b"TAG" else 0)  # an ID3v1 tag is not audio
            kbps = round((end - start - first.size) * 8 * first.rate / (frames * first.samples) / 1000)
        else:
            kbps = first.kbps
        return Mp3Info(first.rate, first.channels, seconds, kbps, vbr)

    # No tag: walk the frames and count them. Stops at the first thing that is
    # not a frame, such as an ID3v1 tag at the end.
    frames, total_kbps, rates, at = 0, 0, set(), start
    while (frame := header(data, at)) is not None and at + frame.size <= len(data):
        frames += 1
        total_kbps += frame.kbps
        rates.add(frame.kbps)
        at += frame.size
    if not frames:
        return None
    return Mp3Info(first.rate, first.channels, frames * first.samples / first.rate,
                   round(total_kbps / frames), vbr=len(rates) > 1)


def describe(path: Path) -> str | None:
    """'3.20 s, 44.1 kHz, stereo, MP3 320 kbps', like audio.describe for WAV files."""
    info = read(path)
    if info is None:
        return None
    layout = "mono" if info.channels == 1 else "stereo"
    return f"{info.seconds:.2f} s, {info.rate / 1000:g} kHz, {layout}, MP3 {'~' if info.vbr else ''}{info.kbps} kbps"
