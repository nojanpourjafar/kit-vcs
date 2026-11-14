"""Make a tiny drum kit of synthesized sounds, so you can try `kit` right away.

    python examples/make_demo_kit.py demo_kit

Every sound is generated from maths below (sine waves and noise), so the kit
is original and safe to share. Standard library only.
"""

import math
import random
import struct
import sys
import wave
from pathlib import Path

RATE = 44100


def save(path: Path, samples: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767)) for s in samples)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)  # 16-bit
        wav.setframerate(RATE)
        wav.writeframes(frames)


def sweep(seconds: float, start_hz: float, end_hz: float, drop: float, decay: float) -> list[float]:
    """A sine whose pitch falls from start_hz to end_hz, fading out. Kicks and 808s."""
    out, phase = [], 0.0
    for i in range(int(seconds * RATE)):
        t = i / RATE
        hz = end_hz + (start_hz - end_hz) * math.exp(-t * drop)
        phase += 2 * math.pi * hz / RATE
        out.append(math.sin(phase) * math.exp(-t * decay))
    return out


def noise(seconds: float, decay: float, seed: int) -> list[float]:
    """Bright filtered noise, fading out. Hats and the snare's rattle."""
    rng = random.Random(seed)  # fixed seed: the same file, and hash, every run
    out, previous = [], 0.0
    for i in range(int(seconds * RATE)):
        white = rng.uniform(-1, 1)
        out.append((white - previous) * 0.5 * math.exp(-(i / RATE) * decay))  # crude high-pass
        previous = white
    return out


def main() -> None:
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "demo_kit")
    kick = sweep(0.35, start_hz=160, end_hz=45, drop=35, decay=9)
    # An 808 in F: a long sine with a quick pitch drop, pushed through tanh for grit.
    eight = [math.tanh(2.5 * s) * 0.9 for s in sweep(1.2, 87.3, 43.65, drop=60, decay=2.2)]
    hat = noise(0.08, decay=60, seed=1)
    body = sweep(0.2, 260, 180, drop=30, decay=22)
    rattle = noise(0.2, decay=20, seed=2)
    snare = [0.6 * b + 0.8 * r for b, r in zip(body, rattle)]

    save(folder / "Kicks" / "kick_punch.wav", kick)
    save(folder / "808s" / "808_F.wav", eight)
    save(folder / "Hats" / "hat_closed.wav", hat)
    save(folder / "Snares" / "snare_tight.wav", snare)
    print(f"Wrote 4 sounds to {folder}/")


if __name__ == "__main__":
    main()
