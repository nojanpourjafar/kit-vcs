"""Record the demo GIF at the top of the README.

    pip install playwright pillow
    python -m playwright install chromium
    python scripts/demo_gif.py docs/demo.gif

It builds three small kits of synthesized sounds in a temporary folder, starts
the same server `kit ui` uses, and clicks through the app in a hidden browser,
taking a picture after every step. A drawn arrow stands in for the mouse,
since a hidden browser has no pointer of its own. Nothing outside the
temporary folder is touched: the app's own settings go there too.

Run it again after changing ui.html, so the GIF shows the app as it is.
"""

from __future__ import annotations

import io
import math
import os
import random
import struct
import sys
import tempfile
import threading
import wave
from pathlib import Path

RATE = 44100
WIDTH, HEIGHT = 1100, 760  # the browser window; the GIF is scaled down from this
GIF_WIDTH = 880


# ---- the sounds ---------------------------------------------------------------

def save(path: Path, samples: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    peak = max(1e-9, max(abs(x) for x in samples))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(b"".join(struct.pack("<h", int(x / peak * 0.9 * 32767)) for x in samples))


def hz(note: int) -> float:
    return 440 * 2 ** ((note - 69) / 12)


def eight08(note: int, seconds: float = 1.4, grit: float = 0.0) -> list[float]:
    out, phase = [], 0.0
    for i in range(int(seconds * RATE)):
        t = i / RATE
        phase += 2 * math.pi * hz(note) * (1 + 0.6 * math.exp(-t / 0.015)) / RATE
        x = math.sin(phase) * math.exp(-t / 0.55)
        out.append(math.tanh(x * (1 + 4 * grit)) if grit else x)
    return out


def hit(kind: str, seed: int = 0) -> list[float]:
    rnd = random.Random(seed)
    if kind == "kick":
        return [math.sin(2 * math.pi * (50 * i / RATE + 3.6 * (1 - math.exp(-i / RATE / 0.03))))
                * math.exp(-i / RATE / 0.12) for i in range(int(0.4 * RATE))]
    if kind == "snare":
        return [(rnd.uniform(-1, 1) * 0.7 + 0.3 * math.sin(2 * math.pi * 190 * i / RATE))
                * math.exp(-i / RATE / 0.07) for i in range(int(0.3 * RATE))]
    length, decay = (0.5, 0.12) if kind == "open hat" else (0.1, 0.015)
    return [rnd.uniform(-1, 1) * math.exp(-i / RATE / decay) for i in range(int(length * RATE))]


def groove(bpm: float, bars: int = 2) -> list[float]:
    """Kick, snare and hats on a grid, the way a drum loop is cut: whole bars."""
    step = 60 / bpm / 4  # sixteenth notes
    out = [0.0] * int(bars * 16 * step * RATE)
    pattern = {"kick": [0, 7, 10], "snare": [4, 12], "hat": list(range(0, 16, 2))}
    for bar in range(bars):
        for kind, steps in pattern.items():
            sound = hit(kind, seed=bar)
            gain = {"kick": 1.0, "snare": 0.7, "hat": 0.25}[kind]
            for s in steps:
                start = int((bar * 16 + s) * step * RATE)
                for i, x in enumerate(sound[:len(out) - start]):
                    out[start + i] += gain * x
    return out


def bells(notes: list[int], bpm: float = 140) -> list[float]:
    step = 60 / bpm / 2
    out = [0.0] * int(len(notes) * step * RATE + RATE)
    for n, note in enumerate(notes):
        start = int(n * step * RATE)
        for i in range(int(1.0 * RATE)):
            t = i / RATE
            out[start + i] += sum(math.sin(2 * math.pi * hz(note) * k * t) / k ** 2 for k in (1, 2, 3)) * math.exp(-t / 0.35)
    return out


def make_kits(root: Path) -> Path:
    """Three kits; the first has two saved versions and a few unsaved changes."""
    from kit.repo import Repo

    dark = root / "Dark Trap"
    save(dark / "808s" / "Big 808.wav", eight08(30))                       # F#1
    save(dark / "808s" / "Glide 808.wav", eight08(33))                     # A1
    save(dark / "Drums" / "Kick Punch.wav", hit("kick"))
    save(dark / "Drums" / "Snare Crack.wav", hit("snare", 2))
    save(dark / "Drums" / "Hat Closed.wav", hit("hat", 3))
    save(dark / "Loops" / "Bounce Groove.wav", groove(142))
    save(dark / "Loops" / "Dark Bells 140bpm F#m.wav", bells([66, 69, 73, 71, 69, 66, 64, 66]))
    repo = Repo.init(dark, author="Nojan")
    repo.commit("first pack")
    save(dark / "Drums" / "Snare Crack.wav", hit("snare", 9))
    save(dark / "Drums" / "Clap Wide.wav", hit("snare", 5))
    repo.commit("tighter snare, added a clap", author="Lucas")
    # Not saved yet: a dirtier 808 and a new open hat.
    save(dark / "808s" / "Big 808.wav", eight08(30, grit=1.2))
    save(dark / "Drums" / "Hat Open.wav", hit("open hat", 4))

    for name, bpm in (("Drill Loops", 144), ("Lo-fi Keys", 82)):
        save(root / name / "Loops" / f"loop {bpm}bpm.wav", groove(bpm, bars=1))
        Repo.init(root / name, author="Nojan").commit("first")
    return dark


# ---- the recording ------------------------------------------------------------

ARROW = """
const arrow = document.createElement("div");
arrow.id = "demo-arrow";
arrow.innerHTML = '<svg width="22" height="30" viewBox="0 0 22 30"><path d="M2 2 L2 24 L8 18 L12 28 L16 26 L12 17 L20 17 Z" fill="#111" stroke="#fff" stroke-width="2" stroke-linejoin="round"/></svg>';
Object.assign(arrow.style, {position: "fixed", left: "0px", top: "0px", zIndex: 99999, pointerEvents: "none",
  filter: "drop-shadow(0 1px 2px rgba(0,0,0,.35))"});
document.body.append(arrow);
"""


class Recorder:
    def __init__(self, page):
        self.page = page
        self.frames: list[tuple[bytes, int]] = []  # (PNG, milliseconds)
        self.x, self.y = WIDTH * 0.6, HEIGHT * 0.7

    def arrow(self) -> None:
        if not self.page.evaluate("Boolean(document.getElementById('demo-arrow'))"):
            self.page.evaluate(ARROW)  # the page was replaced (it is not, normally); draw it again
        self.page.evaluate(f"Object.assign(document.getElementById('demo-arrow').style, "
                           f"{{left: '{self.x - 2:.0f}px', top: '{self.y - 2:.0f}px'}})")

    def shot(self, ms: int) -> None:
        self.arrow()
        self.frames.append((self.page.screenshot(), ms))

    def hold(self, ms: int) -> None:
        self.shot(ms)

    def move_to(self, target, steps: int = 9) -> None:
        box = self.page.locator(target).first.bounding_box() if isinstance(target, str) else target
        to_x, to_y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        from_x, from_y = self.x, self.y
        for i in range(1, steps + 1):
            k = 0.5 - 0.5 * math.cos(math.pi * i / steps)  # ease in and out
            self.x, self.y = from_x + (to_x - from_x) * k, from_y + (to_y - from_y) * k
            self.shot(40)

    def click(self, target: str, wait: int = 350) -> None:
        self.move_to(target)
        self.page.mouse.click(self.x, self.y)
        self.page.wait_for_timeout(wait)
        self.shot(120)

    def type(self, target: str, text: str) -> None:
        self.click(target, wait=100)
        for letter in text:
            self.page.keyboard.type(letter)
            self.shot(45)


def record(url: str):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        page.goto(url)
        page.wait_for_timeout(800)
        page.evaluate(ARROW)
        # The kits live in a temporary folder with a long random name; show them
        # where a producer would keep them.
        page.evaluate("""for (const small of document.querySelectorAll('.kits small'))
          small.textContent = '~/Music/' + small.textContent.split('/').pop();""")
        shot = Recorder(page)

        shot.hold(1400)                                                 # the home screen
        shot.click(".kits .open >> nth=0", wait=1500)                   # open Dark Trap
        shot.hold(1800)                                                 # changes, waveforms, ≈F#
        shot.click("main li.sound:has-text('Big 808') .play", wait=100)
        for _ in range(7):                                              # the new, dirtier 808 plays
            page.wait_for_timeout(110)
            shot.shot(110)
        shot.click("main li.sound:has-text('Big 808') button.before", wait=100)
        for _ in range(7):                                              # and the one saved in v1
            page.wait_for_timeout(110)
            shot.shot(110)
        shot.click("main li.sound:has-text('Big 808') .star")
        shot.click("main li.sound:has-text('Glide 808') .star")
        shot.click("button.only", wait=300)                             # starred only
        shot.hold(1300)
        shot.click("button.only", wait=300)
        shot.type("main .search", "hat")
        shot.hold(1100)
        page.fill("main .search", "")
        page.dispatch_event("main .search", "input")
        shot.type("form.row input[type=text]", "dirtier 808, open hat")
        shot.click("form.row button.primary", wait=1200)                # save v3
        shot.hold(1300)
        page.wait_for_timeout(2500)                                     # let the message fade, unrecorded
        shot.click("#history li:has-text('v3') button", wait=900)
        shot.hold(900)
        shot.move_to(".compare select")
        page.select_option(".compare select", label="v1: first pack")
        page.wait_for_timeout(900)
        shot.hold(2600)                                                 # everything since v1
        browser.close()
    return shot.frames


def write_gif(frames, path: Path) -> None:
    from PIL import Image

    images, durations = [], []
    for png, ms in frames:
        image = Image.open(io.BytesIO(png)).convert("RGB")
        image = image.resize((GIF_WIDTH, round(HEIGHT * GIF_WIDTH / WIDTH)), Image.LANCZOS)
        if images and image.tobytes() == images[-1].tobytes():
            durations[-1] += ms  # an unchanged picture just stays up longer
            continue
        images.append(image)
        durations.append(ms)
    # One palette for the whole GIF, taken from a few frames, so colours do not flicker.
    sample = Image.new("RGB", (GIF_WIDTH, images[0].height * 3))
    for i, image in enumerate(images[:: max(1, len(images) // 3)][:3]):
        sample.paste(image, (0, i * image.height))
    palette = sample.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    frames = [image.quantize(palette=palette, dither=Image.Dither.NONE) for image in images]
    path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True)
    print(f"Wrote {path}: {len(frames)} frames, {sum(durations) / 1000:.1f} s, {path.stat().st_size // 1024} KB")


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/demo.gif").resolve()
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        os.environ["KIT_CONFIG_DIR"] = str(temp / "settings")  # the app's recent list, kept in here
        (temp / "settings").mkdir()
        (temp / "settings" / "settings.json").write_text('{"name": "Nojan"}')
        from kit.app import App, make_server

        root = temp / "Music"
        make_kits(root)
        for name in ("Lo-fi Keys", "Drill Loops", "Dark Trap"):         # newest last, so Dark Trap is on top
            App(root / name)
        server, _ = make_server()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            frames = record(f"http://127.0.0.1:{server.server_address[1]}/")
        finally:
            server.shutdown()
        write_gif(frames, out)


if __name__ == "__main__":
    main()
