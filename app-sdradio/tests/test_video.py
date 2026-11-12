"""Video: analog FPV video decoded from FM, and the radio switching to 20 MS/s for it (HackRF only)."""

import numpy as np
import pytest

from src.config import Config
from src.database import Database
from src.decoders.video import Video, demodulate, frame
from src.radio import Radio
from tests import video_signal
from tests.simulator import SimulatedDevice

RATE = 20_000_000


@pytest.mark.parametrize(("standard", "inverted", "noise"), [("PAL", False, 0), ("NTSC", False, 0),
                                                              ("PAL", True, 0), ("PAL", False, 0.1)])  # fmt: skip
def test_frames(standard, inverted, noise):
    image = video_signal.test_picture()
    iq = video_signal.fm_iq(video_signal.composite(image, RATE, standard, fields=3), RATE, invert=inverted, noise=noise)
    result = frame(demodulate(iq[200_000:1_200_000]))
    assert result and result["standard"] == standard and result["locked"] and result["inverted"] == inverted
    want = image if standard == "PAL" else image[np.arange(240) * 288 // 240]
    assert np.abs(result["image"].astype(int) - want.astype(int)).mean() < (12 if noise else 6)


def test_no_video_in_noise():
    rng = np.random.default_rng(3)
    x = ((rng.standard_normal(1_000_000) + 1j * rng.standard_normal(1_000_000)) * 0.1).astype(np.complex64)
    assert frame(demodulate(x)) is None


def test_the_mode_saves_frames_and_snapshots(tmp_path):
    now = [0.0]
    stored, live = [], []
    decoder = Video({}, stored.append, live.append, clock=lambda: now[0])
    decoder.folder = tmp_path
    iq = video_signal.fm_iq(video_signal.composite(video_signal.test_picture(), RATE, fields=3), RATE)
    decoder.feed_iq(iq[:1_000_000], RATE, 0.0)
    decoder.feed_iq(iq[:1_000_000], RATE, 0.0)  # within the same quarter second: skipped
    assert decoder.frames == 1 and live[-1]["frame"]["standard"] == "PAL"
    assert (tmp_path / "live.png").read_bytes()[:4] == b"\x89PNG"
    item = decoder.snapshot()
    assert item and (tmp_path / item["file"]).is_file() and stored[0]["file"] == item["file"]


def make_radio(tmp_path, kind):
    config = Config()
    config.receiver.type = kind
    db = Database(tmp_path / "db.sqlite")
    events = []
    radio = Radio(
        config, db, lambda c: SimulatedDevice(c, realtime=False, signals=[]), lambda k, p: events.append((k, p))
    )
    radio.events = events
    radio._open()
    return radio


def run(radio, blocks):
    for _ in range(blocks):
        radio._handle_commands()
        radio._process(radio.device.read(radio.block_size))


def test_hackrf_only_and_the_rate_follows(tmp_path):
    radio = make_radio(tmp_path, "rtl-sdr")
    video = next(m for m in radio.snapshot()["modes"] if m["key"] == "video")
    assert video["why_not"].startswith("HackRF only")
    radio.command("decoder", key="video")
    run(radio, 1)
    assert radio.decoder is None and radio.rate == 2_400_000
    radio.db.close()

    radio = make_radio(tmp_path, "hackrf")
    assert next(m for m in radio.snapshot()["modes"] if m["key"] == "video")["why_not"] is None
    radio.command("decoder", key="video")
    run(radio, 3)
    assert radio.decoder.key == "video" and radio.rate == RATE and radio.block_size == RATE // 20
    assert radio.frequency == 5_800_000_000 and radio.center == 5_800_000_000
    radio.command("decoder", key="radio")
    run(radio, 2)
    assert radio.rate == 2_400_000 and radio.block_size == 120_000
    radio.db.close()
