"""The app compresses saved WAV files by itself, in the background."""

import time

from kit.app import App
from kit.repo import Repo

from test_app import app  # noqa: F401  (the running app, used as a fixture)
from test_compact import wav


def wait_until_done(app_object, seconds=20):
    deadline = time.time() + seconds
    while app_object.compacting and time.time() < deadline:
        time.sleep(0.05)
    assert not app_object.compacting


def packed(kit):
    return list((kit / ".kit" / "objects").rglob("*.kz"))


def test_saving_in_the_app_compresses_in_the_background(app, kit):
    app.app.auto_compact = True
    app.json("/api/init", {"author": "Nojan"})
    (kit / "Loops").mkdir()
    (kit / "Loops" / "groove.wav").write_bytes(wav(seconds=1.0))
    app.json("/api/commit", {"message": "a loop"})
    wait_until_done(app.app)
    assert len(packed(kit)) == 1
    state = app.json("/api/state")
    assert state["compacting"] is False and state["compacted"]["files"] == 1 and state["compacted"]["saved"] > 50_000
    assert Repo(kit).verify() == []


def test_it_is_off_unless_the_app_turns_it_on(app, kit):
    # Scripts, tests and `make_server` alone leave the store as it is.
    app.json("/api/init", {"author": "Nojan"})
    (kit / "loop.wav").write_bytes(wav())
    app.json("/api/commit", {"message": "a loop"})
    assert not app.app.compacting and packed(kit) == []


def test_opening_an_older_kit_compresses_it(kit):
    (kit / "loop.wav").write_bytes(wav())
    Repo.init(kit, author="Nojan").commit("saved before compression existed")
    app = App()
    app.auto_compact = True
    app.act("open", {"folder": str(kit)})
    wait_until_done(app)
    assert len(packed(kit)) == 1


def test_one_run_at_a_time_and_nothing_breaks_meanwhile(kit):
    for i in range(4):
        (kit / f"loop{i}.wav").write_bytes(wav(seed=i))
    repo = Repo.init(kit, author="Nojan")
    repo.commit("four loops")
    app = App(kit)
    app.auto_compact = True
    app.compact_soon()
    app.compact_soon()                                   # already running: does nothing
    while app.compacting:                                # the kit stays usable while it works
        assert len(app.state()["sounds"]) == 7
        time.sleep(0.02)
    assert len(packed(kit)) == 4 and repo.verify() == []
    repo.restore("v1", force=True)
    assert (kit / "loop2.wav").read_bytes() == wav(seed=2)


def test_a_failure_is_logged_not_raised(kit, monkeypatch):
    from kit import diagnostics
    from kit.app import config_dir
    from kit.objects import ObjectStore
    (kit / "loop.wav").write_bytes(wav())
    Repo.init(kit, author="Nojan").commit("a loop")
    monkeypatch.setattr(ObjectStore, "compact", lambda self, **kw: 1 / 0)
    app = App(kit)
    app.auto_compact = True
    app.compact_soon()
    wait_until_done(app)
    assert "ZeroDivisionError" in diagnostics.recent(config_dir())[-1]
