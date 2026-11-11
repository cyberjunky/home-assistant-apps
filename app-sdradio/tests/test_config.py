"""The app options."""

import pytest

from src.config import Config


def test_defaults():
    config = Config.from_dict({"receiver": {"type": "rtl-sdr"}, "tune": {"frequency": 145.5, "mode": "nfm"}})
    assert config.tune.frequency == 145_500_000
    assert config.receiver.device_args() == {"driver": "rtlsdr"}
    assert config.public()["tune"]["frequency"] == 145.5


def test_device_args():
    hackrf = Config.from_dict({"receiver": {"type": "hackrf", "serial": "abc"}, "tune": {"frequency": 1, "mode": "am"}})
    assert hackrf.receiver.device_args() == {"driver": "hackrf", "serial": "abc"}
    network = Config.from_dict(
        {
            "receiver": {"type": "network", "network_host": "10.0.0.2", "network_port": 1235},
            "tune": {"frequency": 1, "mode": "am"},
        }
    )
    assert network.receiver.device_args() == {"driver": "rtltcp", "rtltcp": "10.0.0.2:1235"}
    assert (
        Config.from_dict(
            {"receiver": {"type": "soapysdr"}, "tune": {"frequency": 1, "mode": "am"}}
        ).receiver.device_args()
        == {}
    )


@pytest.mark.parametrize(
    "options, message",
    [
        ({"receiver": {"type": "network"}, "tune": {"frequency": 1, "mode": "am"}}, "network_host"),
        ({"receiver": {"type": "hackrf", "bias_tee": True}, "tune": {"frequency": 1, "mode": "am"}}, "bias_tee"),
        ({"receiver": {"type": "rtl-sdr"}, "tune": {"frequency": 1, "mode": "fm"}}, "Unknown mode"),
        ({"receiver": {"type": "airspy"}, "tune": {"frequency": 1, "mode": "am"}}, "Unknown receiver"),
    ],
)
def test_invalid(options, message):
    with pytest.raises(ValueError, match=message):
        Config.from_dict(options)


def test_out_of_range_values_fall_back():
    config = Config.from_dict(
        {
            "receiver": {"type": "rtl-sdr", "sample_rate": 50, "ppm_correction": "x"},
            "tune": {"frequency": 1, "mode": "am"},
        }
    )
    assert config.receiver.sample_rate == 2_400_000
    assert config.receiver.ppm_correction == 0
