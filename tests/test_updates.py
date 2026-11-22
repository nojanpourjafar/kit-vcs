"""The note that a newer Kit is out. GitHub is never really asked here."""

import json

import pytest

from kit import updates

from test_app import app  # noqa: F401  (the running app, used as a fixture)


@pytest.fixture
def github(monkeypatch):
    """Stand in for GitHub: `github.tag` is what it answers, `github.asked` counts requests."""
    monkeypatch.delenv("KIT_NO_UPDATE_CHECK", raising=False)

    class Fake:
        tag = "v0.14.0"
        asked = 0
        error = None

    def fetch():
        Fake.asked += 1
        if Fake.error:
            raise Fake.error
        return Fake.tag

    monkeypatch.setattr(updates, "fetch_latest_tag", fetch)
    return Fake


@pytest.mark.parametrize("text, parsed", [
    ("v0.14.0", (0, 14, 0)), ("1.2.3", (1, 2, 3)), (" v10.0.1 ", (10, 0, 1)),
    ("v1.0-beta", None), ("v1.0", None), ("latest", None), ("", None), (None, None),
])
def test_versions_are_read_as_numbers(text, parsed):
    assert updates.parse(text) == parsed


def test_a_newer_release_is_reported(github, tmp_path):
    cache = tmp_path / "update.json"
    assert updates.newer_version(cache, running="0.13.0") == "0.14.0"
    assert updates.newer_version(cache, running="0.9.9") == "0.14.0"     # 14 > 9: numbers, not text
    assert updates.newer_version(cache, running="0.14.0") is None         # already up to date
    assert updates.newer_version(cache, running="0.15.0") is None         # a build newer than any release


def test_github_is_asked_at_most_once_a_day(github, tmp_path):
    cache = tmp_path / "update.json"
    for _ in range(5):
        updates.latest_version(cache, now=1000.0)
    assert github.asked == 1
    github.tag = "v0.15.0"
    assert updates.latest_version(cache, now=1000.0 + updates.DAY - 1) == "0.14.0"   # still the saved answer
    assert updates.latest_version(cache, now=1000.0 + updates.DAY) == "0.15.0"       # a day later, asked again
    assert github.asked == 2


def test_failures_are_quiet_and_not_remembered(github, tmp_path):
    cache = tmp_path / "update.json"
    github.error = OSError("offline")
    assert updates.newer_version(cache, running="0.13.0") is None
    assert not cache.exists()                                             # so the next launch asks again
    github.error = ValueError("not JSON")
    assert updates.newer_version(cache, running="0.13.0") is None
    github.error = None
    assert updates.newer_version(cache, running="0.13.0") == "0.14.0"


def test_odd_tags_and_damaged_saves_are_handled(github, tmp_path):
    cache = tmp_path / "update.json"
    github.tag = "v1.0-beta"
    assert updates.newer_version(cache, running="0.13.0") is None
    cache.write_text("{damaged")
    github.tag = "v0.14.0"
    assert updates.newer_version(cache, running="0.13.0") == "0.14.0"
    assert json.loads(cache.read_text())["latest"] == "0.14.0"


def test_the_check_can_be_turned_off(github, tmp_path, monkeypatch):
    monkeypatch.setenv("KIT_NO_UPDATE_CHECK", "1")
    assert updates.newer_version(tmp_path / "update.json", running="0.1.0") is None
    assert github.asked == 0


def test_the_app_shows_the_newer_version(github, app, monkeypatch):
    monkeypatch.setattr(updates, "__version__", "0.13.0")
    assert app.json("/api/update") == {"checked": False, "version": None}
    assert app.json("/api/state")["update"] is None
    app.app.check_for_update()
    assert app.json("/api/update") == {"checked": True, "version": "0.14.0"}
    assert app.json("/api/state")["update"] == "0.14.0"


def test_download_opens_the_releases_page(app, monkeypatch):
    opened = []
    monkeypatch.setattr("kit.app.webbrowser.open", opened.append)
    assert app("/api/download", {})[0] == 200
    assert opened == ["https://github.com/nojanpourjafar/kit-vcs/releases/latest"]
