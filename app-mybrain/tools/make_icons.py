"""Write icon.png and logo.png in the repository's house style.

A round dark disc, the white outline of a brain with a blue network of linked dots in it, and the
blue CJ badge copied from another app's icon: python3 -m tools.make_icons ../app-tesla/icon.png
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
SCALE = 4
DISC, WHITE = (38, 50, 56, 255), (255, 255, 255, 255)
BLUE = (76, 141, 255, 255)
BADGE_CENTRE, BADGE_RADIUS = 203.5, 49.5
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
# The brain: lobes as overlapping circles (x, y, r), mirrored left and right of the middle
LOBES = [(104, 94, 26), (86, 122, 26), (98, 150, 24), (114, 162, 18), (120, 82, 22)]
STROKE = 5


def icon(reference: Path) -> Image.Image:
    size = 256 * SCALE
    big = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    d.ellipse((0, 0, size, size), fill=DISC)

    # The outline: the union of the lobes, grown minus shrunk by half a stroke each way
    mask = Image.new("L", (size, size), 0)
    m = ImageDraw.Draw(mask)
    for x, y, r in LOBES:
        for cx in (x, 256 - x):
            m.ellipse([(cx - r) * SCALE, (y - r) * SCALE, (cx + r) * SCALE, (y + r) * SCALE], fill=255)
    # The middle, so the gaps between the lobes don't show as holes
    m.ellipse([84 * SCALE, 76 * SCALE, 172 * SCALE, 168 * SCALE], fill=255)
    half = STROKE * SCALE // 2 * 2 + 1
    outline = Image.eval(
        Image.composite(
            mask.filter(ImageFilter.MaxFilter(half)),
            Image.new("L", mask.size, 0),
            Image.eval(mask.filter(ImageFilter.MinFilter(half)), lambda v: 255 - v),
        ),
        lambda v: v,
    )
    big.paste(WHITE, (0, 0), outline)
    # The fold between the halves
    d.line([128 * SCALE, 66 * SCALE, 128 * SCALE, 172 * SCALE], fill=WHITE, width=STROKE * SCALE)

    # Linked dots in blue: what is saved, and how it connects
    nodes = [(98, 100), (158, 100), (104, 146), (152, 146)]
    for (x0, y0), (x1, y1) in ((nodes[0], nodes[1]), (nodes[0], nodes[2]), (nodes[1], nodes[3]), (nodes[2], nodes[3])):
        d.line([x0 * SCALE, y0 * SCALE, x1 * SCALE, y1 * SCALE], fill=BLUE, width=3 * SCALE)
    for x, y in nodes:
        r = 7
        d.ellipse([(x - r) * SCALE, (y - r) * SCALE, (x + r) * SCALE, (y + r) * SCALE], fill=BLUE)

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
    logo(made, "MyBrain").save(ROOT / "logo.png")
