"""Tempo and key from a sound's file name.

Producers usually write them into names: "Dark Loop 140bpm F#m.wav",
"melody_92_BPM_Cmaj.wav", "Pads/A minor - 90 bpm.wav". Kit reads them so loops
can be shown and searched by tempo and key.

Nothing is guessed. A bare "140" might be a take number and a lone "A" might be
a word, so a tempo needs "bpm" next to it and a key needs a mode (m, min,
minor, maj, major) after the note.
"""

from __future__ import annotations

import re

# "140bpm", "140 BPM", "140_bpm", "bpm140", "BPM-92". The look-arounds stop
# "1400bpm" or "xbpm" from matching, and \d{2,3} keeps it to sensible tempos.
BPM = re.compile(r"(?<![0-9a-z])(\d{2,3})[\s_-]?bpm(?![a-z])|(?<![a-z])bpm[\s_-]?(\d{2,3})(?![0-9])", re.I)

# A note (C, F#, Bb, Eb...), then a mode: "F#m", "C#min", "Bb minor", "Amaj", "D major".
# The note letter must not be glued to other letters, so "Drum" or "Bass" never match.
KEY = re.compile(
    r"(?<![A-Za-z0-9#])([A-Ga-g])(#|♯|b|♭)?[\s_-]?(minor|min|major|maj|m)(?![A-Za-z])"
)


def from_name(path: str) -> dict:
    """{"bpm": 140 or None, "key": "F# min" or None} for a kit path. The file name wins over folder names."""
    found = {"bpm": None, "key": None}
    for part in reversed(path.split("/")):  # file name first, then its folders
        stem = part.rsplit(".", 1)[0] if "." in part else part
        if found["bpm"] is None and (match := BPM.search(stem)):
            bpm = int(match.group(1) or match.group(2))
            if 40 <= bpm <= 300:
                found["bpm"] = bpm
        if found["key"] is None:
            found["key"] = _key(stem)
    return found


def _key(text: str) -> str | None:
    for match in KEY.finditer(text):
        letter, accidental, mode = match.groups()
        after = text[match.end():match.end() + 2]
        if mode == "m" and not accidental and after[:1] == " " and after[1:].isalpha():
            continue  # "Am I Dreaming": a short "m" followed by a word is probably English
        sign = {"♯": "#", "♭": "b"}.get(accidental, accidental or "")
        return f"{letter.upper()}{sign} {'maj' if mode.lower().startswith('maj') else 'min'}"
    return None
