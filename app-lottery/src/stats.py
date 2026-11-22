"""The lottery numbers per miner and for all miners together, for the pages and the sensors."""

from .odds import (
    BLOCKS_PER_DAY,
    BLOCKS_PER_YEAR,
    TWO_32,
    block_chance,
    chance_within,
    expected_years,
    luck,
    seconds_to_share,
    share_chance,
)

DAY = 86400


def hashes_per_second(miner) -> float:
    """What the miner hashes now, in hashes a second: nothing while it's off or paused.

    The 10 minute average if the firmware has one, so the odds don't jump with every reading.
    """
    v = miner.values
    if not miner.connected or v.get("mining") is False:
        return 0.0
    rate = v.get("hashrate_10m") or v.get("hashrate") or 0
    return rate * 1e9


def session_work(miner) -> float:
    """The hashes done since the miner started: counted by the app, or worked out from its uptime and
    average hash rate when the session began before the app was watching."""
    v = miner.values
    average = v.get("hashrate_1d") or v.get("hashrate_1h") or v.get("hashrate_10m") or v.get("hashrate") or 0
    return max(miner.session_hashes, (v.get("uptime") or 0) * average * 1e9)


def odds_for(
    hashrate: float, difficulty: float | None, best: float | None, hashes: float, luck_best=None, luck_hashes=0.0
) -> dict:
    per_block = block_chance(hashrate, difficulty)
    return {
        "hashrate": hashrate,
        "block_chance": per_block,
        "day_chance": chance_within(per_block, BLOCKS_PER_DAY),
        "year_chance": chance_within(per_block, BLOCKS_PER_YEAR),
        "years": expected_years(hashrate, difficulty),
        # Best share against what a block needs
        "best_vs_network": best / difficulty * 100 if best and difficulty else None,
        # How long a share better than the best ever takes on average, and the chance of one today
        "beat_best_seconds": seconds_to_share(hashrate, best),
        "beat_best_today": share_chance(hashrate, best, DAY),
        # The work done, in blocks: at 1 a block would have been found on average
        "blocks_of_work": hashes / (difficulty * TWO_32) if difficulty and hashes else None,
        # This session's best share against the median best for its work: above 1 is lucky
        "luck": luck(luck_best, luck_hashes),
    }


def miner_odds(miner, difficulty: float | None) -> dict:
    return odds_for(
        hashes_per_second(miner),
        difficulty,
        miner.best_ever,
        miner.total_hashes,
        miner.values.get("best_session_diff"),
        session_work(miner),
    )


def totals(miners: list, difficulty: float | None) -> dict:
    rates = {m.slug: hashes_per_second(m) for m in miners}
    combined = sum(rates.values())
    bests = [m.best_ever for m in miners if m.best_ever]
    best = max(bests) if bests else None
    hashes = sum(m.total_hashes for m in miners)
    power = sum(m.values.get("power") or 0 for m in miners if m.connected)
    # Luck over all miners together: the best share of their sessions against all their work
    session_bests = [m.values.get("best_session_diff") for m in miners if m.values.get("best_session_diff")]
    out = odds_for(
        combined,
        difficulty,
        best,
        hashes,
        max(session_bests) if session_bests else None,
        sum(session_work(m) for m in miners),
    )
    out.update(
        {
            "best": best,
            "best_miner": next((m.slug for m in miners if best and m.best_ever == best), None),
            "power": power,
            "kwh_per_day": power * 24 / 1000,
            "energy_kwh": sum(m.energy_kwh for m in miners),
            "total_hashes": hashes,
            "mining": sum(1 for m in miners if rates[m.slug] > 0),
            "miners": len(miners),
            "efficiency": power / (combined / 1e12) if combined else None,
            "shares": {m.slug: rates[m.slug] / combined if combined else 0 for m in miners},
            "shares_accepted": sum(m.values.get("shares_accepted") or 0 for m in miners),
            "blocks_found": sum(m.values.get("blocks_found") or 0 for m in miners),
        }
    )
    return out
