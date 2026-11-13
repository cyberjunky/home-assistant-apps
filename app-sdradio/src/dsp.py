"""Signal processing: from the receiver's IQ samples to audio and a spectrum.

Everything works block by block (a block is 1/20 s of samples) and keeps its state between
blocks, so the audio has no clicks at block edges.
"""

import math

import numpy as np
from scipy import signal

AUDIO_RATE = 48_000
WFM_RATE = 240_000
DB_FLOOR = -140.0
DB_CEIL = 0.0


class Decimator:
    """A low-pass FIR filter that keeps every `factor`-th output, computed polyphase.

    The filter has factor * k + 1 taps, so the samples kept from the previous block line
    up with the next output and the stream continues exactly across blocks.
    """

    def __init__(self, factor: int, cutoff: float, k: int = 4, dtype=np.complex64):
        self.factor = max(1, int(factor))
        self.taps = self.factor * k + 1
        # cutoff is a fraction of the input rate's Nyquist frequency
        self.h = signal.firwin(self.taps, min(max(cutoff, 0.001), 0.999), window=("kaiser", 7.0)).astype(np.float32)
        self.hist = np.zeros(self.taps - 1, dtype=dtype)

    def __call__(self, x: np.ndarray) -> np.ndarray:
        if self.factor == 1:
            return x
        ext = np.concatenate([self.hist, x])
        y = signal.upfirdn(self.h, ext, down=self.factor)
        first = (self.taps - 1) // self.factor
        last = (len(ext) - 1) // self.factor
        out = y[first : last + 1]
        start = (last + 1) * self.factor - (self.taps - 1)
        self.hist = ext[start:]
        return out.astype(ext.dtype, copy=False)


class Mixer:
    """Shifts a frequency offset to 0 Hz, with a continuous phase across blocks."""

    def __init__(self, rate: float):
        self.rate = rate
        self.offset = 0.0
        self.phase = 0.0
        self._table: tuple[float, int, np.ndarray] | None = None

    def __call__(self, x: np.ndarray) -> np.ndarray:
        if self.offset == 0:
            return x
        step = -2 * math.pi * self.offset / self.rate
        # The rotation for a whole block is the same every block: compute it once per offset
        if self._table is None or self._table[0] != step or self._table[1] != len(x):
            self._table = (step, len(x), np.exp(1j * step * np.arange(len(x))).astype(np.complex64))
        out = x * (self._table[2] * np.complex64(complex(math.cos(self.phase), math.sin(self.phase))))
        self.phase = float((self.phase + step * len(x)) % (2 * math.pi))
        return out


class StatefulFilter:
    """An FIR or IIR filter whose state carries over between blocks."""

    def __init__(self, b, a=(1.0,), dtype=np.complex64):
        self.b = np.asarray(b)
        self.a = np.asarray(a, dtype=np.float64)
        self.zi = np.zeros(max(len(self.a), len(self.b)) - 1, dtype=dtype)

    def __call__(self, x: np.ndarray) -> np.ndarray:
        y, self.zi = signal.lfilter(self.b, self.a, x, zi=self.zi)
        return y


def _bandpass(rate: float, low: float, high: float, taps: int = 129) -> np.ndarray:
    """A complex band-pass filter passing low..high Hz (negative for below the carrier)."""
    centre = (low + high) / 2
    half = max((high - low) / 2, 10.0)
    lowpass = signal.firwin(taps, min(half / (rate / 2), 0.99), window=("kaiser", 6.0))
    n = np.arange(taps) - (taps - 1) / 2
    return (lowpass * np.exp(2j * math.pi * centre / rate * n)).astype(np.complex64)


class AGC:
    """Smooth automatic gain: quick to turn down, slow to turn up."""

    def __init__(self, target: float = 0.3):
        self.target = target
        self.level = 0.0

    def __call__(self, audio: np.ndarray, enabled: bool = True) -> np.ndarray:
        if not enabled or len(audio) == 0:
            return audio * 4.0
        peak = float(np.percentile(np.abs(audio), 99)) + 1e-9
        self.level = peak if peak > self.level else self.level * 0.9 + peak * 0.1
        return audio * min(self.target / max(self.level, 1e-6), 10_000.0)


class Demodulator:
    """One receiver channel: picks the tuned signal out of the IQ block and demodulates it.

    rate is the receiver's sample rate, offset the tuned frequency minus the centre frequency.
    """

    def __init__(self, rate: float, mode: str, bandwidth: float):
        self.rate = float(rate)
        self.mode = mode
        self.bandwidth = float(bandwidth)
        self.mixer = Mixer(self.rate)
        self.agc_enabled = True
        self.noise_blanker = False
        self._setup()

    # -------------------------------------------------------------- setup

    def _setup(self) -> None:
        target = WFM_RATE if self.mode == "wfm" else AUDIO_RATE
        factor = max(1, round(self.rate / target))
        self.if_rate = self.rate / factor
        # Pass the widest signal this mode can have; the channel filter narrows it
        passband = min(self.bandwidth * 1.2, self.if_rate * 0.8) if self.mode == "wfm" else self.if_rate * 0.8
        self.decimate = Decimator(factor, passband / self.rate)
        self.agc = AGC()
        self._last = np.complex64(0)
        self._dc = 0.0
        half = self.bandwidth / 2
        self.rds = None
        self.tones = None
        if self.mode == "wfm":
            from .rds import RdsDecoder  # here: rds.py uses this module's filters

            self.rds = RdsDecoder(self.if_rate)
            self.audio_factor = max(1, round(self.if_rate / AUDIO_RATE))
            self.audio_rate = self.if_rate / self.audio_factor
            self.channel = None
            self.audio_decimate = Decimator(self.audio_factor, 15_000 / (self.if_rate / 2), k=8, dtype=np.float32)
            # 50 µs de-emphasis (Europe)
            tau = 50e-6
            alpha = 1 - math.exp(-1 / (self.if_rate * tau))
            self.deemphasis = StatefulFilter([alpha], [1, alpha - 1], dtype=np.float32)
            return
        self.audio_rate = self.if_rate
        self.audio_factor = 1
        if self.mode == "nfm":
            from .tones import ToneReader  # here: tones.py uses this module's filters

            self.tones = ToneReader(self.if_rate)
        self.cw_shift = Mixer(self.if_rate)
        self.cw_shift.offset = -700.0
        if self.mode == "usb":
            taps = _bandpass(self.if_rate, 200, max(self.bandwidth, 400))
        elif self.mode == "lsb":
            taps = _bandpass(self.if_rate, -max(self.bandwidth, 400), -200)
        elif self.mode == "cw":
            # The carrier is heard as a 700 Hz tone
            taps = _bandpass(self.if_rate, 700 - half, 700 + half, taps=257)
        else:
            taps = signal.firwin(129, min(half / (self.if_rate / 2), 0.99), window=("kaiser", 6.0)).astype(np.float32)
        self.channel = StatefulFilter(taps)
        self.audio_lowpass = StatefulFilter(
            signal.firwin(63, min(max(half, 3_000) / (self.if_rate / 2), 0.99)).astype(np.float32), dtype=np.float32
        )

    def configure(self, mode: str | None = None, bandwidth: float | None = None) -> None:
        changed = False
        if mode and mode != self.mode:
            self.mode, changed = mode, True
        if bandwidth and abs(bandwidth - self.bandwidth) > 0.5:
            self.bandwidth, changed = float(bandwidth), True
        if changed:
            self._setup()

    def set_offset(self, offset: float) -> None:
        self.mixer.offset = float(offset)

    # -------------------------------------------------------------- per block

    def process(self, iq: np.ndarray) -> tuple[np.ndarray, float]:
        """Returns (audio as float32 in about -1..1, signal level in dBFS)."""
        x = self.decimate(self.mixer(iq))
        if self.noise_blanker and len(x):
            magnitude = np.abs(x)
            x = np.where(magnitude > 6 * float(np.mean(magnitude)) + 1e-9, 0, x).astype(np.complex64)
        if self.mode == "cw":
            # Move the carrier up to 700 Hz before the filter
            x = self.cw_shift(x)
        if self.channel is not None:
            x = self.channel(x).astype(np.complex64)
        level = 10 * math.log10(float(np.mean(np.abs(x) ** 2)) + 1e-20) if len(x) else DB_FLOOR
        audio = self._demodulate(x)
        return audio.astype(np.float32), max(level, DB_FLOOR)

    def _fm(self, x: np.ndarray) -> np.ndarray:
        if len(x) == 0:
            return np.zeros(0, np.float32)
        prev = np.concatenate([[self._last], x[:-1]])
        self._last = x[-1]
        return np.angle(x * np.conj(prev)).astype(np.float32)

    def _demodulate(self, x: np.ndarray) -> np.ndarray:
        if self.mode == "wfm":
            # The multiplex: mono audio, the 19 kHz pilot, stereo and the 57 kHz RDS
            mpx = self._fm(x) * (self.if_rate / (2 * math.pi * 75_000))
            if self.rds is not None:
                self.rds.process(mpx)
            audio = self.deemphasis(mpx).astype(np.float32)
            return self.audio_decimate(audio) * 0.8
        if self.mode == "nfm":
            deviation = max(self.bandwidth / 4, 1_500)
            audio = self.audio_lowpass(self._fm(x) * (self.if_rate / (2 * math.pi * deviation)) * 0.5)
            if self.tones is not None:
                self.tones.process(audio)
            return audio
        if self.mode == "am":
            envelope = np.abs(x).astype(np.float32)
            mean = float(np.mean(envelope)) if len(envelope) else 0.0
            self._dc = mean if self._dc == 0 else self._dc * 0.8 + mean * 0.2
            audio = (
                (envelope - self._dc) / max(self._dc, 1e-9) * 0.5 if self.agc_enabled else (envelope - self._dc) * 20
            )
            return self.audio_lowpass(audio.astype(np.float32))
        # usb, lsb, cw, dsb: the real part of the filtered signal
        return self.agc(self.audio_lowpass(np.real(x).astype(np.float32)), self.agc_enabled)


class Spectrum:
    """An averaged power spectrum of a block, in dBFS, centre frequency in the middle."""

    def __init__(self, size: int = 2048):
        self.size = size
        self.window = signal.windows.blackmanharris(size).astype(np.float32)
        self.scale = 1.0 / (np.sum(self.window) ** 2)

    def __call__(self, iq: np.ndarray, segments: int = 8) -> np.ndarray:
        n = self.size
        if len(iq) < n:
            iq = np.concatenate([iq, np.zeros(n - len(iq), np.complex64)])
        starts = np.linspace(0, len(iq) - n, num=max(1, min(segments, len(iq) // n)), dtype=int)
        frames = np.stack([iq[s : s + n] for s in starts]) * self.window
        power = np.mean(np.abs(np.fft.fft(frames, axis=1)) ** 2, axis=0) * self.scale
        return np.fft.fftshift(10 * np.log10(power + 1e-20)).astype(np.float32)


def find_signal(power: np.ndarray, rate: float, offset: float, bandwidth: float, search: float,
                min_snr: float = 8.0) -> tuple[float, float] | None:  # fmt: skip
    """Autotune: the middle of the strongest signal within `search` Hz of `offset`, in an averaged
    power spectrum (linear, centre in the middle), and how far it is above the noise (dB).

    The spectrum is smoothed over a quarter of the channel width, so a broadcast FM station is one
    hump rather than its audio's peaks; the middle is halfway between where the hump has dropped
    10 dB on either side, so a station is centred on its whole width, not on its loudest bin.
    """
    size = len(power)
    bin_hz = rate / size
    width = max(1, round(bandwidth / 4 / bin_hz))
    smooth = np.convolve(power, np.ones(width) / width, mode="same")
    noise = float(np.median(power)) or 1e-20
    freqs = (np.arange(size) - size // 2) * bin_hz
    usable = (np.abs(freqs - offset) <= search) & (np.abs(freqs) < 0.45 * rate)
    if not usable.any():
        return None
    peak = int(np.flatnonzero(usable)[np.argmax(smooth[usable])])
    snr = 10 * math.log10(smooth[peak] / noise)
    if snr < min_snr:
        return None
    level = max(smooth[peak] / 10, noise * 2)
    reach = max(1, round(bandwidth / bin_hz))
    low = peak
    while low > max(0, peak - reach) and smooth[low - 1] >= level:
        low -= 1
    high = peak
    while high < min(size - 1, peak + reach) and smooth[high + 1] >= level:
        high += 1
    return float((freqs[low] + freqs[high]) / 2), snr


def quantize(db: np.ndarray) -> bytes:
    """dBFS to one byte per bin, DB_FLOOR..DB_CEIL as 0..255."""
    scaled = (np.clip(db, DB_FLOOR, DB_CEIL) - DB_FLOOR) * (255 / (DB_CEIL - DB_FLOOR))
    return scaled.astype(np.uint8).tobytes()


def noise_floor(db: np.ndarray) -> float:
    return float(np.median(db)) if len(db) else DB_FLOOR


def to_pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
