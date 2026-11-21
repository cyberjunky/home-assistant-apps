"""Write icon.png and logo.png in the repository's house style.

A round dark disc, a white line drawing of a pulse on a bus line with one blue beat, and the blue CJ badge
copied from another app's icon: python3 -m tools.make_icons ../app-tesla/icon.png
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SCALE = 4
DISC, WHITE, BLUE = (38, 50, 56, 255), (255, 255, 255, 255), (76, 141, 255, 255)
BADGE_CENTRE, BADGE_RADIUS = 203.5, 49.5
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def icon(reference: Path) -> Image.Image:
    big = Image.new("RGBA", (256 * SCALE, 256 * SCALE), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)

    def s(*values: float) -> list[float]:
        return [v * SCALE for v in values]

    def polyline(points: list[tuple[float, float]], fill: tuple, width: float) -> None:
        d.line([(x * SCALE, y * SCALE) for x, y in points], fill=fill, width=int(width * SCALE), joint="curve")
        for x, y in (points[0], points[-1]):
            r = width / 2
            d.ellipse(s(x - r, y - r, x + r, y + r), fill=fill)

    d.ellipse(s(0, 0, 256, 256), fill=DISC)
    # The lens: a ring, and through it the bus line with one telegram on it
    d.ellipse(s(52, 52, 204, 204), outline=WHITE, width=5 * SCALE)
    polyline([(74, 128), (100, 128)], WHITE, 5)
    polyline([(100, 128), (113, 92), (133, 164), (146, 128)], BLUE, 6)
    polyline([(146, 128), (182, 128)], WHITE, 5)
    out = big.resize((256, 256), Image.LANCZOS)
    badge = Image.open(reference).convert("RGBA")
    for y in range(256):
        for x in range(256):
            if (x - BADGE_CENTRE) ** 2 + (y - BADGE_CENTRE) ** 2 <= BADGE_RADIUS**2:
                out.putpixel((x, y), badge.getpixel((x, y)))
    return out


def logo(mark: Image.Image, name: str) -> Image.Image:
    out = Image.new("RGBA", (500, 200), (0, 0, 0, 0))
    small = mark.resize((150, 150), Image.LANCZOS)
    out.paste(small, (5, 25), small)
    ImageDraw.Draw(out).text((172, 100), name, font=ImageFont.truetype(FONT, 46), fill=BLUE, anchor="lm")
    return out


if __name__ == "__main__":
    made = icon(Path(sys.argv[1]))
    made.save(ROOT / "icon.png")
    logo(made, "KNX-Ray").save(ROOT / "logo.png")
