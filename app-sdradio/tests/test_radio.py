"""The radio thread's logic, block by block with the simulated receiver (not in real time)."""

import pytest

from src.config import Config
from src.database import Database
from src.radio import Radio
from tests.simulator import SimulatedDevice

# One steady NFM signal and one that is never on
SIGNALS = [(145_500_000, "nfm", 0.05, 1000, 0, 800)]


@pytest.fixture
def radio(tmp_path):
    db = Database(tmp_path / "sdradio.db")
    events = []
    devices = []

    def factory(config):
        device = SimulatedDevice(config, realtime=False, signals=SIGNALS)
        devices.append(device)
        return device

    r = Radio(Config(), db, factory, lambda kind, payload: events.append((kind, payload)))
    r.events = events
    r.devices = devices
    r._open()
    yield r
    db.close()


def run(radio: Radio, blocks: int) -> None:
    for _ in range(blocks):
        radio._handle_commands()
        radio._process(radio.device.read(radio.block_size))


def test_opens_and_reports_state(radio):
    assert radio.device is not None
    run(radio, 5)
    states = [p for k, p in radio.events if k == "state"]
    assert states[-1]["frequency"] == 145_500_000
    assert states[-1]["device"]["ok"] is True


def test_tuning_inside_the_span_does_not_move_the_receiver(radio):
    centre = radio.center
    radio.command("tune", frequency=145_700_000)
    run(radio, 1)
    assert radio.center == centre and radio.frequency == 145_700_000
    radio.command("tune", frequency=430_000_000)
    run(radio, 1)
    assert radio.center != centre
    assert radio.settle > 0


def test_squelch_and_hit(radio):
    radio.command("set", squelch=-60)
    run(radio, 20)
    assert radio.signal is not None  # the steady signal opens the squelch
    radio.command("tune", frequency=145_300_000)  # tuning away ends the transmission
    run(radio, 2)
    hits = [p for k, p in radio.events if k == "hit"]
    assert hits and hits[0]["frequency"] == 145_500_000
    assert radio.db.recent_hits(1)[0]["frequency"] == 145_500_000


def test_audio_only_with_listeners(radio):
    radio.command("set", squelch=None)
    run(radio, 3)
    assert not any(k == "audio" for k, _ in radio.events)
    radio.listeners = 1
    run(radio, 3)
    audio = [p for k, p in radio.events if k == "audio"]
    assert audio and audio[0]["rate"] == 48_000 and len(audio[0]["pcm"]) == 2 * 2400


def test_channel_scan_stops_on_the_busy_channel(radio):
    db = radio.db
    db.save_channel({"name": "Quiet", "frequency": 145_300_000, "mode": "nfm"})
    db.save_channel({"name": "Busy", "frequency": 145_500_000, "mode": "nfm"})
    db.save_channel({"name": "Quiet 2", "frequency": 145_400_000, "mode": "nfm"})
    radio.command("tune", frequency=145_250_000)
    radio.command("set", squelch=-60)
    radio.command("scan", action="start", kind="channels")
    run(radio, 60)
    assert radio.scan.kind == "channels"
    assert radio.frequency == 145_500_000
    assert radio.signal is not None
    radio.command("scan", action="stop")
    run(radio, 1)
    assert radio.scan.kind == ""


def test_skip_goes_on_past_the_busy_channel(radio):
    db = radio.db
    db.save_channel({"name": "Quiet", "frequency": 145_300_000, "mode": "nfm"})
    db.save_channel({"name": "Busy", "frequency": 145_500_000, "mode": "nfm"})
    db.save_channel({"name": "Quiet 2", "frequency": 145_400_000, "mode": "nfm"})
    radio.command("tune", frequency=145_250_000)
    radio.command("set", squelch=-60)
    radio.command("scan", action="start", kind="channels")
    run(radio, 60)
    assert radio.frequency == 145_500_000 and radio.signal is not None
    radio.command("scan", action="skip")
    run(radio, 1)
    assert radio.scan.kind == "channels" and radio.frequency != 145_500_000
    assert [p for k, p in radio.events if k == "hit"]  # the transmission it left is logged
    # A held channel goes on too, and the hold is off
    radio.command("scan", action="hold")
    run(radio, 1)
    held = radio.frequency
    radio.command("scan", action="skip")
    run(radio, 1)
    assert not radio.scan.hold and radio.frequency != held


def test_band_scan_skips_ignored_and_finds_signal(radio):
    radio.command("set", squelch=-60)
    radio.db.ignore(145_487_500)
    radio.reload()  # the web API does this after every change
    radio.command("tune", frequency=145_450_000)
    radio.command("scan", action="start", kind="band")
    run(radio, 3)
    assert radio.scan.label == "2 m"
    assert all(t["frequency"] != 145_487_500 for t in radio.scan.targets)
    run(radio, 40)
    assert radio.frequency == 145_500_000


def test_no_hits_from_noise_with_a_low_squelch(radio):
    radio.command("tune", frequency=145_300_000)  # nothing here
    radio.command("set", squelch=-130)  # below the noise: the squelch is always open
    run(radio, 60)
    assert radio.signal is None
    assert not [p for k, p in radio.events if k == "hit"]


def test_lockout_toggles(radio):
    channel = radio.db.save_channel({"name": "Busy", "frequency": 145_500_000, "mode": "nfm"})
    radio.command("channel", id=channel["id"])
    radio.command("lockout")
    run(radio, 1)
    assert radio.db.channel(channel["id"])["scan"] == 0
    radio.command("tune", frequency=145_525_000)
    radio.command("lockout")
    run(radio, 1)
    assert 145_525_000 in radio.ignored


def test_step_tune_and_cycles(radio):
    radio.command("tune", frequency=145_503_000, step=12_500)
    radio.command("step_tune", steps=1)
    run(radio, 1)
    assert radio.frequency == 145_512_500
    radio.command("step_tune", steps=-2)
    run(radio, 1)
    assert radio.frequency == 145_487_500
    radio.command("cycle", what="mode")
    run(radio, 1)
    assert radio.mode == "wfm" and radio.bandwidth == 180_000
    radio.command("band", name="Air")
    run(radio, 1)
    assert radio.mode == "am" and 118e6 <= radio.frequency <= 137e6
    start = radio.frequency
    radio.command("step_tune", steps=3)  # 8.333 kHz doesn't fit a MHz: plain steps
    run(radio, 1)
    assert radio.frequency == start + 3 * 8_333


def test_state_survives_restart(radio, tmp_path):
    radio.command("tune", frequency=121_500_000, mode="am")
    run(radio, 1)
    radio._save_state()
    again = Radio(Config(), radio.db, lambda c: SimulatedDevice(c, realtime=False), lambda *_: None)
    assert again.frequency == 121_500_000 and again.mode == "am"


def test_manual_tuning_takes_band_and_channel_settings(radio):
    radio.command("tune", frequency=121_500_000)  # into the airband: AM
    run(radio, 1)
    assert radio.mode == "am" and radio.step == 8_333
    channel = radio.db.save_channel({"name": "Wide", "frequency": 121_600_000, "mode": "nfm", "squelch": -50})
    radio.reload()
    radio.command("tune", frequency=121_600_000)  # onto a stored channel: its settings
    run(radio, 1)
    assert radio.mode == "nfm" and radio.squelch == -50 and radio.channel["id"] == channel["id"]
    radio.command("tune", frequency=121_700_000)  # same band, not stored: nothing changes
    run(radio, 1)
    assert radio.mode == "nfm"


def _notices(radio):
    return [p["message"] for k, p in radio.events if k == "notice"]


def test_out_of_range_is_refused(radio):
    """The simulated receiver tunes 24 to 1766 MHz, like an RTL-SDR."""
    radio.command("tune", frequency=531_000)
    run(radio, 1)
    assert radio.frequency == 145_500_000
    assert "out of range" in _notices(radio)[-1]


def test_band_key_skips_bands_out_of_range(radio):
    seen = []
    for _ in range(len(radio.bands) + 2):
        radio.command("band", direction=1)
        run(radio, 1)
        seen.append(radio.frequency)
    assert all(24_000_000 <= f <= 1_766_000_000 for f in seen)
    names = {b["name"] for b in radio.bands if any(b["start"] <= f <= b["stop"] for f in seen)}
    assert {"FM", "Air", "2 m", "70 cm"} <= names
    assert not names & {"LW", "MW", "SW 40 m", "SW 20 m", "13 cm", "ISM 2.4 GHz"}


def test_band_scan_out_of_range_says_so(radio):
    radio.command("scan", action="start", kind="band", band="MW")
    run(radio, 1)
    assert radio.scan.kind == ""
    assert "MW is out of range" in _notices(radio)[-1]


def test_band_view_sweeps_the_band(radio):
    radio.spectrum_listeners = 1
    radio.sweep_wanted = True
    run(radio, 3)
    sweep = radio.sweep
    # 2 m is 2 MHz: wider than the 1.92 MHz used of one capture, so two steps
    assert sweep is not None and sweep["label"] == "2 m" and sweep["count"] == 2
    assert sweep["start"] <= 144_000_000 and sweep["start"] + sweep["span"] >= 146_000_000
    radio.events.clear()
    run(radio, sweep["count"] * 3 + 3)
    frames = [p for k, p in radio.events if k == "spectrum"]
    assert frames, "a complete pass sends a frame"
    frame = frames[-1]
    assert frame["rate"] == sweep["span"]
    # The simulated transmitter on 145.5 MHz stands out of the swept band
    levels = list(frame["bins"])
    at = int((145_500_000 - (frame["center"] - frame["rate"] / 2)) / frame["rate"] * len(levels))
    assert max(levels[at - 5 : at + 5]) > sorted(levels)[len(levels) // 2] + 40
    assert not [k for k, _ in radio.events if k == "audio"]
    # Back to normal: the receiver returns to the tuned frequency
    radio.sweep_wanted = False
    run(radio, 1)
    assert radio.sweep is None and abs(radio.center - 145_500_000) < radio.rate / 2


def test_hackrf_reaches_hf_and_ghz_bands(radio):
    """A HackRF tunes 1 MHz to 6 GHz: its bands are reachable, the BAND key goes there."""
    radio.device.frequency_range = (1_000_000.0, 6_000_000_000.0)
    radio.command("band", name="ISM 2.4 GHz")
    run(radio, 1)
    assert 2_400_000_000 <= radio.frequency <= 2_483_500_000
    radio.command("band", name="SW 40 m")
    run(radio, 1)
    assert radio.frequency == 7_000_000 and radio.mode == "lsb"


def test_autotune_centres_on_the_signal(radio):
    radio.command("tune", frequency=145_492_000)
    run(radio, 3)
    radio.command("autotune")
    run(radio, 10)
    assert radio.frequency == 145_500_000  # on the 12.5 kHz grid
    radio.command("tune", frequency=145_300_000)  # nothing within a channel of here
    run(radio, 3)
    radio.command("autotune")
    run(radio, 10)
    assert radio.frequency == 145_300_000
    assert any(k == "notice" and "No signal" in p["message"] for k, p in radio.events)


def test_power_off_closes_the_receiver_and_on_opens_it(radio):
    import time

    radio.start()
    try:
        radio.command("power", on=False)
        end = time.monotonic() + 3
        while radio.device is not None and time.monotonic() < end:
            time.sleep(0.02)
        assert radio.device is None and radio.devices[-1].closed
        assert radio.snapshot()["power"] is False and radio.db.state()["power"] is False
        radio.command("tune", frequency=145_300_000)  # ignored while off
        time.sleep(0.3)
        assert radio.frequency != 145_300_000
        radio.command("power", on=True)
        end = time.monotonic() + 3
        while radio.device is None and time.monotonic() < end:
            time.sleep(0.02)
        assert radio.device is not None and len(radio.devices) == 2
    finally:
        radio.stop()
