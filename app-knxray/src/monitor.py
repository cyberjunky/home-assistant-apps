"""The live side of the bus: the last telegrams, and what was learned from all of them since the app
started — per group address and per sending device, who answers reads and who does not, how busy it is.
"""

import time
from collections import deque
from typing import Any

# A read that got no response within this many seconds went unanswered
READ_TIMEOUT = 3.0
# A group address repeating the same value this often in a row, each within REPEAT_WINDOW of the last
REPEAT_WINDOW = 2.0
LOAD_MINUTES = 120
# On twisted pair a telegram takes about 20 ms: some 50 a second fill a line
TP_TELEGRAMS_PER_SECOND = 50


def _ga_stats() -> dict[str, Any]:
    return {
        "count": 0,
        "writes": 0,
        "reads": 0,
        "responses": 0,
        "first": 0.0,
        "last": 0.0,
        "last_value": None,  # the last telegram that carried a value
        "sources": {},  # sender -> count
        "responders": {},  # who answered a read -> count
        "unanswered": 0,
        "answered": 0,
        "multi_answer": 0,
        "undecoded": 0,
        "sizes": {},  # payload size ('bits' or a number of bytes) -> count
        "repeats": 0,
        "resent": 0,  # frames sent again because nobody acknowledged them
        "peak": 0,  # the most telegrams in one minute
        "_minute": [0, 0],  # the minute being counted, and its count
        "_same": [None, 0.0],
    }


def _src_stats() -> dict[str, Any]:
    return {
        "count": 0,
        "first": 0.0,
        "last": 0.0,
        "addresses": {},
        "peak": 0,
        "resent": 0,
        "min_hops": 7,
        "_minute": [0, 0],
    }


class Monitor:
    def __init__(self, size: int = 20000):
        self.telegrams: deque[dict[str, Any]] = deque(maxlen=size)
        self.addresses: dict[str, dict[str, Any]] = {}
        self.sources: dict[str, dict[str, Any]] = {}
        self.total = 0
        self.since = time.time()
        self.secure_issues = 0
        self._next_id = 1
        # minute -> telegrams in it, and the busiest second of that minute
        self._load: dict[int, list[int]] = {}
        self._second = [0, 0]
        # group address -> [when asked, who asked, who answered]
        self._reads: dict[str, list[Any]] = {}
        # Somebody waiting for the next response on a group address (the Read button)
        self._waiters: dict[str, list[list[dict[str, Any]]]] = {}

    def seed(self, t: dict[str, Any]) -> None:
        """A telegram from the recording, shown in the list after a restart. It is not counted: what the
        checks say is about what was seen since the app started."""
        t["id"] = self._next_id
        self._next_id += 1
        self.telegrams.append(t)

    def add(self, t: dict[str, Any]) -> dict[str, Any]:
        """Take a telegram in: list it, count it, and note whether a read gets its answer."""
        t["id"] = self._next_id
        self._next_id += 1
        self.telegrams.append(t)
        self.total += 1
        ts, dst, src, kind = t["ts"], t["dst"], t["src"], t["type"]
        minute = int(ts // 60)

        load = self._load.setdefault(minute, [0, 0])
        load[0] += 1
        if self._second[0] != int(ts):
            self._second = [int(ts), 0]
        self._second[1] += 1
        load[1] = max(load[1], self._second[1])
        if len(self._load) > LOAD_MINUTES + 5:
            for old in sorted(self._load)[: len(self._load) - LOAD_MINUTES]:
                del self._load[old]

        ga = self.addresses.get(dst)
        if ga is None:
            ga = self.addresses[dst] = _ga_stats()
            ga["first"] = ts
        ga["count"] += 1
        ga["last"] = ts
        ga[{"write": "writes", "read": "reads", "response": "responses"}[kind]] += 1
        ga["sources"][src] = ga["sources"].get(src, 0) + 1
        self._count_minute(ga, minute)

        source = self.sources.get(src)
        if source is None:
            source = self.sources[src] = _src_stats()
            source["first"] = ts
        source["count"] += 1
        source["last"] = ts
        source["addresses"][dst] = source["addresses"].get(dst, 0) + 1
        if t.get("rep"):
            ga["resent"] += 1
            source["resent"] += 1
        if t.get("hops") is not None:
            source["min_hops"] = min(source["min_hops"], t["hops"])
        self._count_minute(source, minute)

        if kind != "read":
            size = "bits" if t.get("bits") else str(len(t.get("raw") or "") // 2)
            ga["sizes"][size] = ga["sizes"].get(size, 0) + 1
            ga["last_value"] = {k: t.get(k) for k in ("ts", "src", "raw", "text", "unit", "type", "bits", "guess")}
            if t.get("dpt") and t.get("text") is None:
                ga["undecoded"] += 1
            # The same value from the same sender again and again, in quick succession
            same = (src, t.get("raw"))
            if kind == "write" and ga["_same"][0] == same and ts - ga["_same"][1] < REPEAT_WINDOW:
                ga["repeats"] += 1
            ga["_same"] = [same, ts]

        if kind == "read":
            self._settle(dst, ts)
            self._reads[dst] = [ts, src, []]
        elif kind == "response":
            ga["responders"][src] = ga["responders"].get(src, 0) + 1
            pending = self._reads.get(dst)
            if pending is not None and src not in pending[2]:
                pending[2].append(src)
            for waiter in self._waiters.get(dst, []):
                waiter.append(t)
        return t

    @staticmethod
    def _count_minute(stats: dict[str, Any], minute: int) -> None:
        if stats["_minute"][0] != minute:
            stats["_minute"] = [minute, 0]
        stats["_minute"][1] += 1
        stats["peak"] = max(stats["peak"], stats["_minute"][1])

    def _settle(self, dst: str, now: float) -> None:
        """Close the read that was waiting on a group address: answered, by several, or not at all."""
        pending = self._reads.pop(dst, None)
        if pending is None:
            return
        ga = self.addresses[dst]
        if not pending[2]:
            ga["unanswered"] += 1
        else:
            ga["answered"] += 1
            if len(pending[2]) > 1:
                ga["multi_answer"] += 1

    def tick(self, now: float | None = None) -> None:
        """Called regularly: reads that waited long enough are settled."""
        now = time.time() if now is None else now
        for dst in [d for d, pending in self._reads.items() if now - pending[0] >= READ_TIMEOUT]:
            self._settle(dst, now)

    def wait_for(self, dst: str) -> list[dict[str, Any]]:
        """A list that fills with the responses on a group address from now on; give it back to done()."""
        waiter: list[dict[str, Any]] = []
        self._waiters.setdefault(dst, []).append(waiter)
        return waiter

    def done(self, dst: str, waiter: list[dict[str, Any]]) -> None:
        waiters = self._waiters.get(dst, [])
        if waiter in waiters:
            waiters.remove(waiter)
        if not waiters:
            self._waiters.pop(dst, None)

    def clear(self) -> None:
        """Forget what was counted: the statistics start again from now."""
        self.addresses.clear()
        self.sources.clear()
        self.telegrams.clear()
        self._load.clear()
        self._reads.clear()
        self.total = 0
        self.secure_issues = 0
        self.since = time.time()

    # ---- what the pages ask for ----

    def load(self, minutes: int = 60, now: float | None = None) -> list[list[int]]:
        """[minute start, telegrams, busiest second] for each of the last minutes, oldest first."""
        current = int((time.time() if now is None else now) // 60)
        return [[m * 60, *self._load.get(m, [0, 0])] for m in range(current - minutes + 1, current + 1)]

    def rate(self, now: float | None = None) -> float:
        """Telegrams a minute, over the last full minute and the one running."""
        now = time.time() if now is None else now
        current = int(now // 60)
        seconds = 60 + now % 60
        return (self._load.get(current, [0])[0] + self._load.get(current - 1, [0])[0]) / seconds * 60

    @staticmethod
    def public(stats: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in stats.items() if not k.startswith("_")}

    def recent(self, after: int = 0, limit: int = 2000) -> list[dict[str, Any]]:
        """The newest telegrams with an id above ``after``, oldest first."""
        out: list[dict[str, Any]] = []
        for t in reversed(self.telegrams):
            if t["id"] <= after or len(out) >= limit:
                break
            out.append(t)
        out.reverse()
        return out

    def of(self, address: str, limit: int = 100) -> list[dict[str, Any]]:
        """The newest telegrams to a group address or from a device, newest first."""
        key = "dst" if "/" in address else "src"
        out = []
        for t in reversed(self.telegrams):
            if t[key] == address:
                out.append(t)
                if len(out) >= limit:
                    break
        return out
