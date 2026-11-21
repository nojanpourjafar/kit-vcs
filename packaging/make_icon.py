"""Draw Kit's app icon and save it in the formats each system wants.

    pip install pillow
    python packaging/make_icon.py

Writes icon.png (1024 x 1024), Kit.icns (macOS) and Kit.ico (Windows) next to
this script. The results are committed, so building the app does not need
Pillow; run this again only to change the icon.

The icon is a dark tile with an orange waveform. The colours are the ones the
app uses in dark mode.
"""

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
SIZE = 1024
SCALE = 4  # draw 4x larger and shrink at the end, which smooths the edges

TILE_TOP, TILE_BOTTOM = (36, 40, 49), (16, 18, 22)
ORANGE = (251, 146, 60)
# Bar heights from 0 to 1, left to right. Uneven on purpose, like real audio;
# a smooth rise and fall would read as a "play" triangle at small sizes.
BARS = [0.20, 0.46, 0.74, 0.42, 1.00, 0.68, 0.84, 0.36, 0.56, 0.24]


def draw() -> Image.Image:
    big = SIZE * SCALE
    # macOS icons leave a margin around the tile; 100 of 1024 is Apple's grid.
    margin, radius = 100 * SCALE, 185 * SCALE
    tile = (margin, margin, big - margin, big - margin)

    # A vertical gradient, cut to a rounded square by a mask.
    gradient = Image.new("RGB", (1, big))
    for y in range(big):
        t = y / (big - 1)
        gradient.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(TILE_TOP, TILE_BOTTOM)))
    gradient = gradient.resize((big, big))
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle(tile, radius, fill=255)
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    image.paste(gradient, mask=mask)

    # The waveform: rounded bars, mirrored around the tile's middle line.
    pen = ImageDraw.Draw(image)
    left, right = 240 * SCALE, big - 240 * SCALE
    step = (right - left) / len(BARS)
    width = step * 0.58
    middle, tallest = big / 2, 250 * SCALE
    for i, height in enumerate(BARS):
        x = left + i * step + (step - width) / 2
        half = max(height * tallest, width / 2)
        # A bar with round ends: a rectangle plus a circle at each end.
        # (Pillow's rounded_rectangle draws the ends too wide at this radius.)
        r = width / 2
        pen.rectangle((x, middle - half + r, x + width, middle + half - r), fill=ORANGE)
        pen.ellipse((x, middle - half, x + width, middle - half + width), fill=ORANGE)
        pen.ellipse((x, middle + half - width, x + width, middle + half), fill=ORANGE)

    return image.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    icon = draw()
    icon.save(HERE / "icon.png")
    icon.save(HERE / "Kit.icns")
    icon.save(HERE / "Kit.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"Wrote icon.png, Kit.icns and Kit.ico in {HERE}")


if __name__ == "__main__":
    main()
