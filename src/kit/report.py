"""`kit show`: one version of a kit as a web page.

The page is a single self-contained HTML file: every sound gets a waveform
and, when it is small enough to embed, a player. It needs no server, so it
can be opened from disk or sent to someone as a preview of a pack.
"""

from __future__ import annotations

import base64
from html import escape

from .audio import describe, peaks
from .manifest import diff
from .repo import KitError, Repo

MAX_EMBED_FILE = 4 * 1024 * 1024  # larger sounds get a waveform but no player
MAX_EMBED_TOTAL = 60 * 1024 * 1024  # keeps the page itself a sensible size

MIME = {
    "wav": "audio/wav", "mp3": "audio/mpeg", "flac": "audio/flac",
    "ogg": "audio/ogg", "m4a": "audio/mp4", "aac": "audio/aac",
}

CSS = """
:root { --bg:#f4f5f7; --card:#fff; --ink:#16181d; --muted:#5d6672; --line:#d5dae1;
  --accent:#c2410c; --added:#15803d; --modified:#b45309; --renamed:#1d4ed8; --removed:#b91c1c; }
@media (prefers-color-scheme: dark) { :root { --bg:#101216; --card:#191c22; --ink:#e8eaee;
  --muted:#9aa3ae; --line:#323944; --accent:#fb923c; --added:#4ade80; --modified:#fbbf24;
  --renamed:#93c5fd; --removed:#f87171; color-scheme: dark; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
main { max-width:920px; margin:0 auto; padding:32px 20px 64px; display:grid; gap:28px; }
.eyebrow { margin:0; font:600 12px/1 ui-monospace,Menlo,Consolas,monospace; letter-spacing:.1em;
  text-transform:uppercase; color:var(--accent); }
h1 { margin:6px 0 4px; font-size:30px; line-height:1.15; }
h1 span { color:var(--muted); font-weight:500; }
h2 { margin:0 0 12px; font-size:13px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }
.meta { margin:0; color:var(--muted); }
ul, ol { list-style:none; margin:0; padding:0; display:grid; gap:10px; }
.sound { background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:12px 14px; display:grid; grid-template-columns:minmax(0,1fr) 250px; gap:6px 18px; align-items:center; }
.name { font-weight:600; overflow-wrap:anywhere; }
.info { color:var(--muted); font-size:13px; }
.wave { width:100%; height:44px; color:var(--accent); display:block; }
audio { width:100%; height:32px; grid-column:1 / -1; }
.badge { display:inline-block; margin-left:8px; padding:2px 7px; border-radius:999px; font-size:11px;
  font-weight:600; text-transform:uppercase; letter-spacing:.05em; border:1px solid currentColor; }
.added { color:var(--added); } .modified { color:var(--modified); }
.renamed { color:var(--renamed); } .removed { color:var(--removed); }
.gone li { color:var(--muted); text-decoration:line-through; }
.history li { display:grid; grid-template-columns:44px 84px minmax(0,1fr); gap:10px;
  padding:8px 0; border-top:1px solid var(--line); font-size:14px; }
.history .here { font-weight:700; }
.mono { font-family:ui-monospace,Menlo,Consolas,monospace; color:var(--muted); font-size:13px; }
@media (max-width:640px) { .sound { grid-template-columns:minmax(0,1fr); } }
"""


def build_report(repo: Repo, ref: str | None = None) -> str:
    """Return the HTML page for one version (the latest if `ref` is None)."""
    history = repo.log()
    if not history:
        raise KitError("no versions saved yet")
    commit = repo.load_commit(repo.resolve(ref) if ref else history[0].id)
    labels = {c.id: f"v{len(history) - i}" for i, c in enumerate(history)}
    label = labels.get(commit.id, commit.id[:8])

    manifest = repo.manifest_of(commit.id)
    changes = diff(repo.manifest_of(commit.parent), manifest)
    changed = {c.path: c for c in changes if c.kind != "removed"}
    removed = [c.path for c in changes if c.kind == "removed"]
    credits = repo.credits(commit.id)

    rows, embedded = [], 0
    for path, entry in sorted(manifest.items()):
        stored = repo.store.path_for(entry["hash"])
        change = changed.get(path)
        badge = f'<span class="badge {change.kind}">{change.kind}</span>' if change else ""
        details = [f"added by {escape(credits[path].author)}"]
        if change and change.kind == "renamed":
            details.append(f"was {escape(change.old_path)}")
        details.append(escape(describe(stored, path) or _size(entry["size"])))
        player = ""
        mime = MIME.get(path.rsplit(".", 1)[-1].lower())
        if mime and entry["size"] <= MAX_EMBED_FILE and embedded + entry["size"] <= MAX_EMBED_TOTAL:
            embedded += entry["size"]
            data = base64.b64encode(stored.read_bytes()).decode()
            player = f'<audio controls preload="none" src="data:{mime};base64,{data}"></audio>'
        rows.append(
            f'<li class="sound"><div><div class="name">{escape(path)}{badge}</div>'
            f'<div class="info">{" · ".join(details)}</div></div>'
            f"{_waveform(peaks(stored, path))}{player}</li>"
        )

    summary = ", ".join(
        f"{sum(c.kind == kind for c in changes)} {kind}"
        for kind in ("added", "modified", "renamed", "removed")
        if any(c.kind == kind for c in changes)
    ) or "no changes"
    gone = ""
    if removed:
        items = "".join(f"<li>{escape(path)}</li>" for path in removed)
        gone = f'<section><h2>Removed in {label}</h2><ul class="gone">{items}</ul></section>'
    versions = "".join(
        f'<li class="{"here" if c.id == commit.id else ""}"><span>{labels[c.id]}</span>'
        f'<span class="mono">{c.id[:8]}</span>'
        f"<span>{escape(c.author)}: {escape(c.message)}</span></li>"
        for c in history
    )
    title = f"{escape(repo.folder.name)} {label}"
    sounds = "".join(rows)
    when = commit.time[:16].replace("T", " ")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>{CSS}</style></head>
<body><main>
<header><p class="eyebrow">Kit</p>
<h1>{escape(repo.folder.name)} <span>{label}</span></h1>
<p class="meta">{escape(commit.message)}</p>
<p class="meta">{escape(commit.author)} · {when} UTC · {len(manifest)} sounds · {summary}</p>
</header>
<section><h2>Sounds in {label}</h2><ul>{sounds}</ul></section>
{gone}
<section><h2>History</h2><ol class="history">{versions}</ol></section>
</main></body></html>
"""


def _waveform(levels: list[float] | None) -> str:
    """An inline SVG of mirrored bars, or an empty placeholder."""
    if not levels:
        return '<div class="info">no waveform</div>'
    step = 240 / len(levels)
    bars = "".join(
        f'<rect x="{i * step:.2f}" y="{22 - max(level * 20, 0.6):.2f}" '
        f'width="{max(step - 0.8, 0.6):.2f}" height="{max(level * 40, 1.2):.2f}" rx="0.6"/>'
        for i, level in enumerate(levels)
    )
    return (f'<svg class="wave" viewBox="0 0 240 44" preserveAspectRatio="none" '
            f'fill="currentColor" role="img" aria-label="waveform">{bars}</svg>')


def _size(n: float) -> str:
    for unit in ("bytes", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"
