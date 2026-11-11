"""Stream frames must not turn into an API call each (TeslaMate #5920/#5924), and a 429's wait is kept."""

import asyncio
import itertools
import time

from src.config import Config
from src.tesla_api import RateLimited

from .conftest import FakeApi, make_monitor, vehicle_data


async def _run_for(monitor, seconds: float, frame) -> None:
    """Run the monitor's loop while `frame()` is called every 20 ms (the stream at full speed)."""
    task = asyncio.create_task(monitor.run())
    end = time.time() + seconds
    try:
        while time.time() < end:
            frame()
            await asyncio.sleep(0.02)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_gearless_frames_during_a_drive_are_throttled(database):
    api = FakeApi(lambda now: vehicle_data(now, shift="D"))
    monitor = make_monitor(database, api, Config(streaming=False, poll_driving=1))
    frame = {"ts": 0, "lat": 52.1, "lon": 5.1, "shift": None}

    async def scenario():
        await asyncio.sleep(0)
        await _run_for(monitor, 3.0, lambda: monitor._on_stream_sample({**frame, "ts": int(time.time())}))

    asyncio.run(scenario())
    reads = [ts for kind, ts in api.calls if kind == "vehicle_data"]
    assert monitor.drive_id is not None
    # 150 frames in 3 s; at most one read per driving interval (1 s), plus the first one
    assert len(reads) <= 5, len(reads)
    assert all(b - a >= 0.95 for a, b in itertools.pairwise(reads))


def test_no_request_before_the_rate_limit_ends(database):
    api = FakeApi(lambda now: vehicle_data(now))
    api.fail_with = [RateLimited(2)]
    monitor = make_monitor(database, api, Config(streaming=False))

    asyncio.run(_run_for(monitor, 3.0, monitor.poke))
    limited_at = api.calls[1][1]  # the summary, then the read that got the 429
    later = [ts for _, ts in api.calls[2:]]
    assert later, "it asks again after the wait"
    assert min(later) - limited_at >= 1.95, later


def test_rate_limit_also_holds_stream_wakeups_while_driving(database):
    api = FakeApi(lambda now: vehicle_data(now, shift="D"))
    monitor = make_monitor(database, api, Config(streaming=False, poll_driving=1))

    async def scenario():
        task = asyncio.create_task(monitor.run())
        await asyncio.sleep(0.3)
        assert monitor.drive_id is not None
        api.fail_with = [RateLimited(2)]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        api.calls.clear()
        await _run_for(monitor, 3.5, lambda: monitor._on_stream_sample({"ts": 0, "lat": None, "shift": "P"}))

    asyncio.run(scenario())
    first = api.calls[0][1]
    assert api.calls[1][1] - first >= 1.95, api.calls
