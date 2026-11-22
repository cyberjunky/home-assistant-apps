"""The lottery: what your hash rate buys against the Bitcoin network.

A share of difficulty d takes on average d x 2^32 hashes. A block needs a share of at least the
network difficulty D, and the network finds one every 600 seconds on average. So with a hash rate of
H hashes a second, each block is yours with a chance of H x 600 / (D x 2^32), and every block is a
new draw: the chance does not grow with time spent, it just adds up over more draws.
"""

import math

TWO_32 = 2**32
BLOCK_SECONDS = 600
BLOCKS_PER_DAY = 144
BLOCKS_PER_YEAR = 52_560
HALVING_INTERVAL = 210_000

# SI suffixes used by AxeOS to abbreviate difficulty values
SUFFIXES = ("", "K", "M", "G", "T", "P", "E")


def parse_difficulty(value: object) -> float | None:
    """A difficulty as a number. Older firmware reports them abbreviated, like "4.29G"."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if math.isfinite(value) else None
    if not isinstance(value, str) or not (text := value.strip()):
        return None
    exponent = 0
    if text[-1].upper() in SUFFIXES[1:]:
        exponent = SUFFIXES.index(text[-1].upper())
        text = text[:-1]
    try:
        number = float(text) * 1000.0**exponent
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def format_difficulty(value: float | None) -> str | None:
    """A difficulty abbreviated the way AxeOS shows it, e.g. 4.29G."""
    if value is None:
        return None
    exponent = 0
    while abs(value) >= 1000 and exponent < len(SUFFIXES) - 1:
        value /= 1000
        exponent += 1
    return f"{value:.2f}".rstrip("0").rstrip(".") + SUFFIXES[exponent]


def subsidy(height: int | None) -> float | None:
    """The new bitcoin in the block at this height, before fees."""
    if height is None or height < 0:
        return None
    halvings = height // HALVING_INTERVAL
    return 0.0 if halvings >= 64 else 50.0 / 2**halvings


def network_hashrate(difficulty: float | None) -> float | None:
    """The network's hash rate in hashes a second that this difficulty is set for."""
    if not difficulty or difficulty <= 0:
        return None
    return difficulty * TWO_32 / BLOCK_SECONDS


def block_chance(hashrate: float | None, difficulty: float | None) -> float | None:
    """The chance that the next block is yours, for a hash rate in hashes a second."""
    network = network_hashrate(difficulty)
    if network is None or hashrate is None or hashrate <= 0:
        return None
    return min(1.0, hashrate / network)


def chance_within(per_block: float | None, blocks: float) -> float | None:
    """The chance of at least one block in this many blocks."""
    if per_block is None:
        return None
    if per_block >= 1:
        return 1.0
    return -math.expm1(blocks * math.log1p(-per_block))


def one_in(chance: float | None) -> float | None:
    """A chance as the N of "1 in N"."""
    if not chance or chance <= 0:
        return None
    return 1 / chance


def expected_years(hashrate: float | None, difficulty: float | None) -> float | None:
    """How long it takes on average to find a block."""
    per_block = block_chance(hashrate, difficulty)
    if not per_block:
        return None
    return 1 / (per_block * BLOCKS_PER_YEAR)


def seconds_to_share(hashrate: float | None, difficulty: float | None) -> float | None:
    """How long it takes on average to find a share of at least this difficulty."""
    if not hashrate or hashrate <= 0 or not difficulty or difficulty <= 0:
        return None
    return difficulty * TWO_32 / hashrate


def share_chance(hashrate: float | None, difficulty: float | None, seconds: float) -> float | None:
    """The chance of a share of at least this difficulty within this many seconds."""
    mean = seconds_to_share(hashrate, difficulty)
    if mean is None:
        return None
    return -math.expm1(-seconds / mean)


def typical_best(hashes: float | None) -> float | None:
    """The median best share after this many hashes.

    The best of the hashes is at least d with a chance of 1 - exp(-hashes / (d x 2^32)); that is
    one half at d = hashes / (2^32 x ln 2).
    """
    if not hashes or hashes <= 0:
        return None
    return hashes / (TWO_32 * math.log(2))


def luck(best: float | None, hashes: float | None) -> float | None:
    """The best share found against the median best for the work done: above 1 is lucky."""
    typical = typical_best(hashes)
    if not typical or best is None:
        return None
    return best / typical
