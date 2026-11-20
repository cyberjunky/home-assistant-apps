"""Tests of the Scanner app. The scanner is tests/fake_scanimage.py, which prints what scanimage prints."""

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

from src import sane, share
from src.archive import Archive, clean_tags, file_name, fts_query
from src.config import Config, escl_url
from src.pages import Tray
from src.settings import Settings, clean
from src.setup import airscan_conf
from src.station import Station, resolution_of, uses_feeder
from src.webui import WebUI

FAKE = str(Path(__file__).resolve().parent / "fake_scanimage.py")

# As scanimage 1.4 prints it for SANE's test scanner and for a flatbed (shortened)
OPTIONS = """Output format is not set, using pnm as a default.

All options specific to device `genesys:libusb:001:004':
  Scan Mode:
    --mode Color|Gray|Lineart [Gray]
        Selects the scan mode (e.g., lineart, monochrome, or color).
    --source Flatbed|Transparency Adapter [inactive]
        Selects the scan source (such as a document-feeder).
    --preview[=(yes|no)] [no]
        Request a preview-quality scan.
    --resolution 4800|2400|1200|600|300|150|75dpi [75]
        Sets the resolution of the scanned image.
    --auto-res auto||50..1200dpi [300]
        A resolution the scanner may choose.
    --film auto|Positive|Negative [Positive]
  Geometry:
    -l 0..216.07mm [0]
        Top-left x position of scan area.
    -t 0..299mm [0]
    -x 0..216.07mm [216.07]
        Width of scan-area.
    -y 0..299mm [299]
  Enhancement:
    --custom-gamma[=(auto|yes|no)] [no] [advanced]
        Determines whether a builtin or a custom gamma-table should be
        used.
    --gamma-table 0..65535,... (in steps of 1) [inactive]
    --brightness -100..100% (in steps of 1) [0]
    --speed 1.5..12.25 [2.5]
    --levels -32.7|12.1|42|129.5 [42]
    --count <int> [7] [read-only]
    --name <string> [My [old] scanner]
    --profile Standard|Photo [fine]|Text [Photo [fine]]
  Buttons:
    --scan[=(yes|no)] [no] [hardware]
    --clear-calibration
        Clear calibration cache
"""


def option(groups: list[dict], name: str) -> dict:
    found = sane.find_option(groups, name)
    assert found is not None, name
    return found


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(sane, "SCANIMAGE", FAKE)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)


@pytest.fixture
def station(tmp_path, fake):
    settings = Settings(tmp_path / "settings.json")
    tray = Tray(tmp_path / "tray")
    archive = Archive(tmp_path / "archive")
    config = Config(driverless_usb=False)
    yield Station(config, settings, tray, archive, tmp_path / "work")
    archive.close()


def text_page(path: Path, text: str) -> None:
    image = Image.new("L", (1000, 400), 255)
    ImageDraw.Draw(image).text((60, 140), text, fill=0, font=ImageFont.load_default(size=72))
    image.save(path)


# ---------------------------------------------------------------------
# What scanimage prints
# ---------------------------------------------------------------------
def test_devices():
    devices = sane.parse_devices(
        "test:0|Noname|frontend-tester|virtual device\npixma:04A9176D|CANON|LiDE 110|flatbed scanner\n\n"
    )
    assert [d["id"] for d in devices] == ["test:0", "pixma:04A9176D"]
    assert devices[0]["label"] == "Test scanner 0" and devices[0]["backend"] == "test"
    assert devices[1]["label"] == "CANON LiDE 110" and devices[1]["type"] == "flatbed scanner"
    assert sane.parse_devices("") == []


def test_options_are_read():
    groups = sane.parse_options(OPTIONS)
    assert [g["title"] for g in groups] == ["Scan Mode", "Geometry", "Enhancement", "Buttons"]
    mode = option(groups, "mode")
    assert mode["type"] == "choice" and mode["choices"] == ["Color", "Gray", "Lineart"] and mode["value"] == "Gray"
    assert "lineart" in mode["description"]
    source = option(groups, "source")
    assert not source["active"] and source["value"] is None and source["choices"] == ["Flatbed", "Transparency Adapter"]
    preview = option(groups, "preview")
    assert preview["type"] == "bool" and preview["value"] == "no" and not preview["auto"]
    resolution = option(groups, "resolution")
    assert resolution["choices"][-1] == "75" and resolution["unit"] == "dpi" and resolution["numeric"]
    # "auto" before a range or a list, with one or two bars
    auto = option(groups, "auto-res")
    assert auto["auto"] and auto["type"] == "number" and (auto["min"], auto["max"], auto["unit"]) == (50, 1200, "dpi")
    assert auto["value"] == "300"
    film = option(groups, "film")
    assert film["auto"] and film["choices"] == ["Positive", "Negative"]
    # The area, with scanimage's one-letter names
    x = option(groups, "x")
    assert (
        x["flag"] == "-x"
        and x["title"] == "Width"
        and x["max"] == 216.07
        and x["value"] == "216.07"
        and x["unit"] == "mm"
    )
    gamma = option(groups, "custom-gamma")
    assert gamma["type"] == "bool" and gamma["auto"] and gamma["advanced"] and gamma["value"] == "no"
    assert "custom gamma-table should be used." in gamma["description"]
    table = option(groups, "gamma-table")
    assert table["array"] and not table["active"] and table["step"] == 1
    brightness = option(groups, "brightness")
    assert (brightness["min"], brightness["max"], brightness["step"], brightness["unit"]) == (-100, 100, 1, "%")
    assert option(groups, "speed")["max"] == 12.25 and "step" not in option(groups, "speed")
    # Numbers with a point are not mistaken for a number with a unit
    levels = option(groups, "levels")
    assert levels["choices"] == ["-32.7", "12.1", "42", "129.5"] and levels["unit"] == "" and levels["value"] == "42"
    count = option(groups, "count")
    assert count["type"] == "int" and count["value"] == "7" and count["readonly"]
    # Brackets inside a value
    assert option(groups, "name")["value"] == "My [old] scanner"
    profile = option(groups, "profile")
    assert profile["choices"] == ["Standard", "Photo [fine]", "Text"] and profile["value"] == "Photo [fine]"
    assert option(groups, "scan")["readonly"] and option(groups, "scan")["value"] == "no"
    assert option(groups, "clear-calibration")["type"] == "button"
    assert sane.parse_options("scanimage: open of device x failed: Invalid argument") == []


def test_arguments_and_what_can_be_set():
    groups = sane.parse_options(OPTIONS)
    values = {
        "x": "100",
        "mode": "Color",
        "source": "Flatbed",
        "count": "3",
        "gamma-table": "1",
        "nope": "1",
        "l": "-5",
    }
    chosen = sane.settable(groups, values)
    # Not: switched off (source), read-only (count), a list (gamma-table), unknown (nope)
    assert chosen == {"mode": "Color", "l": "-5", "x": "100"}
    # In the scanner's order: left before width, long names with =, the area with its letter
    assert sane.option_args(chosen, groups) == ["--mode=Color", "-l", "-5", "-x", "100"]
    assert sane.bed_size(groups) == (216.07, 299, "mm")
    preview = sane.preview_values(groups, {"mode": "Color", "resolution": "1200", "l": "50", "x": "20"})
    assert preview == {"mode": "Color", "resolution": "75", "l": "0", "t": "0", "x": "216.07", "y": "299"}
    assert resolution_of(groups) == 75 and not uses_feeder(groups)


def test_plain_words():
    assert sane.friendly("scanimage: sane_read: Document feeder jammed") == "The paper is jammed in the feeder"
    assert sane.friendly("scanimage: sane_start: Document feeder out of documents") == "The feeder is empty"
    assert sane.friendly("scanimage: open of device x failed: Device busy").startswith("The scanner is busy")
    assert sane.friendly("scanimage: something new") == "Something new"


# ---------------------------------------------------------------------
# Asking the scanner
# ---------------------------------------------------------------------
async def test_options_with_values(fake):
    assert [d["id"] for d in await sane.list_devices()] == ["fake:0"]
    result = await sane.read_options(
        "fake:0", {"mode": "Lineart", "threshold": "30", "source": "Automatic Document Feeder"}
    )
    assert result["applied"] == {"mode": "Lineart", "source": "Automatic Document Feeder", "threshold": "30"}
    assert option(result["groups"], "threshold")["value"] == "30"
    # The feeder has another paper size
    assert sane.bed_size(result["groups"]) == (210, 356, "mm") and uses_feeder(result["groups"])
    # A value for an option that is off is left for when it is on
    result = await sane.read_options("fake:0", {"threshold": "30"})
    assert result["applied"] == {} and not option(result["groups"], "threshold")["active"]
    # A value the scanner refuses is dropped, the rest stays
    result = await sane.read_options("fake:0", {"mode": "Purple", "resolution": "300"})
    assert result["applied"] == {"resolution": "300"} and "mode" in result["dropped"]
    assert option(result["groups"], "mode")["value"] == "Gray"
    # A value the scanner rounds
    result = await sane.read_options("fake:0", {"resolution": "5000"})
    assert option(result["groups"], "resolution")["value"] == "600" and result["notes"]
    with pytest.raises(sane.ScanError):
        await sane.read_options("other:1", {})


async def test_scan_one_page(station):
    station.start_scan("fake:0", {"mode": "Color", "resolution": "150", "x": "100", "y": "50"}, {"mode": "single"})
    await station.wait()
    assert station.job["state"] == "done" and station.job["pages"] == 1 and not station.job["error"]
    (page,) = station.tray.pages
    assert page["dpi"] == 150 and page["mode"] == "Colour" and page["file"].endswith(".jpg")
    assert station.tray.path(page).is_file() and station.tray.thumb(page).is_file()
    # What was used is the start next time
    assert station.settings["device"] == "fake:0" and station.settings["values"]["fake:0"]["mode"] == "Color"
    # Nothing of the scanner's own files is left
    assert not list(station.work.glob("scan-*"))


async def test_feeder_batch(station):
    values = {"source": "Automatic Document Feeder", "mode": "Lineart"}
    station.start_scan("fake:0", values, {"mode": "feeder", "count": 0})
    await station.wait()
    assert station.job["state"] == "done" and station.job["pages"] == 3, station.job
    assert [p["mode"] for p in station.tray.pages] == ["Black and white"] * 3
    assert all(p["file"].endswith(".png") for p in station.tray.pages)
    # With a number of pages: no more than that
    station.start_scan("fake:0", values, {"mode": "feeder", "count": 2})
    await station.wait()
    assert station.job["pages"] == 2 and len(station.tray.pages) == 5


async def test_empty_feeder_and_glass_as_feeder(station, monkeypatch):
    monkeypatch.setenv("FAKE_FEEDER", "0")
    station.start_scan("fake:0", {"source": "Automatic Document Feeder"}, {"mode": "feeder", "count": 0})
    await station.wait()
    assert station.job["state"] == "failed" and station.job["error"] == "The feeder is empty"
    # The glass never runs empty: a batch from it needs a number of pages
    station.start_scan("fake:0", {}, {"mode": "feeder", "count": 0})
    await station.wait()
    assert station.job["state"] == "failed" and "feeder" in station.job["error"] and not station.tray.pages
    station.start_scan("fake:0", {}, {"mode": "feeder", "count": 2})
    await station.wait()
    assert station.job["state"] == "done" and len(station.tray.pages) == 2


async def test_failed_scan_keeps_no_half_page(station):
    station.start_scan("fake:0", {"mode": "Jam"}, {"mode": "single"})
    await station.wait()
    assert station.job["state"] == "failed" and station.job["error"] == "The paper is jammed in the feeder"
    assert not station.tray.pages


async def test_timed_batch_and_stop(station):
    station.start_scan("fake:0", {}, {"mode": "timed", "count": 2, "interval": 2})
    with pytest.raises(Exception, match="busy"):
        station.start_scan("fake:0", {}, {"mode": "single"})
    await station.wait()
    assert station.job["state"] == "done" and station.job["pages"] == 2
    # Stop while waiting for the next page ends the batch with what there is
    station.start_scan("fake:0", {}, {"mode": "timed", "count": 0, "interval": 30})
    while not station.job.get("countdown"):
        await asyncio.sleep(0.05)
    await station.cancel()
    await station.wait()
    assert station.job["state"] == "done" and not station.job["error"] and len(station.tray.pages) == 3


async def test_cancel_a_scan(station, monkeypatch):
    monkeypatch.setenv("FAKE_DELAY", "2")
    station.start_scan("fake:0", {}, {"mode": "single"})
    while station.job["percent"] == 0:
        await asyncio.sleep(0.05)
    await station.cancel()
    await station.wait()
    assert station.job["state"] == "done" and station.job.get("cancelled") and not station.tray.pages


async def test_preview(station):
    station.start_preview("fake:0", {"mode": "Color", "resolution": "600", "l": "100", "x": "50"})
    await station.wait()
    assert station.job["state"] == "done", station.job
    preview = station.preview
    # The whole glass, at the lowest resolution, whatever area was chosen
    assert preview["bed"] == (216, 297, "mm") and (station.work / "preview.jpg").is_file()
    assert abs(preview["width"] / preview["height"] - 216 / 297) < 0.05
    assert not station.tray.pages


# ---------------------------------------------------------------------
# The pages
# ---------------------------------------------------------------------
def make_scan(path: Path, size=(60, 80), colour=(10, 120, 200)) -> Path:
    Image.new("RGB", size, colour).save(path, format="PPM")
    return path


def test_tray(tmp_path):
    tray = Tray(tmp_path / "tray")
    ids = [tray.add(make_scan(tmp_path / f"{n}.pnm"), 300, 85)["id"] for n in range(6)]
    assert len(set(ids)) == 6 and not (tmp_path / "0.pnm").exists()
    turned = tray.rotate(ids[0], 90, 85)
    assert (turned["width"], turned["height"]) == (80, 60) and turned["turned"] == 1
    with Image.open(tray.path(turned)) as image:
        assert image.size == (80, 60) and image.info["dpi"] == (300, 300)
    # Fronts 1 3 5, then the pile turned over: backs 6 4 2
    tray.order([ids[0], ids[2], ids[4], ids[5], ids[3], ids[1]])
    assert tray.interleave() and [p["id"] for p in tray.pages] == ids
    tray.reverse()
    assert [p["id"] for p in tray.pages] == ids[::-1]
    tray.order([ids[3]])
    assert tray.pages[0]["id"] == ids[3] and len(tray.pages) == 6
    assert tray.delete(ids[3]) and not tray.delete("nope")
    assert not tray.interleave()  # five pages
    # A restart finds them again
    again = Tray(tmp_path / "tray")
    assert [p["id"] for p in again.pages] == [p["id"] for p in tray.pages]
    again.clear()
    assert not again.pages and not list((tmp_path / "tray").glob("*.jpg"))


def test_sixteen_bit_and_black_and_white(tmp_path):
    tray = Tray(tmp_path / "tray")
    Image.new("I;16", (20, 30), 40000).save(tmp_path / "deep.png")
    page = tray.add(tmp_path / "deep.png", 150, 85)
    assert page["mode"] == "Grey"
    with Image.open(tray.path(page)) as image:
        assert image.mode == "L" and 150 < image.getpixel((5, 5)) < 160
    Image.new("1", (20, 30), 1).save(tmp_path / "bw.pbm")
    page = tray.add(tmp_path / "bw.pbm", 300, 85)
    assert page["mode"] == "Black and white" and page["file"].endswith(".png")


# ---------------------------------------------------------------------
# The archive
# ---------------------------------------------------------------------
def test_archive(tmp_path):
    tray = Tray(tmp_path / "tray")
    pages = [tray.add(make_scan(tmp_path / f"{n}.pnm", (248, 351)), 30, 85) for n in range(3)]
    archive = Archive(tmp_path / "archive")
    seen = []
    doc = archive.add(
        pages,
        [tray.path(p) for p in pages],
        "  Invoice  Acme/2026 ",
        ["Tax", "tax", "2026"],
        None,
        "fake:0",
        lambda n, t: seen.append((n, t)),
    )
    assert doc["name"] == "Invoice Acme/2026" and doc["tags"] == ["tax", "2026"] and doc["pages"] == 3
    assert doc["file_name"] == "Invoice Acme 2026.pdf" and not doc["ocr"] and seen == [(1, 3), (2, 3), (3, 3)]
    reader = PdfReader(archive.pdf(doc["id"]))
    assert len(reader.pages) == 3 and reader.metadata.title == "Invoice Acme/2026"
    # A4 at 30 dpi: the page has the paper's size
    box = reader.pages[0].mediabox
    assert abs(float(box.width) - 595) < 2 and abs(float(box.height) - 842) < 2
    assert archive.thumb(doc["id"]).is_file()

    other = archive.add(pages[:1], [tray.path(pages[0])], "Letter from the bank", [], None)
    assert archive.search()["total"] == 2
    assert [d["id"] for d in archive.search("invoice")["documents"]] == [doc["id"]]
    assert [d["id"] for d in archive.search("ban")["documents"]] == [other["id"]]  # the beginning of a word
    assert [d["id"] for d in archive.search("", tag="tax")["documents"]] == [doc["id"]]
    assert archive.search("tax")["total"] == 1 and archive.search("nothing")["total"] == 0
    assert archive.search('"; DROP')["total"] == 0
    assert [d["id"] for d in archive.search(sort="name")["documents"]] == [doc["id"], other["id"]]
    assert archive.tags() == [{"tag": "2026", "count": 1}, {"tag": "tax", "count": 1}]

    changed = archive.update(other["id"], name="Bank statement", tags="Bank, 2026")
    assert changed["name"] == "Bank statement" and changed["tags"] == ["bank", "2026"]
    assert archive.search("letter")["total"] == 0 and archive.search("statement")["total"] == 1
    archive.set_copied(doc["id"], "", "No route")
    assert archive.get(doc["id"])["copy_error"] == "No route" and not archive.get(doc["id"])["copied_to"]
    archive.set_copied(doc["id"], "/share/x.pdf")
    assert archive.get(doc["id"])["copied_to"] == "/share/x.pdf" and not archive.get(doc["id"])["copy_error"]
    assert archive.summary()["documents"] == 2 and archive.summary()["pages"] == 4
    assert archive.delete(doc["id"]) and not archive.pdf(doc["id"]).exists() and archive.search("invoice")["total"] == 0
    with pytest.raises(ValueError):
        archive.add([], [], "Empty", [], None)
    archive.close()


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs Tesseract")
def test_text_is_recognised_and_the_picture_kept(tmp_path):
    text_page(tmp_path / "page.png", "INVOICE 12345")
    tray = Tray(tmp_path / "tray")
    page = tray.add(tmp_path / "page.png", 300, 85)
    picture = tray.path(page).read_bytes()
    archive = Archive(tmp_path / "archive")
    doc = archive.add([page], [tray.path(page)], "With text", [], ["eng"])
    assert doc["ocr"] and doc["has_text"] and doc["languages"] == "eng"
    assert "INVOICE" in archive.get(doc["id"], with_text=True)["text"]
    found = archive.search("invoice 123")["documents"]
    assert [d["id"] for d in found] == [doc["id"]] and "\x02" in found[0]["snippet"]
    reader = PdfReader(archive.pdf(doc["id"]))
    # The text is in the PDF itself, and the picture is the scanned one, not compressed again
    assert "INVOICE" in reader.pages[0].extract_text()
    objects = [o.get_object() for o in reader.pages[0]["/Resources"]["/XObject"].values()]
    assert [o._data for o in objects if o.get("/Subtype") == "/Image"] == [picture]
    archive.close()


def test_names_and_tags():
    assert clean_tags("Tax, tax ,  Bank  statements,") == ["tax", "bank statements"]
    assert file_name('a/b\\c:d*e?"f<g>h|i') == "a b c d e f g h i" and file_name(" .. ") == "Scan"
    assert fts_query('Bank "2026') == '"bank"* "2026"*' and fts_query("  ") == ""


# ---------------------------------------------------------------------
# Copies to a folder
# ---------------------------------------------------------------------
def test_copy_to_a_folder(tmp_path):
    root = tmp_path / "share"
    (root / "nas").mkdir(parents=True)
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"%PDF-1.5 test")
    roots = (root,)
    values = share.tokens(1795184759, "Invoice: Acme/2026", 7, 3)
    assert share.fill("Scan {date} {time} {nope}", values).startswith("Scan 2026-11-") and "{nope}" in share.fill(
        "{nope}", values
    )
    relative = share.relative_path("{year}/{date} {name}", values)
    assert relative.parts[0] == "2026" and relative.name.endswith(" Invoice Acme 2026.pdf")
    assert share.relative_path("../../{name}", values) == Path("Invoice Acme 2026.pdf")
    assert share.relative_path("", values) == Path("Invoice Acme 2026.pdf")

    first = share.copy_document(source, str(root / "nas"), relative, roots)
    assert Path(first).read_bytes() == source.read_bytes() and Path(first).parent.name == "2026"
    # Never over an existing file
    second = share.copy_document(source, str(root / "nas"), relative, roots)
    assert second != first and second.endswith(" (2).pdf")
    assert not list((root / "nas" / "2026").glob(".*"))
    with pytest.raises(share.ShareError, match="isn't in"):
        share.copy_document(source, str(tmp_path), relative, roots)
    with pytest.raises(share.ShareError, match="isn't there"):
        share.copy_document(source, str(root / "gone"), relative, roots)
    with pytest.raises(share.ShareError, match="No folder"):
        share.copy_document(source, "", relative, roots)

    assert share.inside(root / "nas" / ".." / ".." / "etc", roots) is None
    (root / "out").symlink_to(tmp_path)
    assert share.inside(root / "out" / "x", roots) is None
    listing = share.list_folder("", roots)
    assert [f["path"] for f in listing["folders"]] == [str(root)] and listing["parent"] is None
    listing = share.list_folder(str(root), roots)
    assert [f["name"] for f in listing["folders"]] == ["nas", "out"] and listing["parent"] == "" and listing["writable"]
    assert share.list_folder(str(root / "nas"), roots)["parent"] == str(root)
    with pytest.raises(share.ShareError):
        share.list_folder("/etc", roots)
    assert share.make_folder(str(root / "nas"), "Scans: new", roots) == str(root / "nas" / "Scans new")
    with pytest.raises(share.ShareError):
        share.make_folder("/etc", "x", roots)
    mounts = {"/share/nas": "cifs", "/media/films": "nfs4", "/": "overlay"}
    assert share.kind_of(Path("/share/nas/scans/2026"), mounts) == "smb"
    assert share.kind_of(Path("/media/films"), mounts) == "nfs" and share.kind_of(Path("/share/local"), mounts) == ""


# ---------------------------------------------------------------------
# Settings and options
# ---------------------------------------------------------------------
def test_settings(tmp_path):
    settings = Settings(tmp_path / "settings.json")
    assert settings["jpeg_quality"] == 85 and settings["ocr_languages"] == ["eng"]
    settings.update(
        {"jpeg_quality": 500, "ocr_languages": ["nld", "xx"], "share_auto": 1, "name_pattern": "  ", "nope": 1}
    )
    assert settings["jpeg_quality"] == 100 and settings["ocr_languages"] == ["nld"] and settings["share_auto"] is True
    assert settings["name_pattern"] == "Scan {date} {time}" and "nope" not in settings.data
    settings.remember("fake:0", {"mode": "Color", "bad name!": "x"}, {"mode": "timed", "interval": 1, "count": "7"})
    assert settings["values"] == {"fake:0": {"mode": "Color"}}
    assert settings["batch"] == {"mode": "timed", "count": 7, "interval": 2}
    settings.save_profile("Letters", "fake:0", {"mode": "Gray"}, {"mode": "feeder"})
    settings.save_profile("Letters", "fake:0", {"mode": "Lineart"}, {"mode": "feeder"})
    again = Settings(tmp_path / "settings.json")
    assert [(p["name"], p["values"]) for p in again["profiles"]] == [("Letters", {"mode": "Lineart"})]
    assert again["values"] == {"fake:0": {"mode": "Color"}}
    assert again.delete_profile("Letters")["profiles"] == []
    (tmp_path / "broken.json").write_text("{")
    assert Settings(tmp_path / "broken.json")["jpeg_quality"] == 85
    assert clean({"batch": "x", "profiles": [3, {"name": ""}]})["profiles"] == []


def test_options_of_the_app(monkeypatch):
    monkeypatch.delenv("SCANNER_TEST", raising=False)
    assert escl_url("192.168.1.50") == "http://192.168.1.50/eSCL"
    assert escl_url("printer.local:8080/") == "http://printer.local:8080/eSCL"
    assert escl_url("https://10.0.0.2/eSCL") == "https://10.0.0.2/eSCL"
    config = Config.from_options(
        {"network_scanners": [{"name": 'Hall "printer"', "address": "10.0.0.2"}], "test_scanner": True}
    )
    # The test scanner is not an option: an old setting for it does nothing
    assert config.network_scanners == (("Hall  printer", "http://10.0.0.2/eSCL"),) and not config.test_scanner
    monkeypatch.setenv("SCANNER_TEST", "1")
    assert Config.from_options({}).test_scanner
    assert config.driverless_usb and Config.from_options({}).network_scanners == ()
    with pytest.raises(ValueError):
        Config.from_options({"network_scanners": [{"name": "x", "address": "10.0.0.2, wsd"}]})
    conf = airscan_conf([("Hall", "http://10.0.0.2/eSCL"), ("Hall", "http://127.0.0.1:60000/eSCL")])
    assert '"Hall" = http://10.0.0.2/eSCL, eSCL' in conf and '"Hall (2)" = http://127.0.0.1:60000/eSCL, eSCL' in conf
    assert "discovery = disable" in conf


# ---------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------
@pytest.fixture
async def client(station, tmp_path, monkeypatch):
    root = tmp_path / "share"
    root.mkdir()
    monkeypatch.setattr("src.webui.DESTINATION_ROOTS", (root,))
    monkeypatch.setattr("src.station.DESTINATION_ROOTS", (root,))
    monkeypatch.setattr("src.webui.DATA_DIR", tmp_path)
    webui = WebUI(0, station.config, station.settings, station.tray, station.archive, station)
    async with TestClient(TestServer(webui.app)) as c:
        c.station = station
        c.root = root
        yield c


async def post(client, path: str, body: dict, status: int = 200) -> dict:
    response = await client.post(path, json=body)
    assert response.status == status, await response.text()
    return await response.json()


async def test_api_from_scan_to_share(client):
    station = client.station
    page = await (await client.get("/")).text()
    assert "__VERSION__" not in page and "static/scan.js" in page
    assert (await (await client.get("/api/status")).json())["job"]["state"] == "idle"
    # Changes are JSON only: a form from another website can't start a scan
    assert (await client.post("/api/scan", data={"device": "fake:0"})).status == 415
    assert (await client.post("/api/scan", json=["fake:0"])).status == 400
    assert (await client.post("/api/scan", json={"device": "-L"})).status == 400

    devices = await post(client, "/api/devices/search", {})
    assert [d["id"] for d in devices["devices"]] == ["fake:0"]
    options = await post(
        client, "/api/options", {"device": "fake:0", "values": {"source": "Automatic Document Feeder"}}
    )
    assert options["feeder"] and options["bed"] == [210, 356, "mm"] and options["resolution"] == 75
    assert (await client.post("/api/options", json={"device": "other:9", "values": {}})).status == 502

    await post(
        client,
        "/api/scan",
        {"device": "fake:0", "values": {"source": "Automatic Document Feeder"}, "batch": {"mode": "feeder"}},
    )
    assert (await client.post("/api/scan", json={"device": "fake:0", "values": {}})).status == 409
    await station.wait()
    tray = await (await client.get("/api/tray")).json()
    ids = [p["id"] for p in tray["pages"]]
    assert len(ids) == 3 and tray["suggested_name"].startswith("Scan 20")
    assert (await client.get(f"/api/tray/{ids[0]}/thumb")).status == 200
    assert (await client.get(f"/api/tray/{ids[0]}/image")).content_type == "image/jpeg"
    assert (await client.get("/api/tray/nope/thumb")).status == 404
    tray = await post(client, "/api/tray/rotate", {"id": ids[0], "degrees": 90})
    assert tray["pages"][0]["width"] > tray["pages"][0]["height"]
    await post(client, "/api/tray/rotate", {"id": ids[0], "degrees": 45}, 400)
    tray = await post(client, "/api/tray/order", {"ids": ids[::-1]})
    assert [p["id"] for p in tray["pages"]] == ids[::-1]
    await post(client, "/api/tray/arrange", {"how": "interleave"}, 400)  # three pages
    tray = await post(client, "/api/tray/delete", {"id": ids[1]})
    assert len(tray["pages"]) == 2

    # Every saved document goes to the chosen folder
    (client.root / "nas").mkdir()
    await post(client, "/api/settings", {"share_folder": "/etc"}, 400)
    answer = await post(
        client,
        "/api/settings",
        {"share_folder": str(client.root / "nas"), "share_auto": True, "share_pattern": "{year}/{name}"},
    )
    assert answer["settings"]["share_auto"] and "eng" in answer["languages"]
    await post(client, "/api/tray/save", {"name": "Tax return", "tags": ["Tax"], "ocr": False, "device": "fake:0"})
    await station.wait()
    job = (await (await client.get("/api/status")).json())["job"]
    assert job["state"] == "done" and job["doc"]["pages"] == 2 and not job.get("warning"), job
    doc = job["doc"]
    assert Path(doc["copied_to"]).is_file() and Path(doc["copied_to"]).name == "Tax return.pdf"
    assert (await (await client.get("/api/tray")).json())["pages"] == []
    await post(client, "/api/tray/save", {"name": "Nothing"}, 400)

    listing = await (await client.get("/api/documents?q=tax")).json()
    assert listing["total"] == 1 and listing["tags"] == [{"tag": "tax", "count": 1}]
    response = await client.get(f"/api/documents/{doc['id']}/file?download=1")
    assert response.content_type == "application/pdf" and (await response.read()).startswith(b"%PDF")
    assert (
        "attachment" in response.headers["Content-Disposition"]
        and "Tax%20return.pdf" in response.headers["Content-Disposition"]
    )
    assert "inline" in (await client.get(f"/api/documents/{doc['id']}/file")).headers["Content-Disposition"]
    assert (await client.get(f"/api/documents/{doc['id']}/thumb")).content_type == "image/jpeg"
    renamed = await post(client, f"/api/documents/{doc['id']}", {"name": "Tax return 2026", "tags": "tax, done"})
    assert renamed["name"] == "Tax return 2026" and renamed["tags"] == ["tax", "done"]
    again = await post(client, f"/api/documents/{doc['id']}/copy", {})
    assert again["copied_to"].endswith("Tax return 2026.pdf")
    # The share is gone: the document stays, and says why it wasn't copied
    shutil.rmtree(client.root / "nas")
    failed = await post(client, f"/api/documents/{doc['id']}/copy", {}, 502)
    assert "isn't there" in failed["error"]
    assert "isn't there" in (await (await client.get(f"/api/documents/{doc['id']}")).json())["copy_error"]
    await post(client, f"/api/documents/{doc['id']}/delete", {})
    assert (await client.get(f"/api/documents/{doc['id']}/file")).status == 404


async def test_api_save_warns_when_the_share_is_gone(client):
    station = client.station
    station.settings.update({"share_folder": str(client.root / "gone"), "share_auto": True})
    await post(client, "/api/scan", {"device": "fake:0", "values": {}, "batch": {"mode": "single"}})
    await station.wait()
    await post(client, "/api/tray/save", {"name": "Kept", "ocr": False})
    await station.wait()
    job = station.job
    assert job["state"] == "done" and "not copied" in job["warning"] and job["doc"]["copy_error"]
    assert station.archive.pdf(job["doc"]["id"]).is_file()
    status = await (await client.get("/api/status")).json()
    assert not status["share"]["ok"] and status["share"]["problem"] == "The folder isn't there"


async def test_api_profiles_folders_and_ingress(client, monkeypatch):
    answer = await post(
        client,
        "/api/profiles",
        {"name": " Letters ", "device": "fake:0", "values": {"mode": "Gray"}, "batch": {"mode": "feeder"}},
    )
    assert [(p["name"], p["batch"]["mode"]) for p in answer["settings"]["profiles"]] == [("Letters", "feeder")]
    await post(client, "/api/profiles", {"name": "", "device": "fake:0"}, 400)
    assert (await post(client, "/api/profiles/delete", {"name": "Letters"}))["settings"]["profiles"] == []
    made = await post(client, "/api/folders", {"path": str(client.root), "name": "scans"})
    assert Path(made["path"]).is_dir()
    listing = await (await client.get(f"/api/folders?path={client.root}")).json()
    assert [f["name"] for f in listing["folders"]] == ["scans"]
    assert (await client.get("/api/folders?path=/etc")).status == 400
    # Under Home Assistant, changes only come through its ingress
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    assert (await client.post("/api/tray/delete", json={"all": True})).status == 403
    assert (await client.get("/api/status")).status == 200
    assert json.loads(await (await client.get("/api/settings")).text())["roots"] == [str(client.root)]


async def test_step_by_step_waits_for_next_page(station):
    station.start_scan("fake:0", {}, {"mode": "step", "count": 0})
    for expected in (1, 2, 3):
        while not station.job.get("waiting"):
            await asyncio.sleep(0.02)
        # It stays waiting, however long, with the pages so far
        await asyncio.sleep(0.3)
        assert station.busy and station.job["pages"] == expected and len(station.tray.pages) == expected
        if expected < 3:
            assert station.next_page()
            while station.job.get("waiting"):
                await asyncio.sleep(0.02)
    await station.cancel()
    await station.wait()
    assert station.job["state"] == "done" and not station.job["error"] and len(station.tray.pages) == 3
    assert not station.next_page()
    # With a number of pages it ends by itself after the last one
    station.start_scan("fake:0", {}, {"mode": "step", "count": 2})
    while not station.job.get("waiting"):
        await asyncio.sleep(0.02)
    station.next_page()
    await station.wait()
    assert station.job["state"] == "done" and len(station.tray.pages) == 5


async def test_next_page_skips_the_timer(station, client):
    station.start_scan("fake:0", {}, {"mode": "timed", "count": 2, "interval": 60})
    assert (await client.post("/api/next", json={})).status == 409  # the first page is still being scanned
    while not station.job.get("countdown"):
        await asyncio.sleep(0.02)
    assert (await post(client, "/api/next", {}))["job"]["kind"] == "scan"
    await asyncio.wait_for(station.wait(), 10)
    assert station.job["state"] == "done" and len(station.tray.pages) == 2
    assert station.settings["batch"]["mode"] == "timed"
