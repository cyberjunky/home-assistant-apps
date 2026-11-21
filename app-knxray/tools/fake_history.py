"""Days of recorded telegrams from the made-up installation, so the archive, the charts and the statistics
have something to show right away."""

import time
from pathlib import Path

from src.recorder import Recorder
from src.telegrams import decode, dpt_text
from tools import installation as house


def write_history(path: Path, days: float) -> int:
    recorder = Recorder(path, retain_days=0, retain_rows=0)
    simulation = house.Simulation(seed=3, pace=30)
    now = time.time()
    start = now - days * 86400
    count = 0
    events = [(start, "start"), (start + 5, "connected")]
    # A lost link and a night the app did not run, for the availability report
    if days >= 1:
        events += [(now - 0.6 * 86400, "disconnected"), (now - 0.6 * 86400 + 1500, "connected")]
    if days >= 3:
        events += [
            (now - 2.5 * 86400, "stop"),
            (now - 2.5 * 86400 + 5 * 3600, "start"),
            (now - 2.5 * 86400 + 5 * 3600 + 4, "connected"),
        ]
    events.sort()
    down: list[tuple[float, float]] = []
    opened = None
    for ts, kind in events:
        if kind in ("disconnected", "stop") and opened is None:
            opened = ts
        elif kind == "connected" and opened is not None:
            down.append((opened, ts))
            opened = None
    ts = start
    while ts < now - 2:
        ts += 1.0
        if any(a <= ts <= b for a, b in down):
            continue
        hour = time.localtime(ts).tm_hour
        for source, address, kind, payload in simulation.step(ts):
            # A quiet house at night: nobody presses buttons
            if hour < 6 and source in ("1.1.10", "1.1.11", "1.2.11", "1.1.21", house.HA_ADDRESS):
                continue
            main, sub = house.GROUPS.get(address, ("", None, None))[1:]
            dpt = dpt_text(main, sub)
            bits = isinstance(payload, int)
            raw = "" if payload is None else f"{payload:02x}" if bits else bytes(payload).hex()
            text, num, unit = decode(dpt, raw, bits) or (None, None, None)
            recorder.add(
                {
                    "ts": ts,
                    "dir": "out" if source == house.HA_ADDRESS else "in",
                    "src": source,
                    "dst": address,
                    "type": kind,
                    "raw": raw,
                    "text": text,
                    "num": num,
                    "unit": unit,
                    "dpt": dpt,
                }
            )
            count += 1
        if count % 20000 < 3:
            recorder.flush()
    recorder.flush()
    with recorder._lock:
        recorder._db.executemany("INSERT INTO events (ts, kind) VALUES (?, ?)", events)
        recorder._db.execute("INSERT INTO events (ts, kind) VALUES (?, 'stop')", (now - 1,))
        recorder._db.close()
    return count
