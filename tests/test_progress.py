"""Progress while a big kit is read for the first time."""

import json
import os
import threading
import time
import urllib.request

from kit.app import make_server
from kit.cli import _Progress
from kit.manifest import scan
from kit.repo import Repo


def test_the_scan_reports_its_progress_and_skips_known_files(kit):
    calls = []
    cache = {}
    old = time.time() - 60                                    # older than the 2 s "too new to trust" window
    for path in kit.rglob("*.wav"):
        os.utime(path, (old, old))
    scan(kit, cache, lambda *numbers: calls.append(numbers))
    assert calls == [(1, 3, 300, 1100), (2, 3, 700, 1100), (3, 3, 1100, 1100)]   # files in order, then sizes

    calls.clear()
    scan(kit, cache, lambda *numbers: calls.append(numbers))
    assert calls == []                                        # nothing to read: the cache knows every file


def test_progress_answers_while_a_scan_holds_the_lock(kit):
    Repo.init(kit, author="Nojan")
    server, app = make_server(kit)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/api/progress"
    request = urllib.request.Request(url, headers={"X-Kit-Token": app.token})
    try:
        app._report(40, 100, 4_000, 10_000)
        with app.lock:                                        # as during a long scan
            started = time.monotonic()
            with urllib.request.urlopen(request, timeout=5) as response:
                answer = json.loads(response.read())
            assert time.monotonic() - started < 2
        assert answer == {"files": 40, "files_total": 100, "bytes": 4000, "bytes_total": 10000}
    finally:
        server.shutdown()
        server.server_close()


def test_the_app_hears_about_its_own_scans(kit):
    from kit.app import App

    Repo.init(kit, author="Nojan")
    app = App(kit)
    app.state()
    assert app.progress["files"] == app.progress["files_total"] == 3


def test_the_terminal_line_ends_when_reading_is_done(capsys):
    show = _Progress()
    show(1, 2, 512, 2048)
    show(2, 2, 2048, 2048)
    err = capsys.readouterr().err
    assert err.startswith("\rReading the kit: 1/2 files") and err.endswith("2/2 files, 2.0 KB of 2.0 KB\n")
