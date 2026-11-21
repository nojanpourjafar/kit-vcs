"""The desktop window. A real window cannot open in a test, so pywebview is replaced by a stand-in."""

import json
import re
import subprocess
import sys
import types
import urllib.request

import pytest

from kit import cli, desktop


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

    fake = types.SimpleNamespace(create_window=create_window, start=lambda: seen.update(started=True))
    monkeypatch.setattr(desktop, "_load_webview", lambda: fake)

    desktop.run()

    assert seen["title"] == "Kit" and seen["started"] and b"<title>Kit</title>" in seen["page"]
    assert seen["api"]._window == "the window"


def test_without_pywebview_it_falls_back_to_the_browser(tmp_path):
    env = {"KIT_CONFIG_DIR": str(tmp_path), "PYTHONPATH": "src", "PATH": "", "PYTHONUNBUFFERED": "1"}
    process = subprocess.Popen([sys.executable, "-m", "kit.desktop", "--no-open"],
                               stdout=subprocess.PIPE, text=True, env=env)
    try:
        url = re.search(r"http://127\.0\.0\.1:\d+/", process.stdout.readline()).group()
        page = urllib.request.urlopen(url).read().decode()
        token = re.search(r'const TOKEN = "([^"]+)"', page).group(1)
        request = urllib.request.Request(url + "api/state", headers={"X-Kit-Token": token})
        assert json.loads(urllib.request.urlopen(request).read()) == {"home": True, "recent": []}
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
