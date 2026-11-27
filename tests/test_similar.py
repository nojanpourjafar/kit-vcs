"""More like this: the sounds closest to one sound, in this kit and other kits."""

import json
import random
import threading
import urllib.error
import urllib.request
import wave
from pathlib import Path

import pytest

from kit.app import make_server
from kit.cli import main
from kit.fingerprint import distance, fingerprint, most_like
from kit.repo import Repo

from sounds_by_kind import KINDS

RATE = 44100


def write(path, samples):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(b"".join(int(max(-1, min(1, x)) * 32767).to_bytes(2, "little", signed=True) for x in samples))
    return path


def pack(folder, seed, kinds=("Kick", "Snare", "Hat", "808"), each=3):
    """A kit with a few generated sounds of each kind, in a folder named after the kind."""
    rnd = random.Random(seed)
    for kind in kinds:
        for i in range(each):
            write(folder / kind / f"{kind} {i}.wav", KINDS[kind](rnd))
    Repo.init(folder, author="Test")
    return folder


def test_a_sound_is_closest_to_itself_and_further_from_unlike_sounds(tmp_path):
    rnd = random.Random(3)
    kick = fingerprint(write(tmp_path / "a.wav", KINDS["Kick"](rnd)), "a.wav")
    other_kick = fingerprint(write(tmp_path / "b.wav", KINDS["Kick"](rnd)), "b.wav")
    hat = fingerprint(write(tmp_path / "c.wav", KINDS["Hat"](rnd)), "c.wav")
    assert distance(kick, kick) == 0
    assert distance(kick, other_kick) == pytest.approx(distance(other_kick, kick))
    assert distance(kick, other_kick) < distance(kick, hat)


def test_the_closest_sounds_are_mostly_the_same_kind(tmp_path):
    folder = pack(tmp_path / "Pack", seed=5)
    right = total = 0
    for kind in ("Kick", "Snare", "Hat", "808"):
        for i in range(3):
            found = most_like([folder], folder, f"{kind}/{kind} {i}.wav", count=2)
            right += sum(place.path.startswith(kind + "/") for place, _ in found)
            total += len(found)
    assert right / total >= 0.75


def test_results_come_closest_first_from_every_kit_and_leave_out_copies(tmp_path):
    mine = pack(tmp_path / "Mine", seed=1)
    theirs = pack(tmp_path / "Theirs", seed=2)
    (mine / "Kick" / "Kick 0 copy.wav").write_bytes((mine / "Kick" / "Kick 0.wav").read_bytes())
    found = most_like([mine, theirs], mine, "Kick/Kick 0.wav", count=30)
    scores = [score for _, score in found]
    assert scores == sorted(scores)
    assert {place.kit for place, _ in found} == {"Mine", "Theirs"}
    assert all(place.path != "Kick/Kick 0 copy.wav" for place, _ in found)  # the very same file is not "like" it
    assert len(found) == 2 * 12 - 1  # every other file: 24 distinct ones, less itself (its copy is the same file)


def test_a_missing_or_unreadable_sound_is_explained(tmp_path):
    folder = pack(tmp_path / "Pack", seed=1, kinds=("Kick",), each=2)
    with pytest.raises(ValueError, match="no sound"):
        most_like([folder], folder, "Kick/nope.wav")
    write(folder / "silence.wav", [0.0] * RATE)
    with pytest.raises(ValueError, match="cannot be compared"):
        most_like([folder], folder, "silence.wav")


def test_kit_like_lists_the_closest_sounds(tmp_path, monkeypatch, capsys):
    mine = pack(tmp_path / "Mine", seed=1)
    theirs = pack(tmp_path / "Theirs", seed=2)
    monkeypatch.chdir(mine)
    main(["like", "Kick/Kick 0.wav", str(theirs), "-n", "3"])
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 3
    assert all(("Mine/" in line or "Theirs/" in line) for line in lines)


@pytest.fixture
def server(tmp_path):
    """The app with two kits opened, Theirs first and then Mine, which stays open."""
    mine = pack(tmp_path / "Mine", seed=1)
    theirs = pack(tmp_path / "Theirs", seed=2)
    server, state = make_server(theirs)
    state.open(mine)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def get(path):
        request = urllib.request.Request(base + path, headers={"X-Kit-Token": state.token})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as err:
            return err.code, err.read()

    get.mine, get.theirs = mine, theirs
    yield get
    server.shutdown()
    server.server_close()


def test_the_app_lists_similar_sounds_that_can_be_played(server):
    status, body = server("/api/similar?path=" + urllib.request.quote("Kick/Kick 0.wav"))
    assert status == 200, body
    sounds = json.loads(body)["sounds"]
    assert len(sounds) == 10
    assert {sound["label"] for sound in sounds} == {"in this kit", "in Theirs"}
    other = next(sound for sound in sounds if sound["label"] == "in Theirs")
    status, audio = server(f"/api/audio?{other['source']}")
    assert status == 200 and audio[:4] == b"RIFF"
    assert audio == (server.theirs / other["path"]).read_bytes()


def test_only_kits_opened_before_can_be_read(server, tmp_path):
    stranger = pack(tmp_path / "Stranger", seed=9, kinds=("Kick",), each=1)
    status, _ = server(f"/api/audio?kit={urllib.request.quote(str(stranger))}&path=Kick/Kick%200.wav")
    assert status != 200
    theirs = urllib.request.quote(str(server.theirs))
    for path in ("../Mine/Kick/Kick%200.wav", ".kit/HEAD", "Kick/nope.wav"):
        status, _ = server(f"/api/audio?kit={theirs}&path={path}")
        assert status != 200, path
