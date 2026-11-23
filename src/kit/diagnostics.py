"""A small log of what went wrong, and a problem report built from it.

When Kit hits an error it did not expect (a bug, not "there is no folder
there"), the details go to kit.log in the app's settings folder, and the page
says something went wrong instead of failing silently. "Report a problem" on
the home screen shows a short report to copy into a message: the Kit
version, the system, and the end of that log. Nothing is sent anywhere; the
person reads the report and decides whether to send it.

The log is kept small: past MAX_BYTES, the older half is dropped.
"""

from __future__ import annotations

import platform
import sys
import time
from pathlib import Path

from . import __version__

MAX_BYTES = 200_000
LOG_NAME = "kit.log"


def record(folder: Path, text: str) -> None:
    """Add an entry to the log. Never raises: logging must not cause a second problem."""
    path = Path(folder) / LOG_NAME
    entry = f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} Kit {__version__}\n{text.rstrip()}\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_BYTES:
            kept = path.read_bytes()[-MAX_BYTES // 2:]
            kept = kept[kept.find(b"\n--- ") + 1:] if b"\n--- " in kept else b""  # start at a whole entry
            path.write_bytes(kept)
        with path.open("a", encoding="utf-8") as log:
            log.write(entry)
    except OSError:
        pass


def recent(folder: Path, entries: int = 10) -> list[str]:
    """The last few log entries, oldest first."""
    try:
        text = (Path(folder) / LOG_NAME).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    parts = [part for part in text.split("--- ") if part.strip()]
    return ["--- " + part.rstrip() for part in parts[-entries:]]


def _webview_version() -> str:
    try:
        import webview
    except ImportError:
        return "not installed"
    return getattr(webview, "__version__", "installed")


def report(folder: Path, window: str) -> str:
    """The text a person can copy into a message when something is wrong."""
    lines = [
        f"Kit {__version__}",
        f"System: {platform.platform()} ({platform.machine()})",
        f"Python {platform.python_version()}" + (" (built app)" if getattr(sys, "frozen", False) else ""),
        f"Window: {window}",
        f"pywebview: {_webview_version()}",
        "",
    ]
    errors = recent(folder)
    lines += ["Recent problems:", *errors] if errors else ["No problems recorded."]
    return "\n".join(lines)
