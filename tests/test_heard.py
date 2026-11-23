"""Tempo and note for sounds the page decoded (MP3, AAC, Ogg).

The page decodes them and sends the samples: mono, 32-bit floats, raw. Here
the samples are made in Python instead, the way the page would send them.
"""

import json
import urllib.error
import urllib.request
import wave
import zipfile
from array import array
from pathlib import Path

import pytest

from kit import app as app_module
from kit.audio import note_of, tempo, tempo_of
from kit.repo import Repo

from test_app import app  # noqa: F401  (the running app, used as a fixture)
from test_note import an_808
from test_tempo import RATE, drums


def samples_of(wav_path):
    """The mono float samples of a 16-bit WAV file, as the page would send them."""
    with wave.open(str(wav_path)) as wav:
        channels, raw = wav.getnchannels(), wav.readframes(wav.getnframes())
    ints = array("h", raw)
    return array("f", (sum(ints[i:i + channels]) / channels / 32768 for i in range(0, len(ints), channels)))


def post(app, path, samples, **query):
    """Send samples the way the page does, and return (status, answer)."""
    query = "&".join([f"path={urllib.request.quote(path)}"] + [f"{k}={v}" for k, v in query.items()])
    data = samples.tobytes() if isinstance(samples, array) else samples
    request = urllib.request.Request(f"{app.base}/api/heard?{query}", data=data, method="POST",
                                     headers={"X-Kit-Token": app.token, "Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


@pytest.fixture
def made(tmp_path):
    """A drum loop at 128 BPM and an 808 on F#, as WAV files to take samples from."""
    drums(tmp_path / "groove.wav", 128, bars=2, channels=2)
    an_808(tmp_path / "808.wav", 30)
    return tmp_path


def test_a_decoded_loop_gets_a_tempo_and_a_one_shot_a_note(app, kit, made):
    Repo.init(kit, author="Nojan")
    (kit / "Loops").mkdir()
    (kit / "Loops" / "groove.m4a").write_bytes(b"stands in for an AAC file")    # never read: the samples are sent
    (kit / "Loops" / "808.ogg").write_bytes(b"stands in for an Ogg file")
    status, heard = post(app, "Loops/groove.m4a", samples_of(made / "groove.wav"), rate=RATE, seconds=2 * 4 * 60 / 128)
    assert status == 200 and heard["bpm"] == pytest.approx(128, abs=1) and heard["note"] is None
    status, heard = post(app, "Loops/808.ogg", samples_of(made / "808.wav"), rate=RATE, seconds=1.2)
    assert status == 200 and heard == {"bpm": None, "key": None, "note": "F#"}


def test_the_answer_is_kept_per_file(app, kit, made):
    Repo.init(kit, author="Nojan")
    (kit / "loop.m4a").write_bytes(b"x")
    first = post(app, "loop.m4a", samples_of(made / "groove.wav"), rate=RATE, seconds=3.75)[1]
    again = post(app, "loop.m4a", samples_of(made / "808.wav"), rate=RATE, seconds=1.2)[1]
    assert again == first                                                    # the same file: not worked out again
    (kit / "loop.m4a").write_bytes(b"changed")                               # a new bounce is listened to afresh
    assert post(app, "loop.m4a", samples_of(made / "808.wav"), rate=RATE, seconds=1.2)[1]["note"] == "F#"


def test_an_mp3s_own_length_is_used(app, kit, monkeypatch):
    # The tempo search depends on the exact length, and an MP3's headers give it to the sample.
    Repo.init(kit, author="Nojan")
    with zipfile.ZipFile(Path(__file__).resolve().parent / "data" / "mp3_samples.zip") as archive:
        (kit / "tone.mp3").write_bytes(archive.read("cbr320.mp3"))          # exactly 1.00 s
    seen = []
    monkeypatch.setitem(app.app.heard.__func__.__globals__, "tempo_of",
                        lambda samples, rate, seconds: seen.append(seconds))
    post(app, "tone.mp3", array("f", [0.0] * 22050), rate=22050, seconds=1.05)  # the page's figure is a little off
    assert seen == [pytest.approx(1.0)]


def test_bad_requests_are_refused(app, kit, made, monkeypatch):
    Repo.init(kit, author="Nojan")
    (kit / "loop.m4a").write_bytes(b"x")
    some = samples_of(made / "808.wav")
    assert post(app, "loop.m4a", some, rate=1000, seconds=1)[0] == 400                 # not a real sample rate
    assert post(app, "loop.m4a", some, seconds=1)[0] == 400                            # no rate
    assert post(app, "missing.m4a", some, rate=RATE, seconds=1)[0] == 400              # no such sound
    assert post(app, ".kit/HEAD", some, rate=RATE, seconds=1)[0] == 400                 # never Kit's own files
    assert post(app, "loop.m4a", b"", rate=RATE, seconds=1)[0] == 400                  # nothing sent
    monkeypatch.setattr(app_module, "HEARD_LIMIT", 1000)
    assert post(app, "loop.m4a", some, rate=RATE, seconds=1)[0] == 400                 # too much


def test_the_token_is_needed(app, kit, made):
    Repo.init(kit, author="Nojan")
    (kit / "loop.m4a").write_bytes(b"x")
    request = urllib.request.Request(f"{app.base}/api/heard?path=loop.m4a&rate={RATE}", method="POST",
                                     data=samples_of(made / "808.wav").tobytes())
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(request)
    assert refused.value.code == 403


def test_decoded_samples_give_the_same_answers_as_the_file(made):
    assert tempo_of(samples_of(made / "groove.wav"), RATE, 3.75) == tempo(made / "groove.wav", "groove.wav")
    assert note_of(list(samples_of(made / "808.wav")), RATE) == "F#"
    assert tempo_of(samples_of(made / "808.wav"), RATE, 1.2) is None         # one hit is not a beat
    assert tempo_of(array("f"), RATE, 0.5) is None
