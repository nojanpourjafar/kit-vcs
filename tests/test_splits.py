"""Split sheets: a PDF of who added which sounds, with a share for each person."""

import re
import shutil
import subprocess

import pytest

from kit import app as app_module
from kit.cli import main
from kit.pdf import Document, fit, text_width, wrap
from kit.repo import KitError, Repo
from kit.splits import shares, split_sheet, write_split_sheet

from test_app import app  # noqa: F401  (the running app, used as a fixture)


@pytest.fixture
def three_people(kit):
    """Nojan's three sounds, two from Lucas, one from Jaylen; then a version named 1.0."""
    repo = Repo.init(kit, author="Nojan")
    repo.commit("first")
    (kit / "Hats" / "hat_open.wav").write_bytes(b"OPEN" * 50)
    (kit / "Kicks" / "kick_03.wav").write_bytes(b"THUD" * 50)
    repo.commit("two more", author="Lucas")
    (kit / "Kicks" / "kick_01.wav").rename(kit / "Kicks" / "Punch (main).wav")   # renamed: still Nojan's
    (kit / "rim.wav").write_bytes(b"RIM" * 50)
    repo.commit("a rim", author="Jaylen")
    repo.name_version("1.0", "latest")
    return repo


def text_of(data: bytes) -> str:
    """The text shown in our (uncompressed) PDF, string by string."""
    strings = re.findall(rb"\((.*?)(?<!\\)\) Tj", data)
    return "\n".join(s.replace(b"\\(", b"(").replace(b"\\)", b")").decode("cp1252") for s in strings)


@pytest.mark.parametrize("counts, expected", [
    ({"a": 1, "b": 1, "c": 1}, {"a": 34, "b": 33, "c": 33}),     # 33.3 each: someone gets the extra 1
    ({"a": 5, "b": 3, "c": 1}, {"a": 56, "b": 33, "c": 11}),
    ({"a": 7}, {"a": 100}),
    ({}, {}),
])
def test_shares_always_add_up_to_100(counts, expected):
    assert shares(counts) == expected


def test_the_sheet_says_who_added_what(three_people):
    data, name = split_sheet(three_people, "1.0")
    assert name == "kit 1.0 split sheet.pdf"
    text = text_of(data)
    assert "Split sheet" in text and "kit, version 1.0" in text
    assert re.search(r"Nojan\n3\n50%", text) and re.search(r"Lucas\n2\n33%", text) and re.search(r"Jaylen\n1\n17%", text)
    assert "Kicks/Punch (main).wav" in text                       # renamed, still credited to Nojan
    assert text.count("Signature") == 3


def test_the_pdf_is_well_formed_and_the_same_every_time(three_people):
    data, _ = split_sheet(three_people, "1.0")
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    table = int(data.rsplit(b"startxref\n", 1)[1].split()[0])
    offsets = [int(line[:10]) for line in data[table:].split(b"\n")[3:] if line.endswith(b" n ")]
    for number, offset in enumerate(offsets, 1):                  # every entry points at its object
        assert data[offset:].startswith(b"%d 0 obj" % number)
    assert split_sheet(three_people, "1.0")[0] == data              # dated by the version, not by today


@pytest.mark.skipif(not shutil.which("qpdf"), reason="qpdf is not installed")
def test_qpdf_finds_nothing_wrong(three_people, tmp_path):
    out = write_split_sheet(three_people, "1.0", tmp_path)
    result = subprocess.run(["qpdf", "--check", str(out)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_long_kits_go_onto_more_pages(kit):
    for i in range(80):
        (kit / f"loop {i:02d}.wav").write_bytes(b"%d" % i)
    repo = Repo.init(kit, author="Nojan")
    repo.commit("lots")
    data, _ = split_sheet(repo, "latest")
    assert b"/Count 3" in data and "Page 3" in text_of(data)


def test_it_goes_next_to_the_kit_never_inside_it(three_people, tmp_path):
    with pytest.raises(KitError, match="inside the kit"):
        write_split_sheet(three_people, "1.0", three_people.folder / "Kicks")
    with pytest.raises(KitError):
        split_sheet(Repo.init(tmp_path / "empty", author="x"), "latest")   # nothing saved yet


def test_text_is_measured_and_fitted():
    assert text_width("i", 10) < text_width("W", 10) and text_width("ab", 10, bold=True) > text_width("ab", 10)
    assert fit("short", 10, 500) == "short"
    shortened = fit("a very long sound name that will not fit.wav", 10, 80)
    assert shortened.endswith("...") and text_width(shortened, 10) <= 80
    assert all(text_width(line, 10) <= 100 for line in wrap("one two three four five six seven eight", 10, 100))
    doc = Document()
    doc.new_page()
    doc.text(10, 10, "brackets ( ) and \\ and émoji 🎹")
    assert b"(brackets \\( \\) and \\\\ and \xe9moji ?) Tj" in doc.bytes()


def test_the_command_and_the_app(three_people, tmp_path, monkeypatch, capsys, app):
    monkeypatch.chdir(three_people.folder)
    main(["splits", "1.0", "-o", str(tmp_path / "out")])
    assert (tmp_path / "out" / "kit 1.0 split sheet.pdf").exists() and "Wrote" in capsys.readouterr().out

    monkeypatch.setattr(app_module, "downloads_folder", lambda: tmp_path / "Downloads")
    monkeypatch.setattr(app_module, "reveal", lambda path: None)
    message = app.json("/api/split-sheet", {"version": "1.0"})["message"]
    assert message == "Saved kit 1.0 split sheet.pdf in Downloads."
    assert (tmp_path / "Downloads" / "kit 1.0 split sheet.pdf").read_bytes().startswith(b"%PDF")
