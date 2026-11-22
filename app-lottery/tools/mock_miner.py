"""Stand-in miners for development: python3 -m tools.mock_miner [--port 18601]

Serves two miners on two ports: an AxeOS miner like the Lucky Miner LV08 (four BM1366, pauses and
resumes in place) on --port, and a NerdQAxe+ (four BM1368, shuts down and restarts, v2 dashboard with
per-chip hash rates) on --port + 1. The numbers wander like the real ones and shares beat the best now
and then. Pause, resume, shutdown and restart work.
"""

import argparse
import asyncio
import math
import random
import time

from aiohttp import web

NETWORK = 149.3e12


class Fake:
    def __init__(self, kind: str):
        self.kind = kind
        self.started = time.time() - random.randint(3600, 5 * 86400)
        self.paused = False
        self.down_until = 0.0
        self.best_ever = 2.87e9 if kind == "lv08" else 4.21e9
        self.best_session = self.best_of((time.time() - self.started) * self.nominal * 1e9)
        self.last_tick = time.time()
        self.accepted = random.randint(5000, 20000)
        self.rejected = 12

    @property
    def nominal(self) -> float:
        return 4500.0 if self.kind == "lv08" else 2500.0

    @staticmethod
    def best_of(hashes: float) -> float:
        """The best share of this many hashes: below d with a chance of exp(-hashes / (d x 2^32))."""
        return hashes / 2**32 / random.expovariate(1)

    def tick(self) -> None:
        now = time.time()
        if not self.paused:
            share = self.best_of((now - self.last_tick) * self.nominal * 1e9)
            self.best_session = max(self.best_session, share)
            self.best_ever = max(self.best_ever, share)
        self.last_tick = now
        if not self.paused:
            self.accepted += random.randint(0, 3)

    def info(self) -> dict:
        self.tick()
        t = time.time()
        running = not self.paused
        rate = self.nominal * (0.97 + 0.04 * math.sin(t / 300)) * (1 if running else 0)
        power = (120.0 if self.kind == "lv08" else 72.0) * (1 if running else 0.06)
        base = {
            "power": round(power + random.uniform(-1, 1), 2),
            "voltage": 12050 if self.kind == "lv08" else 11980,
            "current": round(power / 12 * 1000),
            "temp": round(58 + 6 * running + random.uniform(-0.5, 0.5), 1),
            "vrTemp": round(49 + 5 * running, 1),
            "hashRate": round(rate, 1),
            "hashRate_1m": round(rate * 0.99, 1),
            "hashRate_10m": round(self.nominal * 0.985 * running, 1),
            "hashRate_1h": round(self.nominal * 0.98, 1),
            "expectedHashrate": self.nominal,
            "bestDiff": self.best_ever,
            "bestSessionDiff": self.best_session,
            "poolDifficulty": 4096 if self.kind == "lv08" else 2048,
            "networkDifficulty": NETWORK,
            "blockHeight": 925114 + int((t - 1795300000) / 600),
            "sharesAccepted": self.accepted,
            "sharesRejected": self.rejected,
            "frequency": 525 if self.kind == "lv08" else 490,
            "coreVoltage": 1200 if self.kind == "lv08" else 1150,
            "coreVoltageActual": 1194,
            "fanspeed": 72 if self.kind == "lv08" else 55,
            "fanrpm": 5200 if self.kind == "lv08" else 3900,
            "autofanspeed": 1,
            "overheat_mode": 0,
            "errorPercentage": 0.42,
            "uptimeSeconds": int(t - self.started),
            "wifiRSSI": -58 if self.kind == "lv08" else -63,
            "freeHeap": 182000,
            "stratumURL": "solo.ckpool.org",
            "stratumPort": 3333,
            "stratumUser": "bc1qexample.worker",
            "fallbackStratumURL": "pool.nerdminers.org",
            "fallbackStratumPort": 3333,
            "asicTemps": [round(57 + i + 5 * running, 1) for i in range(4)],
            "macAddr": "AA:BB:CC:00:00:01" if self.kind == "lv08" else "AA:BB:CC:00:00:02",
            "hostname": self.kind,
        }
        if self.kind == "lv08":
            base.update(
                {
                    "ASICModel": "BM1366",
                    "asicCount": 4,
                    "smallCoreCount": 894,
                    "boardVersion": "LV08",
                    "version": "v2.4.2-lucky",
                    "miningPaused": self.paused,
                    "isUsingFallbackStratum": 0,
                    "blockFound": 0,
                    "lastpingrtt": 21.4,
                    "recentpingloss": 0.0,
                    "hashrateMonitor": {
                        "asics": [
                            {"total": round(rate / 4 + random.uniform(-15, 15), 1), "errorCount": 30 + i}
                            for i in range(4)
                        ]
                    },
                }
            )
        else:
            base.update(
                {
                    "ASICModel": "BM1368",
                    "deviceModel": "NerdQAxe+",
                    "asicCount": 4,
                    "version": "v1.0.31",
                    "shutdown": self.paused,
                    "foundBlocks": 0,
                    "totalFoundBlocks": 0,
                    "responseTime": 34.0,
                    "lastResetReason": "SYSTEM.RESET_POWERON",
                    "stratum": {"usingFallback": False, "pools": [{"connected": running}]},
                    "fanCount": 1,
                }
            )
        return base


def make_app(fake: Fake) -> web.Application:
    app = web.Application()

    def up() -> None:
        if time.time() < fake.down_until:
            raise web.HTTPServiceUnavailable()

    async def info(request):
        up()
        return web.json_response(fake.info())

    async def dashboard(request):
        up()
        if fake.kind != "nerdqaxe":
            raise web.HTTPNotFound()
        rate = 0 if fake.paused else fake.nominal / 4
        chips = [
            {"hashRate": round(rate + random.uniform(-10, 10), 1), "errorHashRate": round(random.uniform(0, 3), 2)}
            for _ in range(4)
        ]
        return web.json_response({"performance": {"chips": chips}})

    async def command(request):
        up()
        action = request.match_info["action"]
        if action == "pause" and fake.kind == "lv08":
            fake.paused = True
        elif action == "resume" and fake.kind == "lv08":
            fake.paused = False
        elif action == "shutdown" and fake.kind == "nerdqaxe":
            fake.paused = True
        elif action == "restart":
            fake.paused = False
            fake.started = time.time() + 8
            fake.best_session = 0
            fake.last_tick = time.time()
            fake.down_until = time.time() + 8
        else:
            raise web.HTTPNotFound()
        return web.json_response({"ok": True})

    app.router.add_get("/api/system/info", info)
    app.router.add_get("/api/v2/dashboard", dashboard)
    app.router.add_post("/api/system/{action}", command)
    return app


async def main(port: int) -> None:
    runners = []
    for offset, kind in enumerate(("lv08", "nerdqaxe")):
        runner = web.AppRunner(make_app(Fake(kind)), access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port + offset).start()
        runners.append(runner)
        print(f"{kind} on http://127.0.0.1:{port + offset}")
    await asyncio.Event().wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18601)
    asyncio.run(main(parser.parse_args().port))
