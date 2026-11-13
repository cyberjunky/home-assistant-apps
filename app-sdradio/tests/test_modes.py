"""The modes: parsing the decoder programs' output, their settings, and the radio switching modes.

The tests that run multimon-ng and rtl_433 themselves are skipped where those aren't installed
(run them in the app image); the rtl_433 ones also need SDRADIO_RTL433_DATA, a folder with
recordings from the rtl_433_tests project.
"""

import json
import os
import shutil
import time
from pathlib import Path

import numpy as np
import pytest
from scipy import signal

from src.config import Config
from src.database import Database, ValidationError
from src.decoders import Decoder, pager, sensors
from src.dsp import Demodulator
from src.radio import Radio
from tests import pocsag_signal
from tests.simulator import SimulatedDevice

RATE = 2_400_000
BLOCK = RATE // 20


# ---------------------------------------------------------------- parsing


def test_pager_parses_multimon_lines():
    assert pager.parse("POCSAG1200: Address:  123456  Function: 3  Alpha:   Hello there<NUL>") == {
        "protocol": "POCSAG1200",
        "address": "123456",
        "function": 3,
        "type": "alpha",
        "text": "Hello there",
    }
    assert pager.parse("POCSAG512: Address: 2000  Function: 0  Numeric: 0612 345")["type"] == "numeric"
    assert pager.parse("POCSAG2400: Address: 777  Function: 1 ")["type"] == "tone"
    flex = pager.parse("FLEX|2026-11-12 10:00:00|1600/2/K/A|07.034|001234567 001234568|ALN|P 1 BDH-01 A|B")
    assert flex["address"] == "001234567 001234568" and flex["text"] == "P 1 BDH-01 A|B" and flex["type"] == "alpha"
    assert pager.parse("FLEX: FrameInfoWord ...") is None
    assert pager.parse("multimon-ng 1.3.1") is None


def test_sensors_parses_rtl433_json():
    line = json.dumps({"time": "@0.09s", "model": "Nexus-TH", "id": 181, "channel": 2, "battery_ok": 0,
                       "temperature_C": 19.0, "humidity": 71, "rssi": -12.1, "snr": 20.3, "mic": "CRC"})  # fmt: skip
    reading = sensors.parse(line)
    assert reading["key"] == "Nexus-TH/181/2"
    assert reading["values"] == {"battery_ok": 0, "temperature_C": 19.0, "humidity": 71}
    assert reading["rssi"] == -12.1
    assert sensors.parse("rtl_433 version 25.02") is None


# ---------------------------------------------------------------- settings


def test_mode_settings_are_checked(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    settings = db.settings()
    assert settings["mode_pager"]["frequency"] == 169_650_000
    saved = db.update_settings({"mode_pager": {"frequency": 466_075_000, "protocols": ["POCSAG1200"]}})
    assert saved["mode_pager"]["protocols"] == ["POCSAG1200"] and saved["mode_pager"]["frequency"] == 466_075_000
    with pytest.raises(ValidationError):
        db.update_settings({"mode_pager": {"frequency": 169_650_000, "protocols": ["MORSE"]}})
    with pytest.raises(ValidationError):
        db.update_settings({"mode_sensors": {"frequency": 7e9}})
    db.close()


def test_decoded_history(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    for i in range(5):
        db.add_decoded(1000 + i, "sensors", "Nexus-TH/181/2" if i % 2 else "Acurite/1", 433_920_000, {"n": i})
    assert [x["n"] for x in db.decoded("sensors", limit=3)] == [4, 3, 2]
    keys = {k["key"]: k for k in db.decoded_keys("sensors")}
    assert keys["Nexus-TH/181/2"]["count"] == 2 and keys["Nexus-TH/181/2"]["n"] == 3
    assert db.delete_decoded("sensors", "Acurite/1") == 3
    db.close()


# ---------------------------------------------------------------- the radio in a mode


class FakeDecoder(Decoder):
    key = "fake"
    name = "Fake"
    input = "iq"
    defaults = {"frequency": 433_920_000}
    demod = ("am", 15_000)
    blocks = 0

    def feed_iq(self, iq, rate, offset):
        FakeDecoder.blocks += 1
        if FakeDecoder.blocks == 3:
            self.publish({"key": "dev/1", "values": {"temperature_C": 20.5}})


@pytest.fixture
def radio(tmp_path, monkeypatch):
    import src.radio

    monkeypatch.setattr(src.radio, "registry", lambda: {"fake": FakeDecoder})
    db = Database(tmp_path / "db.sqlite")
    events = []
    r = Radio(
        Config(), db, lambda c: SimulatedDevice(c, realtime=False, signals=[]), lambda k, p: events.append((k, p))
    )
    r.events = events
    r._open()
    yield r
    db.close()


def run(radio, blocks):
    for _ in range(blocks):
        radio._handle_commands()
        radio._process(radio.device.read(radio.block_size))


def test_radio_switches_to_a_mode_and_back(radio):
    FakeDecoder.blocks = 0
    radio.command("decoder", key="fake")
    run(radio, 6)
    assert radio.frequency == 433_920_000 and radio.mode == "am"
    assert FakeDecoder.blocks >= 3
    decoded = [p for k, p in radio.events if k == "decoded"]
    assert decoded and decoded[0]["item"]["key"] == "dev/1" and decoded[0]["item"]["values"]["temperature_C"] == 20.5
    assert radio.db.decoded("fake")[0]["key"] == "dev/1"
    assert radio.snapshot()["decoder"]["key"] == "fake"
    radio.command("decoder", key="radio")
    run(radio, 2)
    assert radio.decoder is None and radio.snapshot()["decoder"] == {"key": "radio"}


def test_unknown_mode_is_refused(radio):
    radio.command("decoder", key="nothing")
    run(radio, 1)
    assert radio.decoder is None


# ---------------------------------------------------------------- with the real programs


def _wait(items, count=1, seconds=8.0):
    end = time.monotonic() + seconds
    while len(items) < count and time.monotonic() < end:
        time.sleep(0.1)


@pytest.mark.skipif(not (shutil.which("multimon-ng") and shutil.which("sox")), reason="needs multimon-ng and sox")
@pytest.mark.parametrize("baud", [512, 1200, 2400])
def test_pager_decodes_pocsag(baud):
    offset = 120_000
    data = pocsag_signal.bits(1234567, 3, f"SDRadio test {baud}")
    iq = pocsag_signal.fm_iq(data, baud, RATE, offset)
    noise = np.random.default_rng(1).standard_normal((len(iq), 2)) * 0.003
    iq = (iq + noise[:, 0] + 1j * noise[:, 1]).astype(np.complex64)
    items = []
    decoder = pager.Pager({"protocols": pager.PROTOCOLS}, items.append)
    demod = Demodulator(RATE, *decoder.demod)
    demod.set_offset(offset)
    for i in range(0, len(iq), BLOCK):
        audio, _ = demod.process(iq[i : i + BLOCK])
        decoder.feed_audio(audio, demod.audio_rate)
    _wait(items)
    decoder.stop()
    assert items, f"multimon-ng found nothing ({decoder.error})"
    assert items[0]["protocol"] == f"POCSAG{baud}"
    assert items[0]["key"] == "1234567" and items[0]["text"] == f"SDRadio test {baud}"


RTL433_DATA = os.environ.get("SDRADIO_RTL433_DATA")


@pytest.mark.skipif(not shutil.which("rtl_433") or not RTL433_DATA, reason="needs rtl_433 and SDRADIO_RTL433_DATA")
@pytest.mark.parametrize("name", ["nexus_01_gfile001", "bresser_5in1_01_g001_868.3M_250k"])
def test_sensors_decode_recordings(name):
    """An rtl_433 test recording (250 kHz) made into what the receiver delivers: 2.4 MS/s, 300 kHz off centre."""
    folder = Path(RTL433_DATA)
    raw = np.fromfile(folder / f"{name}.cu8", dtype=np.uint8).astype(np.float32)
    x = ((raw[0::2] - 127.5) + 1j * (raw[1::2] - 127.5)) / 128
    up = signal.resample_poly(x, 48, 5)
    offset = 300_000
    up = up * np.exp(2j * np.pi * offset * np.arange(len(up)) / RATE)
    quiet = np.zeros(RATE // 2, dtype=np.complex64)
    iq = np.concatenate([quiet, up.astype(np.complex64), quiet, quiet])
    items = []
    decoder = sensors.Sensors({}, items.append)
    for i in range(0, len(iq), BLOCK):
        decoder.feed_iq(iq[i : i + BLOCK], RATE, offset)
    _wait(items)
    decoder.stop()
    expected = json.loads((folder / f"{name}.json").read_text())
    assert items, f"rtl_433 found nothing ({decoder.error})"
    assert items[0]["model"] == expected["model"] and items[0]["id"] == expected["id"]
    assert items[0]["values"]["temperature_C"] == expected["temperature_C"]


class CentredDecoder(FakeDecoder):
    key = "centred"
    centred = True
    defaults = {"frequency": 1_090_000_000}


def test_a_centred_mode_has_the_receiver_on_its_frequency(radio, monkeypatch):
    import src.radio

    monkeypatch.setattr(src.radio, "registry", lambda: {"fake": FakeDecoder, "centred": CentredDecoder})
    radio.command("decoder", key="centred")
    run(radio, 3)
    assert radio.frequency == 1_090_000_000 and radio.center == 1_090_000_000
    radio.command("decoder", key="fake")
    run(radio, 3)
    assert radio.center != radio.frequency  # the usual offset, away from the receiver's DC spike


def test_a_mode_keeps_to_its_frequency(radio):
    radio.command("decoder", key="fake")
    run(radio, 3)
    radio.command("scan", action="start", kind="band")
    radio.command("priority", on=True)
    radio.command("band", direction=1)
    radio.command("cycle", what="mode")
    run(radio, 3)
    assert not radio.scan.kind and not radio.priority
    assert radio.frequency == 433_920_000 and radio.mode == "am"
    assert any(k == "notice" and "No scanning" in p["message"] for k, p in radio.events)
    d = radio.snapshot()["decoder"]
    assert d["decoded_by"] == "SDRadio" and d["input"] == "iq"
    radio.command("decoder", key="radio")
    radio.command("scan", action="start", kind="band")
    run(radio, 2)
    assert radio.scan.kind == "band"


def test_sensors_count_the_bursts_rtl433_saw():
    decoder = sensors.Sensors({}, lambda item: None)
    decoder._line(
        json.dumps({"time": "@10s", "enabled": 238, "frames": {"count": 3, "fsk": 0, "events": 3}, "stats": []})
    )
    decoder._line(
        json.dumps({"time": "@20s", "enabled": 238, "frames": {"count": 2, "fsk": 1, "events": 2}, "stats": []})
    )
    assert decoder.status()["bursts"] == 5 and decoder.status()["count"] == 0


def test_modes_without_sound_measure_their_channel(radio, monkeypatch):
    """A mode whose decoder reads the samples has no audio; the meter shows the channel, not the band."""
    import src.radio

    class Silent(FakeDecoder):
        key = "silent"
        listen = False

    monkeypatch.setattr(src.radio, "registry", lambda: {"silent": Silent})
    radio.listeners = 1
    radio.command("decoder", key="silent")
    run(radio, 6)
    assert not any(k == "audio" for k, _ in radio.events[-20:])
    quiet = radio.level
    assert quiet < -50  # noise in a 15 kHz channel, not the whole 2.4 MHz


def test_a_channel_brings_its_mode(radio):
    """A stored channel tagged mode:<mode> switches to that mode on its frequency; another goes back to the radio."""
    db = radio.db
    weather = db.save_channel({"name": "Weather", "frequency": 434_000_000, "mode": "am", "tags": "ism,mode:fake"})
    calling = db.save_channel({"name": "Calling", "frequency": 145_500_000, "mode": "nfm"})
    radio.command("channel", id=weather["id"])
    run(radio, 3)
    assert radio.decoder_key == "fake" and radio.frequency == 434_000_000 and radio.decoder.frequency == 434_000_000
    # A settings change restarts the mode, still on the channel's frequency
    radio.db.update_settings({"decoded_keep_days": 7})
    radio.reload()
    run(radio, 2)
    assert radio.decoder.frequency == 434_000_000
    radio.command("channel", id=calling["id"])
    run(radio, 2)
    assert radio.decoder is None and radio.decoder_key == "radio" and radio.frequency == 145_500_000
    # ◀ ▶ too
    radio.command("memory", direction=-1)
    run(radio, 3)
    assert radio.decoder_key == "fake" and radio.frequency == 434_000_000


def _real_radio(tmp_path, signals, receiver="rtl-sdr"):
    config = Config()
    config.receiver.type = receiver
    db = Database(tmp_path / "db.sqlite")
    events = []
    r = Radio(
        config, db, lambda c: SimulatedDevice(c, realtime=False, signals=signals), lambda k, p: events.append((k, p))
    )
    r.events = events
    r.files_dir = tmp_path / "files"
    r._open()
    return r


def test_ctcss_under_an_nfm_channel(tmp_path):
    from tests import simulator

    r = _real_radio(tmp_path, [(145_612_500, "nfm", 0.05, 30, 0, 800)])
    r.command("tune", frequency=145_612_500, mode="nfm")
    run(r, 20 * 4)
    assert simulator.CTCSS[145_612_500] == 88.5
    assert r.snapshot()["tones"] == {"ctcss": 88.5, "dcs": None, "dtmf": None}
    r.db.close()


def test_weather_fax_through_the_radio(tmp_path, monkeypatch):
    from scipy.signal import resample_poly

    from tests import simulator, wefax_signal

    short = resample_poly(wefax_signal.transmission(wefax_signal.chart(60), phasing=6, start=3.0), 4, 1)
    monkeypatch.setattr(simulator.SimulatedDevice, "_wefax_audio", staticmethod(lambda: short.astype(np.float32)))
    r = _real_radio(tmp_path, [(7_878_100, "wefax", 0.05, 0, 0, 0)], receiver="hackrf")
    r.command("decoder", key="wefax")
    seconds = len(short) / 48_000 + 1
    run(r, int(seconds * 20))
    [chart] = r.db.decoded("wefax")
    assert chart["rows"] >= 55 and (tmp_path / "files" / "wefax" / chart["file"]).exists()
    assert r.frequency == 7_878_100 and r.mode == "usb"
    r.db.close()


def test_every_mode_starts_and_stops(tmp_path):
    """Every mode through the radio, on a receiver that can do them all: started, fed, stopped."""
    from src.decoders import registry

    r = _real_radio(tmp_path, [], receiver="hackrf")
    for key in registry():
        r.command("decoder", key=key)
        run(r, 3)
        assert r.decoder is not None and r.decoder.key == key, key
        assert r.snapshot()["decoder"]["key"] == key
    r.command("decoder", key="radio")
    run(r, 2)
    assert r.decoder is None
    r.db.close()


@pytest.mark.parametrize("key", ["pager", "aprs", "ft8", "sstv"])
def test_data_modes_are_decoded_but_not_heard(tmp_path, key):
    """Pager bursts, APRS packets, FT8 tones: the decoder gets the audio, a listening page doesn't."""
    r = _real_radio(tmp_path, [], receiver="hackrf")
    r.listeners = 1
    r.command("decoder", key=key)
    run(r, 1)
    fed = []
    r.decoder.feed_audio = lambda audio, rate: fed.append(len(audio))
    r.events.clear()
    run(r, 4)
    assert fed and not [k for k, _ in r.events if k == "audio"]
    assert r.snapshot()["decoder"]["listen"] is False  # the display says "no sound"
    r.command("decoder", key="radio")
    run(r, 2)
    r.events.clear()
    run(r, 2)
    assert [k for k, _ in r.events if k == "audio"]  # the radio is heard again
    r.db.close()


def test_memory_skips_channels_this_receiver_cant_use(tmp_path):
    """◀ ▶ on an RTL-SDR: past shortwave (out of range) and a Video channel (a HackRF mode)."""
    r = _real_radio(tmp_path, [])
    db = r.db
    first = db.save_channel({"number": 1, "name": "2 m", "frequency": 145_500_000, "mode": "nfm"})
    db.save_channel(
        {"number": 2, "name": "1.2 GHz video", "frequency": 1_200_000_000, "mode": "wfm", "tags": "mode:video"}
    )
    db.save_channel({"number": 3, "name": "VOLMET", "frequency": 5_505_000, "mode": "usb"})
    db.save_channel({"number": 4, "name": "PMR", "frequency": 446_006_250, "mode": "nfm"})
    r.command("channel", id=first["id"])
    run(r, 2)
    assert r.frequency == 145_500_000
    r.command("memory", direction=1)
    run(r, 2)
    assert r.frequency == 446_006_250 and r.decoder is None
    r.command("memory", direction=1)
    run(r, 2)
    assert r.frequency == 145_500_000
    r.db.close()
