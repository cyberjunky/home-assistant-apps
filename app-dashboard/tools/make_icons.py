"""Write icon.png, logo.png and the page's own icon in the repository's house style.

A round dark disc, a white line drawing of a wall tablet with one orange dot, and the blue CJ badge
copied from another app's icon: python3 -m tools.make_icons ../app-tesla/icon.png
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SCALE = 4
DISC, WHITE, ORANGE, BLUE = (38, 50, 56, 255), (255, 255, 255, 255), (224, 96, 63, 255), (76, 141, 255, 255)
BADGE_CENTRE, BADGE_RADIUS = 203.5, 49.5
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def icon(reference: Path) -> Image.Image:
    big = Image.new("RGBA", (256 * SCALE, 256 * SCALE), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)

    def s(*values: float) -> list[float]:
        return [v * SCALE for v in values]

    d.ellipse(s(0, 0, 256, 256), fill=DISC)
    # The tablet, and on it: the time, a line of text, two tiles and what is on
    d.rounded_rectangle(s(66, 82, 190, 174), radius=11 * SCALE, outline=WHITE, width=5 * SCALE)
    d.line(s(83, 104, 126, 104), fill=WHITE, width=9 * SCALE)
    d.line(s(83, 121, 112, 121), fill=WHITE, width=4 * SCALE)
    d.rounded_rectangle(s(82, 135, 104, 157), radius=4 * SCALE, outline=WHITE, width=4 * SCALE)
    d.rounded_rectangle(s(111, 135, 133, 157), radius=4 * SCALE, outline=WHITE, width=4 * SCALE)
    d.line(s(146, 124, 174, 124), fill=WHITE, width=4 * SCALE)
    d.line(s(146, 139, 174, 139), fill=WHITE, width=4 * SCALE)
    d.line(s(146, 154, 164, 154), fill=WHITE, width=4 * SCALE)
    d.ellipse(s(153, 95, 169, 111), fill=ORANGE)
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
    made.save(ROOT / "src" / "web" / "icon.png")
    logo(made, "Dashboard").save(ROOT / "logo.png")
