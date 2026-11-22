"""Write icon.png and logo.png in the repository's house style.

A round dark disc, a white line drawing of a lottery ticket with an orange bitcoin sign on it, and the
blue CJ badge copied from another app's icon: python3 -m tools.make_icons ../app-tesla/icon.png
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SCALE = 4
DISC, WHITE = (38, 50, 56, 255), (255, 255, 255, 255)
ORANGE, BLUE = (247, 147, 26, 255), (76, 141, 255, 255)
BADGE_CENTRE, BADGE_RADIUS = 203.5, 49.5
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def icon(reference: Path) -> Image.Image:
    big = Image.new("RGBA", (256 * SCALE, 256 * SCALE), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)

    def s(*values: float) -> list[float]:
        return [v * SCALE for v in values]

    d.ellipse(s(0, 0, 256, 256), fill=DISC)

    # The ticket: a rounded card, a notch in each side, a perforation before the stub
    left, top, right, bottom, notch = 60, 90, 196, 166, 13
    middle = (top + bottom) / 2
    d.rounded_rectangle(s(left, top, right, bottom), radius=12 * SCALE, outline=WHITE, width=5 * SCALE)
    for x, start, end in ((left, 270, 90), (right, 90, 270)):
        d.ellipse(s(x - notch, middle - notch, x + notch, middle + notch), fill=DISC)
        d.arc(s(x - notch, middle - notch, x + notch, middle + notch), start, end, fill=WHITE, width=5 * SCALE)
    stub = 156
    for y in range(top + 12, bottom - 8, 11):
        d.line(s(stub, y, stub, y + 5), fill=WHITE, width=4 * SCALE)

    # The bitcoin sign on the ticket, in bitcoin orange
    bx, b_top, b_bottom, w = 96, 106, 150, 5
    b_mid = (b_top + b_bottom) / 2
    d.line(s(bx, b_top, bx, b_bottom), fill=ORANGE, width=w * SCALE)
    for y0, y1, reach in ((b_top, b_mid, 22), (b_mid, b_bottom, 25)):
        r = (y1 - y0) / 2
        d.line(s(bx, y0, bx + reach - r, y0), fill=ORANGE, width=w * SCALE)
        d.line(s(bx, y1, bx + reach - r, y1), fill=ORANGE, width=w * SCALE)
        d.arc(s(bx + reach - 2 * r, y0, bx + reach, y1), 270, 90, fill=ORANGE, width=w * SCALE)
    for x in (bx + 5, bx + 14):
        d.line(s(x, b_top - 8, x, b_top), fill=ORANGE, width=4 * SCALE)
        d.line(s(x, b_bottom, x, b_bottom + 8), fill=ORANGE, width=4 * SCALE)
    # Round ends on the stroke
    for x, y in ((bx, b_top), (bx, b_bottom)):
        d.ellipse(s(x - 2.5, y - 2.5, x + 2.5, y + 2.5), fill=ORANGE)

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
    logo(made, "Lottery").save(ROOT / "logo.png")
