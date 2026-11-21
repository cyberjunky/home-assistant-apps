"""Write icon.png and logo.png in the repository's house style.

A round dark disc, a white line drawing of a marshal's star with a blue check in it, and the blue CJ
badge copied from another app's icon: python3 -m tools.make_icons ../app-tesla/icon.png
"""

import math
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

    d.ellipse(s(0, 0, 256, 256), fill=DISC)
    # The star: six points, a ball on each tip, centred on the disc
    centre, outer, inner = 128, 70, 38
    points = []
    for i in range(12):
        radius = outer if i % 2 == 0 else inner
        angle = math.radians(-90 + i * 30)
        points.append((centre + radius * math.cos(angle), centre + radius * math.sin(angle)))
    outline = [(x * SCALE, y * SCALE) for x, y in [*points, points[0], points[1]]]
    d.line(outline, fill=WHITE, width=5 * SCALE, joint="curve")
    for x, y in points[::2]:
        d.ellipse(s(x - 7, y - 7, x + 7, y + 7), fill=WHITE)
    # The check: what Marshal found in order
    d.line(s(110, 129, 123, 142, 148, 115), fill=BLUE, width=8 * SCALE, joint="curve")
    for x, y in ((110, 129), (148, 115)):
        d.ellipse(s(x - 4, y - 4, x + 4, y + 4), fill=BLUE)
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
    logo(made, "Marshal").save(ROOT / "logo.png")
