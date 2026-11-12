"""SSTV: pictures decoded from their audio, also with a clock that is off and with noise."""

import numpy as np
import pytest

from src.decoders.sstv import KINDS, Sstv, SstvReader
from tests import sstv_signal

FAST = ["Robot 36", "Martin M2", "PD50"]


def decode(name, clock=1.0, noise=0.0, seed=1):
    k = sstv_signal.kind(name)
    image = sstv_signal.test_picture(k.width, k.height)
    audio = sstv_signal.encode(name, image, clock=clock)
    audio = audio + np.random.default_rng(seed).standard_normal(len(audio)).astype(np.float32) * noise
    got = []
    reader = SstvReader(got.append)
    for i in range(0, len(audio), 2400):
        reader.feed(audio[i : i + 2400], 48_000)
    assert got, f"{name}: no picture"
    p = got[0]
    return p, float(np.abs(p.image.astype(int) - image.astype(int)).mean())


@pytest.mark.parametrize("name", [*FAST, "PD120"])
def test_kinds(name):
    p, err = decode(name)
    assert p.kind.name == name and p.rows >= p.kind.height and err < 4


@pytest.mark.parametrize("clock", [0.998, 1.002])
def test_a_clock_that_is_off(clock):
    _, err = decode("PD50", clock=clock)
    assert err < 6


def test_noise():
    p, err = decode("Martin M2", noise=0.2)
    assert p.rows == 256 and err < 30


def test_the_mode_saves_pictures(tmp_path):
    stored, live = [], []
    decoder = Sstv({}, stored.append, live.append)
    decoder.folder = tmp_path
    k = sstv_signal.kind("Robot 36")
    audio = sstv_signal.encode("Robot 36", sstv_signal.test_picture(k.width, k.height))
    for i in range(0, len(audio), 2400):
        decoder.feed_audio(audio[i : i + 2400], 48_000)
    assert stored and stored[0]["kind"] == "Robot 36" and stored[0]["complete"]
    assert (tmp_path / stored[0]["file"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert any(u.get("receiving") for u in live) and live[-1]["receiving"] is None
    assert Sstv({"frequency": 14_230_000}, print).demod[0] == "usb" and decoder.demod[0] == "nfm"


def test_every_kind_has_a_known_code():
    assert {k.vis for k in KINDS.values()} == set(KINDS)
