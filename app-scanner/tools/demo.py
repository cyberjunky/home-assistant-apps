"""Fill the app with paper that looks like paper, for screenshots and for trying the archive
(development only). Run it inside the app's container, then restart the app:

    docker exec -i <container> python3 - < tools/demo.py

It draws a few letters and invoices, saves them to the archive with text recognition, and leaves
three pages in the tray.
"""

import random
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, "/app")
from src.archive import Archive
from src.pages import Tray

DATA = Path(sys.argv[1] if len(sys.argv) > 1 else "/data")
DPI = 150
W, H = int(8.27 * DPI), int(11.69 * DPI)
LOREM = [
    "Thank",
    "you",
    "for",
    "your",
    "payment.",
    "This",
    "letter",
    "confirms",
    "the",
    "details",
    "of",
    "your",
    "account",
    "for",
    "the",
    "period",
    "shown",
    "above.",
    "Please",
    "keep",
    "it",
    "with",
    "your",
    "records.",
    "If",
    "anything",
    "on",
    "this",
    "page",
    "is",
    "not",
    "right,",
    "let",
    "us",
    "know",
    "within",
    "thirty",
    "days",
    "and",
    "we",
    "will",
    "put",
    "it",
    "right.",
    "You",
    "can",
    "reach",
    "us",
    "on",
    "working",
    "days",
    "between",
    "nine",
    "and",
    "five.",
]


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def paper(
    sender: str, colour: tuple, title: str, lines: list[tuple[str, str]], seed: int, body: int = 2
) -> Image.Image:
    rng = random.Random(seed)
    image = Image.new("RGB", (W, H), (252, 251, 248))
    d = ImageDraw.Draw(image)
    d.rectangle([90, 90, 150, 150], fill=colour)
    d.text((170, 92), sender, fill=colour, font=font(40))
    d.text((170, 140), "Postbus 1234 · 1000 AB Amsterdam", fill=(110, 110, 110), font=font(15))
    d.text((90, 250), "R. Jansen\nDorpsstraat 12\n1234 AB Ons Dorp", fill=(30, 30, 30), font=font(20), spacing=8)
    d.text(
        (W - 360, 250),
        f"Date  {rng.randint(1, 28)} November 2026\nRef   {rng.randint(100000, 999999)}",
        fill=(60, 60, 60),
        font=font(18),
        spacing=8,
    )
    d.text((90, 420), title, fill=(20, 20, 20), font=font(34))
    y = 500
    for _ in range(body):
        words = [rng.choice(LOREM) for _ in range(70)]
        line = ""
        for word in words:
            if d.textlength(line + word, font=font(19)) > W - 190:
                d.text((90, y), line, fill=(40, 40, 40), font=font(19))
                y += 30
                line = ""
            line += word + " "
        d.text((90, y), line, fill=(40, 40, 40), font=font(19))
        y += 56
    if lines:
        d.line([90, y, W - 90, y], fill=(150, 150, 150), width=2)
        y += 16
        for label, amount in lines:
            d.text((90, y), label, fill=(30, 30, 30), font=font(20))
            d.text((W - 90 - d.textlength(amount, font=font(20)), y), amount, fill=(30, 30, 30), font=font(20))
            y += 38
        d.line([90, y, W - 90, y], fill=(150, 150, 150), width=2)
    d.text((90, H - 120), f"{sender} · IBAN NL00 BANK 0123 4567 89 · page 1", fill=(130, 130, 130), font=font(14))
    # A scan is never perfectly straight or clean
    image = image.rotate(rng.uniform(-0.5, 0.5), resample=Image.Resampling.BICUBIC, fillcolor=(252, 251, 248))
    return image.filter(ImageFilter.GaussianBlur(0.4))


DOCUMENTS = [
    (
        "Energy bill November",
        ["energy", "2026"],
        "Noorderlicht Energie",
        (20, 110, 190),
        "Your energy bill",
        [("Electricity 312 kWh", "€ 93.60"), ("Gas 148 m3", "€ 177.60"), ("Total", "€ 271.20")],
        2,
    ),
    (
        "Tax assessment 2025",
        ["tax", "2025"],
        "Belastingdienst",
        (10, 80, 140),
        "Final assessment income tax",
        [("Income", "€ 48,210"), ("To receive", "€ 312")],
        3,
    ),
    (
        "Car insurance policy",
        ["insurance", "car"],
        "Veilig Verzekerd",
        (190, 60, 40),
        "Your policy for 2027",
        [("Premium per month", "€ 42.15")],
        1,
    ),
    (
        "Water bill Q3",
        ["water", "2026"],
        "Waterbedrijf Midden",
        (30, 150, 160),
        "Statement third quarter",
        [("Usage 31 m3", "€ 58.40")],
        1,
    ),
    (
        "Letter from the municipality",
        ["municipality"],
        "Gemeente Ons Dorp",
        (120, 60, 150),
        "Work on your street",
        [],
        1,
    ),
    (
        "Dentist invoice",
        ["health", "2026"],
        "Tandartspraktijk De Linde",
        (60, 140, 70),
        "Invoice",
        [("Check-up", "€ 27.50"), ("X-ray", "€ 19.80"), ("Total", "€ 47.30")],
        1,
    ),
    (
        "Rental agreement",
        ["house"],
        "Woonstad Verhuur",
        (90, 90, 90),
        "Rental agreement",
        [("Rent per month", "€ 1,145.00")],
        4,
    ),
]

tmp = DATA / "demo"
tmp.mkdir(exist_ok=True)
tray = Tray(DATA / "tray")
archive = Archive(DATA / "archive")
for n, (name, tags, sender, colour, title, lines, pages) in enumerate(DOCUMENTS):
    added = []
    for p in range(pages):
        path = tmp / f"{n}-{p}.png"
        paper(
            sender,
            colour,
            title if p == 0 else f"{title} (continued)",
            lines if p == 0 else [],
            n * 10 + p,
            2 if p == 0 else 5,
        ).save(path)
        added.append(tray.add(path, DPI, 85))
    doc = archive.add(added, [tray.path(p) for p in added], name, tags, ["eng", "nld"], "pixma:04A9176D")
    # Spread them over the last weeks
    archive.db.execute(
        "UPDATE documents SET created = ? WHERE id = ?", (time.time() - (len(DOCUMENTS) - n) * 3.3 * 86400, doc["id"])
    )
    archive.db.commit()
    tray.clear([p["id"] for p in added])
    print(f"{name}: {pages} page(s), text {'found' if doc['has_text'] else 'NOT found'}, {doc['size'] // 1024} KiB")
for p in range(3):
    path = tmp / f"tray-{p}.png"
    paper(
        "Rabo Hypotheken",
        (230, 120, 20),
        "Your mortgage in 2026" if p == 0 else "Your mortgage in 2026 (continued)",
        [("Interest paid", "€ 4,812.00"), ("Repaid", "€ 3,600.00")] if p == 0 else [],
        500 + p,
        2 if p == 0 else 5,
    ).save(path)
    tray.add(path, DPI, 85)
archive.close()
for path in tmp.iterdir():
    path.unlink()
tmp.rmdir()
