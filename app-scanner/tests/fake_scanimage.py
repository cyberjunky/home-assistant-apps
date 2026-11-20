#!/usr/bin/env python3
"""A stand-in for scanimage in the tests: one scanner with a glass and a feeder of three pages.

It prints what the real one prints (see sane.py): the scanners for -f, the options for -A with
the given values applied, and for a scan the progress on stderr and the pages as PNM files.
"""

import os
import signal
import sys
import time

DEVICE = "fake:0"
args = sys.argv[1:]


def say(text: str) -> None:
    sys.stderr.write(f"scanimage: {text}\n")


def value(name: str, default: str) -> str:
    for i, a in enumerate(args):
        if a.startswith(f"--{name}="):
            return a.split("=", 1)[1]
        if a == f"-{name}" and len(name) == 1 and i + 1 < len(args):
            return args[i + 1]
    return default


if "-f" in args:
    print(f"{DEVICE}|Acme|Scan 3000|flatbed scanner")
    sys.exit(0)

if value("d", "") != DEVICE and "-d" in args:
    say(f"open of device {value('d', '')} failed: Invalid argument")
    sys.exit(0)

mode = value("mode", "Gray")
source = value("source", "Flatbed")
resolution = value("resolution", "75")
left, top = float(value("l", "0")), float(value("t", "0"))
bed = (216.0, 297.0) if source == "Flatbed" else (210.0, 356.0)
width = min(float(value("x", str(bed[0]))), bed[0] - left)
height = min(float(value("y", str(bed[1]))), bed[1] - top)

known = ("mode", "source", "resolution", "threshold", "brightness", "format", "batch", "batch-count", "batch-print")
for a in args:
    if a.startswith("--") and a[2:].split("=")[0] not in known:
        sys.stderr.write(f"scanimage: unrecognized option '{a}'\n")
        sys.exit(1)
if mode not in ("Lineart", "Gray", "Color", "Jam"):
    say("setting of option --mode failed (Invalid argument)")
    sys.exit(0)
if value("threshold", "") and mode != "Lineart":
    say("attempted to set inactive option threshold")
    sys.exit(0)
if resolution not in ("75", "150", "300", "600"):
    say(f"rounded value of resolution from {resolution} to 600")
    resolution = "600"

if "-A" in args:
    threshold = f"[{value('threshold', '50')}]" if mode == "Lineart" else "[inactive]"
    print(f"""
All options specific to device `{DEVICE}':
  Scan Mode:
    --mode Lineart|Gray|Color [{mode}]
        Selects the scan mode (e.g., lineart, monochrome, or color).
    --source Flatbed|Automatic Document Feeder [{source}]
        Selects the scan source (such as a document-feeder).
    --resolution 75|150|300|600dpi [{resolution}]
        Sets the resolution of the scanned image.
  Geometry:
    -l 0..{bed[0]:g}mm [{left:g}]
        Top-left x position of scan area.
    -t 0..{bed[1]:g}mm [{top:g}]
        Top-left y position of scan area.
    -x 0..{bed[0] - left:g}mm [{width:g}]
        Width of scan-area.
    -y 0..{bed[1] - top:g}mm [{height:g}]
        Height of scan-area.
  Enhancement:
    --brightness -100..100% (in steps of 1) [{value("brightness", "0")}]
        Controls the brightness of the acquired image.
    --threshold 0..100% (in steps of 1) {threshold}
        Select minimum-brightness to get a white point
    --gamma-table 0..255,... [inactive]
        Gamma-correction table.
  Sensors:
    --scan[=(yes|no)] [no] [hardware]
        Scan button
""")
    sys.exit(0)

signal.signal(signal.SIGTERM, lambda *_: (say("sane_read: Operation was cancelled"), sys.exit(2)))
pixels = (max(1, int(width / 25.4 * int(resolution) / 20)), max(1, int(height / 25.4 * int(resolution) / 20)))


def page(path: str, number: int) -> None:
    sys.stderr.write(f"Scanning page {number}\n")
    for percent in (25.0, 60.0, 100.0):
        sys.stderr.write(f"Progress: {percent}%\r")
        sys.stderr.flush()
        time.sleep(float(os.environ.get("FAKE_DELAY", "0")))
    w, h = pixels
    with open(path, "wb") as f:
        if mode == "Color":
            f.write(f"P6\n{w} {h}\n255\n".encode() + bytes([200, 30 * number % 255, 90]) * (w * h))
        elif mode == "Lineart":
            f.write(f"P4\n{w} {h}\n".encode() + bytes([0xAA]) * ((w + 7) // 8 * h))
        else:
            f.write(f"P5\n{w} {h}\n255\n".encode() + bytes([40 * number % 255]) * (w * h))
    sys.stderr.write(f"\nScanned page {number}. (scanner status = 5)\n")


if mode == "Jam":
    sys.stderr.write("Progress: 30.0%\r\n")
    say("sane_read: Document feeder jammed")
    sys.exit(6)

batch = value("batch", "")
if batch:
    in_feeder = int(os.environ.get("FAKE_FEEDER", "3")) if source != "Flatbed" else 10**6
    count = int(value("batch-count", "0")) or 10**6
    number = 0
    while number < count:
        if number >= in_feeder:
            say("sane_start: Document feeder out of documents")
            break
        number += 1
        name = batch % number
        page(name, number)
        print(name, flush=True)
    sys.stderr.write(f"Batch terminated, {number} pages scanned\n")
    sys.exit(0 if number else 7)

page(value("o", "/dev/null"), 1)
