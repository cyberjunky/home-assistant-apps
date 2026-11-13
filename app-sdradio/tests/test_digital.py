"""Digital voice: DSD-FME's event log read into calls, its voice mixed to mono for the page.

test_dmr_recording runs DSD-FME itself (in the app image) on SDRADIO_DMR_DATA: the FM audio of a
DMR transmission, 16-bit at 48 kHz; it is skipped without them.
"""

import os
import shutil
import time
from pathlib import Path

import numpy as np
import pytest

from src.decoders.digital import Digital, parse_events
from src.errors import ValidationError

# What DSD-FME wrote for sigidwiki's DMR recording (a group call, then text messages)
LOG = """2026-11-13 11:53:32 DSD-FME Started and Event History Initialized;
2026-11-13 11:53:32 Any decoded voice calls or data calls display here;
2026-11-13 11:53:35 DMR TGT: 00000100; SRC: 00000022; CC: 13; Group; Slot 1;
2026-11-13 11:53:35 TMS SRC: 22; DST: 21;
The quick brown fox jumps over the lazy dog. 1234567890.
2026-11-13 11:53:36 DSTAR TGT: CQCQCQ   SRC: PA3XYZ  /ID51
 Talker Alias: Jan
2026-11-13 11:53:37 P25 TGT: 00001001; SRC: 01234567; NAC: 293; ENC; Emergency; Group;
 GPS: 52.1N 5.1E
"""


def test_events():
    calls = parse_events(LOG)
    assert [c["system"] for c in calls] == ["DMR", "DMR text", "D-STAR", "P25"]
    dmr, tms, dstar, p25 = calls
    assert dmr["target"] == "100" and dmr["source"] == "22" and dmr["cc"] == "13" and dmr["flags"] == ["Group"]
    assert dmr["slot"] == 1
    assert tms["source"] == "22" and tms["target"] == "21" and tms["text"].startswith("The quick brown fox")
    assert dstar["target"] == "CQCQCQ" and dstar["source"] == "PA3XYZ /ID51" and dstar["alias"] == "Jan"
    assert p25["nac"] == "293" and p25["flags"] == ["ENC", "Emergency", "Group"] and p25["gps"] == "52.1N 5.1E"


def test_protocol_setting():
    assert Digital.validate({"protocol": "dstar"})["protocol"] == "dstar"
    with pytest.raises(ValidationError):
        Digital.validate({"protocol": "tetra"})


def test_stereo_voice_to_mono():
    mode = Digital({"protocol": "dmr"}, lambda m: None)
    slot1 = np.array([1000, 2000, 3000], "<i2")
    stereo = np.empty(6, "<i2")
    stereo[0::2], stereo[1::2] = slot1, 0
    mode._voice(stereo.tobytes())
    assert np.frombuffer(mode.take_voice(), "<i2").tolist() == [1000, 2000, 3000]
    assert mode.take_voice() is None and mode.speaking
    mode.stop()


def test_log_read_as_it_grows(tmp_path):
    out = []
    mode = Digital({}, out.append)
    mode.events.write_text(LOG.split("2026-11-13 11:53:36")[0])
    mode._read_events()
    assert [c["key"] for c in out] == ["100", "21"]
    with mode.events.open("a") as f:
        f.write("2026-11-13 11:53:36" + LOG.split("2026-11-13 11:53:36")[1])
    mode._read_events()
    assert [c["key"] for c in out] == ["100", "21", "CQCQCQ", "1001"] and mode.count == 4
    mode.stop()


def test_the_radio_plays_the_decoded_voice(tmp_path, monkeypatch):
    """In this mode the page hears the decoder's voice (8 kHz), never the FM audio."""
    from tests.test_modes import _real_radio, run

    r = _real_radio(tmp_path, [])
    r.listeners = 1
    r.command("decoder", key="digital")
    run(r, 1)
    monkeypatch.setattr(r.decoder, "take_voice", lambda: b"\x01\x00" * 160)
    r.events.clear()
    run(r, 3)
    sent = [p for k, p in r.events if k == "audio"]
    assert sent and all(p["rate"] == 8000 and len(p["pcm"]) == 320 for p in sent)
    r.db.close()


DATA = os.environ.get("SDRADIO_DMR_DATA")


@pytest.mark.skipif(not (DATA and shutil.which("dsd-fme")), reason="needs dsd-fme and SDRADIO_DMR_DATA")
def test_dmr_recording():
    out = []
    mode = Digital({"protocol": "dmr"}, out.append)
    audio = np.fromfile(DATA, "<i2").astype(np.float32) / 32000
    voice = 0
    for i in range(0, len(audio), 2400):
        mode.feed_audio(audio[i : i + 2400], 48_000)
        time.sleep(0.0125)  # four times as fast as the radio: DSD-FME keeps up, nothing dropped
        voice += len(mode.take_voice() or b"")
    time.sleep(2)
    mode.feed_audio(np.zeros(2400, np.float32), 48_000)
    voice += len(mode.take_voice() or b"")
    mode._read_events()
    dropped = mode.program.dropped
    mode.stop()
    calls = [c for c in out if c["system"] == "DMR"]
    assert calls and calls[0]["target"] == "100" and calls[0]["source"] == "22"
    assert any("quick brown fox" in (c.get("text") or "") for c in out)
    assert dropped == 0 and voice > 8000 * 2 * 10  # more than ten seconds of speech
    assert Path(mode.workdir).exists() is False
