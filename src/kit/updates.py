"""Is there a newer Kit? Asked of GitHub's releases, at most once a day.

The app asks GitHub for the tag of the latest release ("v0.14.0") and says so
when it is newer than the running version. Nothing about the user or their
kits is sent: it is the same request as opening the releases page. Any
failure (offline, GitHub down, its hourly limit reached) is quiet, and the
next launch tries again.

Setting KIT_NO_UPDATE_CHECK=1 turns the check off.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request

from . import __version__

REPO = "nojanpourjafar/kit-vcs"
DOWNLOAD_PAGE = f"https://github.com/{REPO}/releases/latest"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
DAY = 24 * 60 * 60


def parse(text: str) -> tuple[int, int, int] | None:
    """(0, 14, 0) for "v0.14.0" or "0.14.0"; None for anything else, like "v1.0-beta"."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", text.strip()) if isinstance(text, str) else None
    return tuple(int(part) for part in match.groups()) if match else None


def fetch_latest_tag() -> str:
    """The tag of the newest release on GitHub. Raises OSError or ValueError when that fails."""
    request = urllib.request.Request(LATEST_API, headers={
        "Accept": "application/vnd.github+json", "User-Agent": f"kit/{__version__}"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())["tag_name"]


def latest_version(cache_file, now: float | None = None) -> str | None:
    """The newest released version ("0.14.0"), or None if it cannot be found out.

    The answer is kept in `cache_file` for a day, so opening Kit ten times
    asks GitHub once. GitHub allows 60 of these requests an hour per network.
    """
    now = time.time() if now is None else now
    try:
        saved = json.loads(cache_file.read_text())
        if 0 <= now - saved["checked"] < DAY:
            return saved["latest"]
    except (OSError, ValueError, KeyError, TypeError):
        pass  # no answer saved yet, or a damaged one: ask
    try:
        tag = fetch_latest_tag()
    except (OSError, ValueError, KeyError, TypeError):
        return None  # offline or refused; nothing is saved, so the next launch asks again
    latest = ".".join(map(str, parse(tag))) if parse(tag) else None
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"checked": now, "latest": latest}))
    except OSError:
        pass
    return latest


def newer_version(cache_file, running: str | None = None) -> str | None:
    """The latest version if it is newer than `running` (this Kit's version), else None. Never raises."""
    if os.environ.get("KIT_NO_UPDATE_CHECK"):
        return None
    running = running or __version__
    latest = latest_version(cache_file)
    if latest and parse(running) and parse(latest) > parse(running):
        return latest
    return None
