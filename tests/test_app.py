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
