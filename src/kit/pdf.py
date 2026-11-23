"""A very small PDF writer: pages of text and lines, in Helvetica.

A PDF file is a list of numbered objects (the document, its pages, the fonts,
each page's drawing instructions), followed by a table of where each object
starts in the file (the "xref"), so a reader can jump straight to any of them.
Page contents are short drawing programs: "BT /F1 12 Tf 72 700 Td (Hello) Tj ET"
means begin text, use font F1 at 12 points, move to (72, 700), show "Hello".

Helvetica is one of the fonts every PDF reader has built in, so no font is
embedded. Its letters are measured with the widths below (from Adobe's font
metrics), so text can be fitted and aligned. Standard library only.
"""

from __future__ import annotations

WIDTH, HEIGHT = 612, 792  # US Letter, in points (1/72 inch)

# Helvetica's width of each character from space (32) to "~" (126), in 1/1000 of the font size.
_WIDTHS = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,  # space to /
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,  # 0 to ?
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,  # @ to O
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,  # P to _
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,  # ` to o
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,  # p to ~
]


def text_width(text: str, size: float, bold: bool = False) -> float:
    """How wide `text` is at `size` points. Bold is about 5% wider."""
    units = sum(_WIDTHS[ord(c) - 32] if 32 <= ord(c) <= 126 else 556 for c in text)
    return units * size / 1000 * (1.05 if bold else 1.0)


def fit(text: str, size: float, room: float, bold: bool = False) -> str:
    """`text`, shortened with "..." if it is wider than `room` points."""
    if text_width(text, size, bold) <= room:
        return text
    while text and text_width(text + "...", size, bold) > room:
        text = text[:-1]
    return text + "..."


def wrap(text: str, size: float, room: float, bold: bool = False) -> list[str]:
    """`text` broken into lines no wider than `room` points, at spaces."""
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}" if line else word
        if line and text_width(candidate, size, bold) > room:
            lines.append(line)
            line = word
        else:
            line = candidate
    return lines + [line] if line else lines


def _string(text: str) -> bytes:
    """A PDF string literal. Characters outside Windows-1252 become "?"."""
    data = text.encode("cp1252", errors="replace")
    return b"(" + data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


class Document:
    """Pages of text and lines. Coordinates are points from the bottom left of the page."""

    def __init__(self, title: str = "", date: str = ""):
        self.title = title
        self.date = date  # "D:20261123120000Z": kept fixed so the same input gives the same file
        self.pages: list[list[bytes]] = []

    def new_page(self) -> None:
        self.pages.append([])

    def text(self, x: float, y: float, text: str, size: float = 11, bold: bool = False, grey: bool = False) -> None:
        colour = b"0.4 g " if grey else b"0 g "
        font = b"/F2" if bold else b"/F1"
        self.pages[-1].append(colour + b"BT " + font + b" %g Tf %g %g Td " % (size, x, y) + _string(text) + b" Tj ET")

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float = 0.75) -> None:
        self.pages[-1].append(b"0 G %g w %g %g m %g %g l S" % (width, x1, y1, x2, y2))

    def bytes(self) -> bytes:
        objects: list[bytes] = []  # object n is objects[n - 1]

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)

        catalog = add(b"")  # filled in below, once the pages object exists
        pages = add(b"")
        regular = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        bold = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        kids = []
        for drawing in self.pages or [[]]:
            content = b"\n".join(drawing)
            stream = add(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
            kids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R "
                            b"/Resources << /Font << /F1 %d 0 R /F2 %d 0 R >> >> >>"
                            % (pages, WIDTH, HEIGHT, stream, regular, bold)))
        objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages
        objects[pages - 1] = (b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids)
                              + b"] /Count %d >>" % len(kids))
        info = add(b"<< /Title " + _string(self.title) + b" /Producer (Kit)"
                   + (b" /CreationDate " + _string(self.date) if self.date else b"") + b" >>")

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")  # the second line marks the file as binary
        offsets = []
        for number, body in enumerate(objects, 1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
        table = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
        out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
        out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
            len(objects) + 1, catalog, info, table)
        return bytes(out)
