"""The desktop window. A real window cannot open in a test, so pywebview is replaced by a stand-in."""

import json
import os
import re
import subprocess
import sys
import types
import urllib.request
from pathlib import Path

import pytest

from kit import cli, desktop
from kit.app import App
from kit.repo import KitError, Repo


class FakeWindow:
    def __init__(self, answer):
        self.answer, self.asked = answer, None

    def create_file_dialog(self, kind):
        self.asked = kind
        return self.answer


@pytest.mark.parametrize("module, expected", [
    (types.SimpleNamespace(FileDialog=types.SimpleNamespace(FOLDER="new-style")), "new-style"),
    (types.SimpleNamespace(FOLDER_DIALOG="old-style"), "old-style"),
])
def test_the_folder_chooser_works_with_old_and_new_pywebview(monkeypatch, module, expected):
    monkeypatch.setitem(sys.modules, "webview", module)
    bridge = desktop.Bridge()
    bridge._window = FakeWindow(("/Users/nojan/Music/MyKit",))

    assert bridge.pick_folder() == "/Users/nojan/Music/MyKit"
    assert bridge._window.asked == expected

    bridge._window = FakeWindow(None)                      # the user pressed Cancel
    assert bridge.pick_folder() is None


def test_a_window_is_opened_on_the_running_server(monkeypatch):
    seen = {}

    def create_window(title, url, js_api, **options):
        with urllib.request.urlopen(url) as response:      # the server is already up
            seen.update(title=title, page=response.read(), api=js_api)
        return "the window"

    fake = types.SimpleNamespace(create_window=create_window, start=lambda debug=False: seen.update(started=True))
    monkeypatch.setattr(desktop, "_load_webview", lambda: fake)

    desktop.run()

    assert seen["title"] == "Kit" and seen["started"] and b"<title>Kit</title>" in seen["page"]
    assert seen["api"]._window == "the window"


def test_without_pywebview_it_falls_back_to_the_browser(tmp_path):
    # Start from the real environment: on Windows, Python cannot start without
    # system variables such as SYSTEMROOT.
    src = Path(__file__).resolve().parent.parent / "src"
    env = {**os.environ, "KIT_CONFIG_DIR": str(tmp_path), "PYTHONPATH": str(src), "PYTHONUNBUFFERED": "1"}
    process = subprocess.Popen([sys.executable, "-m", "kit.desktop", "--no-open"],
                               stdout=subprocess.PIPE, text=True, env=env)
    try:
        url = re.search(r"http://127\.0\.0\.1:\d+/", process.stdout.readline()).group()
        page = urllib.request.urlopen(url).read().decode()
        token = re.search(r'const TOKEN = "([^"]+)"', page).group(1)
        request = urllib.request.Request(url + "api/state", headers={"X-Kit-Token": token})
        state = json.loads(urllib.request.urlopen(request).read())
        assert state["home"] is True and state["recent"] == []
    finally:
        process.terminate()
        process.wait()


def test_kit_app_is_a_command(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(desktop, "run", lambda folder, window=True, **more: calls.append((folder, window)))
    assert cli.main(["app"]) == 0 and cli.main(["app", "MyKit", "--browser"]) == 0
    assert calls == [(None, True), ("MyKit", False)]


def test_a_folder_that_is_not_there_gets_a_plain_message(tmp_path, capsys):
    assert desktop.main([str(tmp_path / "missing"), "--no-open"]) == 1
    assert "no folder" in capsys.readouterr().out


@pytest.fixture
def bridge(kit, monkeypatch):
    """A Bridge for an open kit, with a stand-in for the macOS drag code."""
    Repo.init(kit, author="Nojan")
    started = []
    fake = types.SimpleNamespace(start=lambda window, file: started.append((window, file)))
    monkeypatch.setitem(sys.modules, "kit.dragout_mac", fake)
    monkeypatch.delattr("kit.dragout_mac", raising=False)   # make `from . import` find the stand-in
    bridge = desktop.Bridge(App(kit))
    bridge._window = types.SimpleNamespace(native="the NSWindow")
    bridge.started = started
    return bridge


def test_a_sound_can_be_dragged_out_on_a_mac(bridge, monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    assert bridge.drag_sound("Kicks/kick_01.wav") is True
    [(window, file)] = bridge.started
    assert window == "the NSWindow" and file.name == "kick_01.wav" and file.is_file()


def test_dragging_is_refused_outside_the_kit(bridge, monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    for bad in ["../outside.wav", ".kit/HEAD", "Kicks/missing.wav"]:
        with pytest.raises(KitError):
            bridge.drag_sound(bad)
    assert bridge.started == []


def test_a_sound_can_be_dragged_out_on_windows(bridge, monkeypatch):
    started = []
    fake = types.SimpleNamespace(start=lambda form, file: started.append((form, file)))
    monkeypatch.setitem(sys.modules, "kit.dragout_windows", fake)
    monkeypatch.delattr("kit.dragout_windows", raising=False)
    monkeypatch.setattr(desktop.sys, "platform", "win32")
    assert bridge.drag_sound("Kicks/kick_01.wav") is True
    [(form, file)] = started
    assert form == "the NSWindow" and file.name == "kick_01.wav"   # the stand-in window from the fixture


def test_linux_says_dragging_is_not_supported(bridge, monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    assert bridge.drag_sound("Kicks/kick_01.wav") is False and bridge.started == []


def test_the_mac_drag_code_loads():
    pytest.importorskip("AppKit")          # only where PyObjC is installed: macOS with pywebview
    import importlib

    module = importlib.import_module("kit.dragout_mac")
    source = module.KitDragSource.alloc().init()
    assert source.draggingSession_sourceOperationMaskForDraggingContext_(None, 0) == module.AppKit.NSDragOperationCopy


def test_dev_mode_turns_on_the_developer_tools(monkeypatch):
    started = {}
    fake = types.SimpleNamespace(create_window=lambda *args, **options: "the window",
                                 start=lambda debug=False: started.update(debug=debug))
    monkeypatch.setattr(desktop, "_load_webview", lambda: fake)
    desktop.run(dev=True)
    assert started == {"debug": True}


def test_kit_app_and_kit_ui_pass_dev_on(monkeypatch):
    import kit.app

    calls = []
    monkeypatch.setattr(desktop, "run", lambda folder, window=True, dev=False, **more: calls.append(("app", dev)))
    monkeypatch.setattr(kit.app, "serve", lambda folder, port=0, open_browser=True, dev=False: calls.append(("ui", dev)))
    cli.main(["app", "--dev"])
    cli.main(["ui", "--dev", "--no-open"])
    cli.main(["app"])
    assert calls == [("app", True), ("ui", True), ("app", False)]


def test_the_windows_drag_code_builds_what_explorer_would(tmp_path):
    # Runs on Windows with pywebview installed (GitHub's Windows test machines):
    # the real .NET calls, everything except moving the mouse.
    pytest.importorskip("clr")
    if sys.platform != "win32":
        pytest.skip("Windows Forms is only on Windows")
    import importlib

    module = importlib.import_module("kit.dragout_windows")
    sound = tmp_path / "kick.wav"
    sound.write_bytes(b"KICK")
    data = module.file_drop(sound)
    assert data.GetDataPresent(module.DataFormats.FileDrop)
    assert list(data.GetData(module.DataFormats.FileDrop)) == [str(sound)]
