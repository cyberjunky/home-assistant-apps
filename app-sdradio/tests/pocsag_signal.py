"""A POCSAG transmitter (ITU-R M.584), for testing the Pager mode against multimon-ng."""

import math

import numpy as np

SYNC = 0x7CD215D8
IDLE = 0x7A89C197
# BCH(31,21) generator: x^10 + x^9 + x^8 + x^6 + x^5 + x^3 + 1
GENERATOR = 0x769


def codeword(data21: int) -> int:
    """21 bits of data, then the 10 BCH check bits and an even parity bit."""
    remainder = data21 << 10
    for bit in range(30, 9, -1):
        if remainder >> bit & 1:
            remainder ^= GENERATOR << (bit - 10)
    word = (data21 << 11) | (remainder << 1)
    return word | (bin(word).count("1") & 1)


def message_words(address: int, function: int, text: str) -> list[int]:
    """The codewords after the preamble: batches of a sync word and 16 codewords."""
    words = [IDLE] * (2 * (address & 7))
    words.append(codeword(((address >> 3) << 2) | function))
    # Alpha: 7-bit characters, least significant bit first, 20 bits a codeword
    bits = [(ord(c) >> i) & 1 for c in text + "\x04" for i in range(7)]
    bits += [0] * (-len(bits) % 20)
    for i in range(0, len(bits), 20):
        chunk = 0
        for b in bits[i : i + 20]:
            chunk = (chunk << 1) | b
        words.append(codeword((1 << 20) | chunk))
    words += [IDLE] * (-len(words) % 16)
    out = []
    for i in range(0, len(words), 16):
        out += [SYNC, *words[i : i + 16]]
    return out


def bits(address: int, function: int, text: str, preamble: int = 576) -> np.ndarray:
    out = [1 - i % 2 for i in range(preamble)]
    for word in message_words(address, function, text):
        out += [(word >> (31 - i)) & 1 for i in range(32)]
    return np.array(out, dtype=np.int8)


def fm_iq(data: np.ndarray, baud: int, rate: float, offset: float, deviation: float = 4_500,
          lead: float = 0.3) -> np.ndarray:  # fmt: skip
    """The bits as 2-FSK (a 1 is the lower frequency, as POCSAG sends it), as IQ samples."""
    samples_per_bit = rate / baud
    n = int(len(data) * samples_per_bit)
    index = (np.arange(n) / samples_per_bit).astype(int)
    level = np.where(data[index] == 1, -1.0, 1.0)
    quiet = np.zeros(int(lead * rate))
    level = np.concatenate([quiet, level, quiet])
    phase = 2 * math.pi * np.cumsum(offset + deviation * level) / rate
    return (np.exp(1j * phase) * 0.1).astype(np.complex64)
