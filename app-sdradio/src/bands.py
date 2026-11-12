"""Modes, tuning steps and the default band plan."""

# Demodulator modes: the default filter bandwidth and the bandwidths the Filter knob offers (Hz)
MODES: dict[str, dict] = {
    "am": {"label": "AM", "bandwidth": 9_000, "bandwidths": [3_000, 5_000, 6_000, 8_333, 9_000, 10_000, 15_000]},
    "nfm": {"label": "NFM", "bandwidth": 12_500, "bandwidths": [6_250, 8_333, 10_000, 12_500, 16_000, 20_000, 25_000]},
    "wfm": {"label": "WFM", "bandwidth": 180_000, "bandwidths": [120_000, 150_000, 180_000, 200_000]},
    "usb": {"label": "USB", "bandwidth": 2_700, "bandwidths": [1_800, 2_100, 2_400, 2_700, 3_000, 3_600]},
    "lsb": {"label": "LSB", "bandwidth": 2_700, "bandwidths": [1_800, 2_100, 2_400, 2_700, 3_000, 3_600]},
    "cw": {"label": "CW", "bandwidth": 500, "bandwidths": [100, 250, 500, 800, 1_000]},
    "dsb": {"label": "DSB", "bandwidth": 6_000, "bandwidths": [3_000, 5_000, 6_000, 9_000]},
}

# The STEP key cycles through these (Hz)
STEPS = [10, 100, 500, 1_000, 2_500, 5_000, 6_250, 8_333, 9_000, 10_000, 12_500, 25_000, 50_000, 100_000, 1_000_000]

# The default band plan (Region 1), in order of frequency; editable on the Settings page.
# Frequencies in Hz. Only the bands the selected receiver can tune show up (an RTL-SDR 24 to
# 1766 MHz, a HackRF 1 MHz to 6 GHz); "button": False leaves a band off the BAND key.
DEFAULT_BANDS = [
    {"name": "LW", "start": 148_500, "stop": 283_500, "mode": "am", "step": 9_000, "bandwidth": 9_000},
    {"name": "MW", "start": 531_000, "stop": 1_602_000, "mode": "am", "step": 9_000, "bandwidth": 9_000},
    {"name": "SW 160 m", "start": 1_810_000, "stop": 2_000_000, "mode": "lsb", "step": 500, "bandwidth": 2_700, "button": False},
    {"name": "SW 80 m", "start": 3_500_000, "stop": 3_800_000, "mode": "lsb", "step": 500, "bandwidth": 2_700},
    {"name": "BC 49 m", "start": 5_900_000, "stop": 6_200_000, "mode": "am", "step": 5_000, "bandwidth": 9_000},
    {"name": "SW 40 m", "start": 7_000_000, "stop": 7_200_000, "mode": "lsb", "step": 500, "bandwidth": 2_700},
    {"name": "BC 41 m", "start": 7_200_000, "stop": 7_450_000, "mode": "am", "step": 5_000, "bandwidth": 9_000, "button": False},
    {"name": "BC 31 m", "start": 9_400_000, "stop": 9_900_000, "mode": "am", "step": 5_000, "bandwidth": 9_000},
    {"name": "SW 30 m", "start": 10_100_000, "stop": 10_150_000, "mode": "cw", "step": 100, "bandwidth": 500, "button": False},
    {"name": "BC 25 m", "start": 11_600_000, "stop": 12_100_000, "mode": "am", "step": 5_000, "bandwidth": 9_000, "button": False},
    {"name": "SW 20 m", "start": 14_000_000, "stop": 14_350_000, "mode": "usb", "step": 500, "bandwidth": 2_700},
    {"name": "BC 19 m", "start": 15_100_000, "stop": 15_800_000, "mode": "am", "step": 5_000, "bandwidth": 9_000, "button": False},
    {"name": "SW 17 m", "start": 18_068_000, "stop": 18_168_000, "mode": "usb", "step": 500, "bandwidth": 2_700, "button": False},
    {"name": "SW 15 m", "start": 21_000_000, "stop": 21_450_000, "mode": "usb", "step": 500, "bandwidth": 2_700},
    {"name": "SW 12 m", "start": 24_890_000, "stop": 24_990_000, "mode": "usb", "step": 500, "bandwidth": 2_700, "button": False},
    {"name": "CB", "start": 26_965_000, "stop": 27_405_000, "mode": "am", "step": 10_000, "bandwidth": 9_000},
    {"name": "SW 10 m", "start": 28_000_000, "stop": 29_700_000, "mode": "usb", "step": 500, "bandwidth": 2_700},
    {"name": "6 m", "start": 50_000_000, "stop": 52_000_000, "mode": "usb", "step": 500, "bandwidth": 2_700},
    {"name": "4 m", "start": 70_000_000, "stop": 70_500_000, "mode": "nfm", "step": 12_500, "bandwidth": 12_500, "button": False},
    {"name": "FM", "start": 87_500_000, "stop": 108_000_000, "mode": "wfm", "step": 100_000, "bandwidth": 180_000},
    {"name": "Air", "start": 118_000_000, "stop": 137_000_000, "mode": "am", "step": 8_333, "bandwidth": 8_333},
    {"name": "2 m", "start": 144_000_000, "stop": 146_000_000, "mode": "nfm", "step": 12_500, "bandwidth": 12_500},
    {"name": "Marine", "start": 156_000_000, "stop": 162_025_000, "mode": "nfm", "step": 25_000, "bandwidth": 16_000},
    {"name": "Mil air", "start": 225_000_000, "stop": 400_000_000, "mode": "am", "step": 25_000, "bandwidth": 9_000, "button": False},
    {"name": "70 cm", "start": 430_000_000, "stop": 440_000_000, "mode": "nfm", "step": 12_500, "bandwidth": 12_500},
    {"name": "LPD 433", "start": 433_075_000, "stop": 434_775_000, "mode": "nfm", "step": 25_000, "bandwidth": 12_500},
    {"name": "PMR", "start": 446_000_000, "stop": 446_200_000, "mode": "nfm", "step": 6_250, "bandwidth": 8_333},
    {"name": "SRD 868", "start": 868_000_000, "stop": 870_000_000, "mode": "nfm", "step": 25_000, "bandwidth": 12_500, "button": False},
    {"name": "23 cm", "start": 1_240_000_000, "stop": 1_300_000_000, "mode": "nfm", "step": 12_500, "bandwidth": 12_500},
    {"name": "13 cm", "start": 2_320_000_000, "stop": 2_450_000_000, "mode": "nfm", "step": 12_500, "bandwidth": 12_500},
    {"name": "ISM 2.4 GHz", "start": 2_400_000_000, "stop": 2_483_500_000, "mode": "wfm", "step": 1_000_000, "bandwidth": 200_000},
    {"name": "9 cm", "start": 3_400_000_000, "stop": 3_475_000_000, "mode": "nfm", "step": 12_500, "bandwidth": 12_500, "button": False},
    {"name": "6 cm", "start": 5_650_000_000, "stop": 5_850_000_000, "mode": "nfm", "step": 12_500, "bandwidth": 12_500, "button": False},
    {"name": "FPV 5.8 GHz", "start": 5_645_000_000, "stop": 5_945_000_000, "mode": "wfm", "step": 1_000_000, "bandwidth": 200_000},
]  # fmt: skip


def default_bandwidth(mode: str) -> int:
    return MODES.get(mode, MODES["nfm"])["bandwidth"]


def band_for(bands: list[dict], frequency: int) -> dict | None:
    """The narrowest band that holds this frequency."""
    matches = [b for b in bands if b["start"] <= frequency <= b["stop"]]
    return min(matches, key=lambda b: b["stop"] - b["start"]) if matches else None
