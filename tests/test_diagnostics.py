"""The problem log and the report built from it."""

import sys
import types

import pytest

import kit as kit_package
from kit import __version__, diagnostics
from kit.app import App, config_dir
from kit.desktop import Bridge
from kit.repo import KitError, Repo

from test_app import app  # noqa: F401  (the running app, used as a fixture)


def test_entries_are_kept_and_shown_newest_last(tmp_path):
    diagnostics.record(tmp_path, "first problem")
    diagnostics.record(tmp_path, "second problem\nwith a second line")
    entries = diagnostics.recent(tmp_path)
    assert len(entries) == 2 and "first problem" in entries[0] and "with a second line" in entries[1]
    assert f"Kit {__version__}" in entries[0]


def test_the_log_stays_small(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "MAX_BYTES", 2000)
    for i in range(200):
        diagnostics.record(tmp_path, f"problem {i} " + "x" * 50)
    assert (tmp_path / "kit.log").stat().st_size < 2200
    entries = diagnostics.recent(tmp_path, entries=1000)
    assert "problem 199" in entries[-1] and all(entry.startswith("--- ") for entry in entries)  # whole entries only


def test_logging_never_fails(tmp_path):
    (tmp_path / "file").write_text("a file where the folder should be")
    diagnostics.record(tmp_path / "file", "nowhere to write this")   # no error
    assert diagnostics.recent(tmp_path / "file") == []


def test_the_report_says_what_and_where(tmp_path):
    text = diagnostics.report(tmp_path, "own window")
    assert text.startswith(f"Kit {__version__}") and "System: " in text and "Window: own window" in text
    assert "No problems recorded." in text
    diagnostics.record(tmp_path, "Traceback: something broke")
    assert "something broke" in diagnostics.report(tmp_path, "browser")


def test_a_bug_is_logged_and_the_page_is_told(app, kit, monkeypatch):
    Repo.init(kit, author="Nojan")
    monkeypatch.setattr(App, "state", lambda self: 1 / 0)                # a bug, not a KitError
    status, body, _ = app("/api/state")
    assert status == 500 and b"Report a problem" in body
    log = diagnostics.recent(config_dir())[-1]
    assert "GET /api/state" in log and "ZeroDivisionError" in log


def test_ordinary_problems_are_not_logged(app):
    assert app("/api/open", {"folder": "/no/such/folder"})[0] == 400
    assert diagnostics.recent(config_dir()) == []


def test_the_page_can_log_and_ask_for_the_report(app):
    app.json("/api/log", {"message": "TypeError: x is undefined (ui.html:120)"})
    text = app.json("/api/report", {})["text"]
    assert "In the page: TypeError: x is undefined" in text and "Window: browser" in text


def test_a_failed_drag_is_logged(kit, monkeypatch):
    Repo.init(kit, author="Nojan")
    broken = types.ModuleType("broken")
    broken.start = lambda window, file: (_ for _ in ()).throw(RuntimeError("no drag here"))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "kit.dragout_windows", broken)
    monkeypatch.setattr(kit_package, "dragout_windows", broken, raising=False)  # if a test imported the real one
    bridge = Bridge(App(kit))
    bridge._window = types.SimpleNamespace(native=None)
    with pytest.raises(RuntimeError):
        bridge.drag_sound("Kicks/kick_01.wav")
    assert "Dragging Kicks/kick_01.wav" in diagnostics.recent(config_dir())[-1]
    with pytest.raises(KitError):
        bridge.drag_sound(".kit/HEAD")                                    # refused, not a bug: not logged
    assert len(diagnostics.recent(config_dir())) == 1
