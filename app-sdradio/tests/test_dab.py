"""DAB+: the blocks, welle-cli's ensemble read, and (in the app image, with a recording) welle-cli
decoding SDRadio's samples. The recording is not in the repository: SDRADIO_DAB_FILE points to one
(8-bit IQ at 2.048 MS/s, as the raw IQ data of the DAB-Radio project)."""

import json
import os
import shutil
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np
import pytest

from src.decoders.dab import BLOCKS, Dab, block_of, summary
from src.errors import ValidationError

MUX = json.loads((Path(__file__).parent / "data" / "dab_mux.json").read_text())


def test_blocks():
    assert block_of(227_360_000) == "12C" and block_of(222_064_000) == "11D" and block_of(100_000_000) is None
    assert len(BLOCKS) == 38
    assert Dab.validate({"frequency": 220_352_000})["frequency"] == 220_352_000
    with pytest.raises(ValidationError):
        Dab.validate({"frequency": 221_000_000})


def test_the_ensemble_read():
    """welle-cli's mux.json (from a recording of the Bavarian ensemble on 11D)."""
    s = summary(MUX)
    assert s["ensemble"] == "Bayern" and s["snr"] == 18.0
    # The EPG data service has nothing to hear: left out
    assert [x["label"] for x in s["services"]] == ["BAYERN 3", "BR-KLASSIK"]
    b3 = s["services"][0]
    assert b3["sid"] == "0xd313" and b3["pty"] == "Pop Music" and b3["kind"] == "DAB+" and b3["bitrate"] == 96


DAB_FILE = os.environ.get("SDRADIO_DAB_FILE")


@pytest.mark.skipif(not shutil.which("welle-cli") or not DAB_FILE, reason="needs welle-cli and SDRADIO_DAB_FILE")
def test_welle_cli_decodes_the_radio_samples():
    raw = np.fromfile(DAB_FILE, dtype=np.uint8, count=2_048_000 * 2 * 15)
    iq = ((raw[0::2].astype(np.float32) - 127.5) + 1j * (raw[1::2].astype(np.float32) - 127.5)) / 128
    stored, live = [], []
    decoder = Dab({"frequency": 222_064_000}, stored.append, live.append)
    block = 2_048_000 // 20
    stop = threading.Event()

    def feed():
        # At the receiver's pace, round and round, as long as the test listens
        start, k = time.monotonic(), 0
        while not stop.is_set():
            i = (k * block) % (len(iq) - block)
            decoder.feed_iq(iq[i : i + block].astype(np.complex64), 2_048_000, 0.0)
            k += 1
            time.sleep(max(0.0, start + k * 0.05 - time.monotonic()))

    feeder = threading.Thread(target=feed, daemon=True)
    feeder.start()
    try:
        end = time.monotonic() + 40
        while time.monotonic() < end and not (live and len(live[-1]["dab"]["services"]) >= 5):
            time.sleep(0.5)
        assert live, decoder.error
        mux = live[-1]["dab"]
        assert mux["ensemble"] and mux["block"] == "11D" and len(mux["services"]) >= 5
        assert {s["key"] for s in stored} >= {s["label"] for s in mux["services"] if s["label"]}
        # A station's audio, as MP3
        sid = mux["services"][0]["sid"]
        with urllib.request.urlopen(decoder.url(f"/mp3/{sid}"), timeout=20) as r:
            head = r.read(4096)
        assert head[:2] in (b"\xff\xfb", b"\xff\xf3", b"ID")
    finally:
        stop.set()
        feeder.join(timeout=2)
        decoder.stop()
