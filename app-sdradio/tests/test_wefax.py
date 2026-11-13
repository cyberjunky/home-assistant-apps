"""Weather fax: a transmission from start tone to stop tone becomes one picture, lined up."""

import numpy as np

from src.decoders.wefax import LINE, RATE, WIDTH, FaxReader, Wefax, tone
from tests import wefax_signal


def run(audio, rate=RATE, block=4800):
    pictures = []
    reader = FaxReader(lambda r: pictures.append(r.image.copy()))
    for i in range(0, len(audio), block):
        reader.feed(audio[i : i + block], rate)
    return pictures, reader


def band_start(row):
    """Where the white band starts: the first jump up of more than half the range."""
    smooth = np.convolve(row, np.ones(9) / 9, "same")
    return int(np.argmax(np.diff(smooth[WIDTH // 4 :]) > 15)) + WIDTH // 4 + 1


def test_tones():
    t = np.arange(RATE // 2) / RATE
    square = (np.sin(2 * np.pi * 300 * t) > 0).astype(float)
    assert tone(square, 300) > 0.7 and tone(square, 450) < 0.1
    assert tone(np.full(RATE // 2, 0.5), 300) == 0.0


def test_picture_lined_up_on_the_phasing():
    chart = wefax_signal.chart(120)
    audio = wefax_signal.transmission(chart, shift=1234)
    [picture], reader = run(audio)
    assert reader.state == "idle"
    assert picture.shape[1] == WIDTH and 110 <= picture.shape[0] <= 121
    # The ramp: dark on the left, bright on the right; the white band in the middle
    row = picture[5].astype(float)
    assert row[50] < 60 and row[-50] > 190
    assert row[WIDTH // 2] > 200 and row[int(WIDTH * 0.4)] < 0.5 * 255
    assert abs(band_start(row) - 0.45 * WIDTH) < 15


def test_from_48k_audio_with_noise():
    from scipy import signal

    audio = wefax_signal.transmission(wefax_signal.chart(80), shift=777)
    audio = signal.resample_poly(audio, 4, 1).astype(np.float32)
    audio += np.random.default_rng(2).standard_normal(len(audio)).astype(np.float32) * 0.05
    [picture], _ = run(audio, 48_000, 9600)
    assert abs(band_start(picture[10].astype(float)) - 0.45 * WIDTH) < 15


def test_noise_is_no_picture():
    noise = np.random.default_rng(3).standard_normal(RATE * 40).astype(np.float32) * 0.3
    pictures, reader = run(noise)
    assert pictures == [] and reader.state == "idle"


def test_mode(tmp_path):
    out, updates = [], []
    mode = Wefax({}, out.append, updates.append)
    mode.folder = tmp_path
    audio = wefax_signal.transmission(wefax_signal.chart(60))
    for i in range(0, len(audio), 4800):
        mode.feed_audio(audio[i : i + 4800], RATE)
    [item] = out
    assert item["frequency"] == 7_880_000 and (tmp_path / item["file"]).read_bytes()[:4] == b"\x89PNG"
    assert updates[-1] == {"receiving": None} and any(u.get("receiving") for u in updates)
    assert Wefax.why_not("rtl-sdr", (24e6, 1766e6)).startswith("HackRF only")
    assert LINE == 6000
