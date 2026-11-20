"""Draw the app's icon.png and logo.png in the house style: a round dark disc, a white line
symbol with one blue accent, and the blue CJ badge (copied from another app's icon).

    python3 -m tools.make_icon ../app-mqtt/icon.png
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

APP = Path(__file__).resolve().parent.parent
SCALE = 4
DISC = (38, 50, 56, 255)
WHITE = (255, 255, 255, 255)
BLUE = (76, 141, 255, 255)
BADGE = (203.5, 203.5, 49.5)


def icon(reference: Path) -> Image.Image:
    big = Image.new("RGBA", (256 * SCALE, 256 * SCALE), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    s = SCALE

    def line(points: list[tuple[float, float]], fill=WHITE, width: float = 5) -> None:
        d.line([(x * s, y * s) for x, y in points], fill=fill, width=int(width * s), joint="curve")
        r = width * s / 2
        for x, y in (points[0], points[-1]):
            d.ellipse([x * s - r, y * s - r, x * s + r, y * s + r], fill=fill)

    d.ellipse([0, 0, 256 * s - 1, 256 * s - 1], fill=DISC)
    # A flatbed scanner seen from the front: the lid open, the body, the light under the glass
    line([(66, 84), (190, 122)])
    d.rounded_rectangle([64 * s, 128 * s, 192 * s, 170 * s], radius=9 * s, outline=WHITE, width=5 * s)
    line([(82, 149), (142, 149)], BLUE)
    line([(160, 149), (174, 149)])
    image = big.resize((256, 256), Image.Resampling.LANCZOS)
    # The badge, pixel for pixel
    ref = Image.open(reference).convert("RGBA")
    cx, cy, radius = BADGE
    for y in range(256):
        for x in range(256):
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius**2:
                image.putpixel((x, y), ref.getpixel((x, y)))
    return image


def logo(mark: Image.Image, name: str, like: Path) -> Image.Image:
    image = Image.new("RGBA", (500, 200), (0, 0, 0, 0))
    small = mark.resize((150, 150), Image.Resampling.LANCZOS)
    image.alpha_composite(small, (5, 25))
    d = ImageDraw.Draw(image)
    font = None
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"):
        if Path(path).is_file():
            font = ImageFont.truetype(path, 44)
            break
    font = font or ImageFont.load_default(size=44)
    d.text((173, 100), name, fill=BLUE, font=font, anchor="lm")
    return image


if __name__ == "__main__":
    reference = Path(sys.argv[1] if len(sys.argv) > 1 else APP.parent / "app-mqtt" / "icon.png")
    mark = icon(reference)
    mark.save(APP / "icon.png")
    logo(mark, "Scanner", reference.with_name("logo.png")).save(APP / "logo.png")
    print("icon.png and logo.png written")
