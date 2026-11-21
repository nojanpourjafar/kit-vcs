"""File names that work on every system.

A Mac or Linux computer accepts almost any file name, but Windows refuses some
characters and a few special names. A kit saved on a Mac with a sound called
"What?.wav" would then fail to download on a Windows computer. Kit warns when
such a name is about to be shared, and refuses to write one on Windows.

Mac and Windows also ignore capital letters in names, so "Kick.wav" and
"kick.wav" in the same folder would be the same file there.
"""

from __future__ import annotations

from collections.abc import Iterable

FORBIDDEN = set('<>:"|?*\\')  # \\ is a folder separator on Windows
# Names Windows keeps for devices, with or without an extension ("CON.wav" is refused too).
RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"{kind}{n}" for kind in ("COM", "LPT") for n in range(1, 10)}


def problem(path: str) -> str | None:
    """Why Windows cannot hold this kit path ("Drums/What?.wav"), or None if it can."""
    for part in path.split("/"):
        bad = sorted({ch for ch in part if ch in FORBIDDEN or ord(ch) < 32})
        if bad:
            shown = " ".join(ch if ord(ch) >= 32 else f"\\x{ord(ch):02x}" for ch in bad)
            return f"Windows does not allow {shown} in names"
        if part.split(".")[0].upper() in RESERVED:
            return f"Windows keeps the name {part.split('.')[0]} for itself"
        if part != part.rstrip(" ."):
            return "Windows does not allow names ending in a space or a dot"
    return None


def warnings(paths: Iterable[str]) -> list[str]:
    """One line for every path that would not work on every system."""
    paths = sorted(paths)
    lines = [f"{path}: {reason}" for path in paths if (reason := problem(path))]
    seen: dict[str, str] = {}
    for path in paths:
        other = seen.setdefault(path.lower(), path)
        if other != path:
            lines.append(f"{other} and {path} differ only in capital letters; "
                         "Mac and Windows treat them as one file")
    return lines
