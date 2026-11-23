"""A split sheet for a version: who added which sounds, and a share for each person.

When a beat made with a kit is placed, the people who made the kit need a
record of who contributed what. Kit already knows: each sound is credited to
whoever first added its audio (see Repo.credits). This writes that down as a
one- or two-page PDF, with a line for each person to agree and sign.

The shares are by number of sounds, as a starting point. People often agree
on other splits; the sheet says so and leaves room to write the agreed share.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import pdf
from .export import label
from .repo import KitError, Repo

LEFT, RIGHT, TOP, BOTTOM = 60, pdf.WIDTH - 60, pdf.HEIGHT - 60, 60


def shares(counts: dict[str, int]) -> dict[str, int]:
    """Whole percentages that add up to exactly 100 (largest remainder first)."""
    total = sum(counts.values())
    if not total:
        return {}
    exact = {who: n * 100 / total for who, n in counts.items()}
    result = {who: int(value) for who, value in exact.items()}
    left = 100 - sum(result.values())
    for who in sorted(exact, key=lambda w: (-(exact[w] - result[w]), w))[:left]:
        result[who] += 1
    return result


def split_sheet(repo: Repo, ref: str) -> tuple[bytes, str]:
    """The PDF for version `ref`, and the name to save it under."""
    commit_id = repo.resolve(ref)
    commit = repo.load_commit(commit_id)
    credits = repo.credits(commit_id)
    if not credits:
        raise KitError("this version has no sounds to split")
    version = label(repo, commit_id)
    history = {c.id: f"v{len(repo.log()) - i}" for i, c in enumerate(repo.log())}

    by_person: dict[str, list[tuple[str, str]]] = {}
    for path, first in credits.items():
        by_person.setdefault(first.author, []).append((path, history.get(first.id, first.id[:8])))
    people = sorted(by_person, key=lambda who: (-len(by_person[who]), who.lower()))
    percent = shares({who: len(by_person[who]) for who in people})

    when = datetime.fromisoformat(commit.time.replace("Z", "+00:00"))
    doc = pdf.Document(title=f"{repo.folder.name} {version} split sheet", date=when.strftime("D:%Y%m%d%H%M%SZ"))
    page = _Page(doc)

    page.write("Split sheet", 22, bold=True, gap=30)
    page.write(f"{repo.folder.name}, version {version}", 13, bold=True, gap=18)
    page.write(f"Saved {when.strftime('%B %d, %Y')} by {commit.author}: {commit.message}", 10, grey=True, gap=14)
    page.write(f"Version ID {commit_id}", 8, grey=True, gap=26)
    for line in pdf.wrap("Each sound is credited to whoever first added its audio to the kit, even if it was renamed "
                         "later. The shares below are by number of sounds, as a starting point: agree the final split "
                         "together and write it in below.", 10, RIGHT - LEFT):
        page.write(line, 10, gap=14)
    page.space(12)

    # The table: person, sounds, share.
    page.need(20 + 18 * len(people))
    page.row(["Producer", "Sounds", "Share by sounds"], bold=True)
    page.rule()
    for who in people:
        page.row([who, str(len(by_person[who])), f"{percent[who]}%"])
    page.rule()
    page.row(["Total", str(len(credits)), "100%"], bold=True)
    page.space(24)

    # What each person added.
    for who in people:
        page.need(40)
        page.write(f"Added by {who}", 12, bold=True, gap=16)
        for path, since in sorted(by_person[who]):
            page.need(13)
            page.doc.text(LEFT + 12, page.y, pdf.fit(path, 9, RIGHT - LEFT - 80), 9)
            page.doc.text(RIGHT - pdf.text_width(f"since {since}", 9), page.y, f"since {since}", 9, grey=True)
            page.y -= 13
        page.space(10)

    # Signatures.
    page.need(40 + 60 * len(people))
    page.write("Agreed", 12, bold=True, gap=24)
    for who in people:
        page.need(60)
        page.doc.text(LEFT, page.y, who, 10, bold=True)
        page.y -= 30
        for x, width, caption in ((LEFT, 200, "Signature"), (LEFT + 220, 120, "Agreed share (%)"), (LEFT + 360, 132, "Date")):
            page.doc.line(x, page.y, x + width, page.y)
            page.doc.text(x, page.y - 11, caption, 8, grey=True)
        page.y -= 30
    page.footer()

    name = f"{repo.folder.name} {version} split sheet.pdf".replace("/", "-")
    return doc.bytes(), name


def write_split_sheet(repo: Repo, ref: str, folder: Path) -> Path:
    """Save the split sheet for `ref` into `folder`. Returns its path."""
    data, name = split_sheet(repo, ref)
    folder = Path(folder).expanduser().resolve()
    if folder == repo.folder or repo.folder in folder.parents:
        raise KitError("the split sheet cannot go inside the kit folder; choose a folder outside it")
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / name
    partial = out.with_name(out.name + ".part")
    partial.write_bytes(data)
    partial.replace(out)
    return out


class _Page:
    """Writes top to bottom, starting a new page when the current one is full."""

    def __init__(self, doc: pdf.Document):
        self.doc = doc
        self.number = 0
        self._start()

    def _start(self) -> None:
        self.doc.new_page()
        self.number += 1
        self.y = TOP

    def need(self, height: float) -> None:
        if self.y - height < BOTTOM + 20:
            self.footer()
            self._start()

    def footer(self) -> None:
        self.doc.text(LEFT, BOTTOM - 20, "Made with Kit (github.com/nojanpourjafar/kit-vcs)", 8, grey=True)
        page = f"Page {self.number}"
        self.doc.text(RIGHT - pdf.text_width(page, 8), BOTTOM - 20, page, 8, grey=True)

    def write(self, text: str, size: float, bold: bool = False, grey: bool = False, gap: float = 14) -> None:
        self.need(gap)
        self.doc.text(LEFT, self.y, pdf.fit(text, size, RIGHT - LEFT, bold), size, bold=bold, grey=grey)
        self.y -= gap

    def row(self, cells: list[str], bold: bool = False) -> None:
        self.doc.text(LEFT, self.y, pdf.fit(cells[0], 10, 260, bold), 10, bold=bold)
        for right_edge, cell in ((LEFT + 360, cells[1]), (RIGHT, cells[2])):  # numbers line up on the right
            self.doc.text(right_edge - pdf.text_width(cell, 10, bold), self.y, cell, 10, bold=bold)
        self.y -= 18

    def rule(self) -> None:
        self.doc.line(LEFT, self.y + 12, RIGHT, self.y + 12, 0.5)
        self.y -= 4

    def space(self, height: float) -> None:
        self.y -= height
