"""The browser app: its JSON endpoints and the checks that guard them."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from kit.app import make_server
from kit.repo import Repo


@pytest.fixture
def app(kit):
    """A running server for the kit folder. `call` makes requests the way the page does."""
    server, state = make_server(kit)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(path, body=None, token=state.token, headers=None):
        request = urllib.request.Request(
            base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"X-Kit-Token": token, **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as err:
            return err.code, err.read(), err.headers

    call.json = lambda path, body=None: json.loads(call(path, body)[1])
    call.token = state.token
    yield call
    server.shutdown()
    server.server_close()


def test_the_page_is_served_with_its_token_filled_in(app):
    status, body, headers = app("/", token="")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert app.token.encode() in body and b"__KIT_TOKEN__" not in body


def test_an_untracked_folder_says_so(app, kit):
    state = app.json("/api/state")
    assert state["repo"] is False and state["name"] == kit.name


def test_state_lists_sounds_with_their_changes_and_credits(app, kit):
    Repo.init(kit, author="Nojan").commit("first")
    (kit / "Hats" / "hat_02.wav").write_bytes(b"open hat")
    (kit / "Kicks" / "kick_02.wav").unlink()

    state = app.json("/api/state")

    assert [v["label"] for v in state["versions"]] == ["v1"]
    assert [(c["kind"], c["path"]) for c in state["changes"]] == [
        ("added", "Hats/hat_02.wav"), ("removed", "Kicks/kick_02.wav")]
    sounds = {s["path"]: s for s in state["sounds"]}
    assert sounds["Hats/hat_02.wav"]["change"] == "added" and sounds["Hats/hat_02.wav"]["by"] is None
    assert (sounds["Kicks/kick_01.wav"]["by"], sounds["Kicks/kick_01.wav"]["since"]) == ("Nojan", "v1")
    assert state["removed"] == ["Kicks/kick_02.wav"]


def test_requests_without_the_token_are_refused(app):
    assert app("/api/state", token="guess")[0] == 403
    assert app("/api/state", token="")[0] == 403


def test_requests_for_another_host_are_refused(app):
    # A hostile web page can point its own domain at 127.0.0.1 (DNS rebinding).
    # The browser then sends that domain in the Host header, which gives it away.
    assert app("/api/state", headers={"Host": "evil.example"})[0] == 403
    assert app("/", headers={"Host": "evil.example"})[0] == 403


def test_start_save_and_restore_from_the_page(app, kit):
    assert app.json("/api/init", {"author": "Nojan"})["message"]
    assert app.json("/api/state")["repo"] is True
    assert app.json("/api/commit", {"message": "first"})["message"] == "Saved v1: first"

    (kit / "Hats" / "hat_01.wav").write_bytes(b"brighter")
    app.json("/api/commit", {"message": "brighter hat"})
    first = app.json("/api/version?ref=v1")
    assert first["latest"] is False and len(first["sounds"]) == 3
    assert app.json("/api/version?ref=v2")["sounds"][0]["change"] == "modified"

    app.json("/api/restore", {"version": first["id"]})
    assert (kit / "Hats" / "hat_01.wav").read_bytes() == b"TSS" * 100


def test_problems_come_back_as_messages_not_crashes(app, kit):
    Repo.init(kit)
    status, body, _ = app("/api/commit", {"message": ""})
    assert status == 400 and "needs a message" in json.loads(body)["error"]
    assert app("/api/nonsense", {})[0] == 400
    assert app("/api/commit", {"message": "x"}, token="guess")[0] == 403


def test_audio_is_served_whole_or_in_byte_ranges(app, kit):
    repo = Repo.init(kit)
    repo.commit("first")
    data = (kit / "Kicks" / "kick_01.wav").read_bytes()
    url = f"/api/audio?path=Kicks/kick_01.wav&t={app.token}"   # the token rides in the address

    status, body, headers = app(url, token="")
    assert (status, body, headers["Accept-Ranges"]) == (200, data, "bytes")

    status, body, headers = app(url, token="", headers={"Range": "bytes=4-7"})
    assert (status, body) == (206, data[4:8])
    assert headers["Content-Range"] == f"bytes 4-7/{len(data)}"
    assert app(url, token="", headers={"Range": "bytes=-4"})[1] == data[-4:]

    digest = repo.manifest_of(repo.head())["Kicks/kick_01.wav"]["hash"]
    assert app(f"/api/audio?hash={digest}&name=kick_01.wav")[1] == data   # a saved version, by hash


def test_hidden_files_and_paths_outside_the_folder_are_never_served(app, kit):
    Repo.init(kit)
    (kit.parent / "secret.wav").write_bytes(b"private")
    for target in ("path=.kit/config.json", "path=../secret.wav", "path=/etc/passwd", "hash=../../x"):
        assert app(f"/api/audio?{target}")[0] == 404
        assert app(f"/api/peaks?{target}")[0] == 400


def test_peaks_come_back_for_wav_files_only(app, kit):
    import struct
    import wave

    Repo.init(kit)
    with wave.open(str(kit / "tone.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"".join(struct.pack("<h", s) for s in [12000, -12000] * 500))

    levels = app.json("/api/peaks?path=tone.wav")["peaks"]
    assert 80 <= len(levels) <= 96 and max(levels) == 1.0
    assert app.json("/api/peaks?path=Kicks/kick_01.wav")["peaks"] is None   # not really audio


def test_set_a_remote_then_push_and_pull(app, kit, tmp_path):
    from kit import remote

    Repo.init(kit, author="Nojan").commit("first")
    status, body, _ = app("/api/push", {})
    assert status == 400 and "set a remote first" in json.loads(body)["error"]

    shared = tmp_path / "shared"
    assert str(shared.resolve()) in app.json("/api/remote", {"remote": str(shared)})["message"]
    assert app.json("/api/push", {})["message"] == "Pushed 1 version(s)."
    assert app.json("/api/push", {})["message"] == "The remote already has every version."

    fork, _ = remote.clone(str(shared), tmp_path / "fork", author="Lucas")
    (fork.folder / "Hats" / "hat_lucas.wav").write_bytes(b"lucas hat")
    fork.commit("a hat from Lucas")
    remote.push(fork, remote.open_remote(str(shared)))

    assert app.json("/api/pull", {})["message"] == "Pulled 1 version(s)."
    sounds = {s["path"]: s["by"] for s in app.json("/api/state")["sounds"]}
    assert sounds["Hats/hat_lucas.wav"] == "Lucas"
    assert app.json("/api/pull", {})["message"] == "Already up to date."
