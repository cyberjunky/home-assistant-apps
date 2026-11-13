"""A stand-in Proxmox Backup Server for development: its REST API with two datastores and their jobs.

    python -m tools.mock_pbs --port 18007

Token: homeassistant@pbs!dashboard with secret "secret". Point a `pbs` entry at http://localhost:18007.
"""

import argparse
import math
import random
import time

from aiohttp import web

TOKEN = "PBSAPIToken=homeassistant@pbs!dashboard:secret"
TB = 1000**4
GiB = 1024**3


class MockPBS:
    def __init__(self, seed: int = 1):
        self.rng = random.Random(seed)
        self.started = time.time() - 46 * 86400
        now = time.time()
        day = now - now % 86400
        self.stores = {
            "main": {
                "total": 8 * TB,
                "used": 790 * GiB,
                "comment": "Nightly backups of the cluster",
                "growth": 4.6 * GiB,
            },
            "archive": {"total": 4 * TB, "used": 2.44 * TB, "comment": "USB disk, synced from main", "growth": 9 * GiB},
        }
        self.tasks: list[dict] = []
        self.logs: dict[str, list[str]] = {}
        # Two weeks of the jobs: backups from the cluster at 02:00, then prune, GC, sync and verify
        for d in range(14, -1, -1):
            base = day - d * 86400
            for vmid in (100, 101, 102, 103, 104, 105, 107, 200, 201, 202, 203, 204, 205, 206, 207, 208):
                kind = "vm" if vmid < 200 else "ct"
                if base + 7200 < now:
                    self._task("backup", f"main:{kind}/{vmid}", base + 7200 + vmid % 50 * 9, 20, "OK", "root@pam!pve")
            if base + 10800 < now:
                self._task("prunejob", "main:prune-daily", base + 10800, 4, "OK")
                self._task(
                    "garbage_collection",
                    "main",
                    base + 11000,
                    260,
                    "OK",
                    log=[
                        "starting garbage collection on store main",
                        "Start GC phase1 (mark used chunks)",
                        "Start GC phase2 (sweep unused chunks)",
                        "Removed garbage: 6.2 GiB",
                        "Removed chunks: 3121",
                        "Pending removals: 1.1 GiB (in 412 chunks)",
                        "Original data usage: 2.04 TiB",
                        "On-Disk usage: 790 GiB (37.81%)",
                        "On-Disk chunks: 412731",
                        "Deduplication factor: 2.64",
                        "Average chunk size: 2.0 MiB",
                        "TASK OK",
                    ],
                )
                self._task("syncjob", "main:archive:local-sync", base + 14400, 900, "OK")
            if d % 7 == 1 and base + 21600 < now:
                self._task("garbage_collection", "archive", base + 21600, 1900, "OK")
            if d % 7 == 0 and base + 18000 < now:
                bad = d == 0
                self._task(
                    "verificationjob",
                    "archive:v-weekly",
                    base + 18000,
                    5400,
                    "TASK ERROR: verification failed - please check the log for details" if bad else "OK",
                    log=[
                        "verify datastore archive",
                        "check vm/102/2026-11-08T01:00:12Z",
                        "can't verify chunk, load failed - store 'archive', unable to load chunk '9f3c…'",
                        "verify of vm/102/2026-11-08T01:00:12Z failed: 2 chunks could not be verified",
                        "Failed to verify the following snapshots/groups:",
                        "\tvm/102/2026-11-08T01:00:12Z",
                        "TASK ERROR: verification failed - please check the log for details",
                    ]
                    if bad
                    else None,
                )
            if base + 19000 < now:
                self._task("verificationjob", "main:v-daily", base + 19000, 620, "OK")
        self._task("reader", "main:vm/101", now - 2400, 140, "OK", "root@pam!pve")
        self.last = lambda kind, wid: next(
            (
                t
                for t in sorted(self.tasks, key=lambda t: -t["starttime"])
                if t["worker_type"] == kind and t["worker_id"] == wid
            ),
            None,
        )

    def _task(self, kind, worker_id, start, seconds, status, user="root@pam", log=None):
        start = int(start)
        upid = f"UPID:pbs:{len(self.tasks):08X}:{self.rng.randint(1, 99999):08X}:00000000:{start:08X}:{kind}:{worker_id.replace(':', chr(92) + 'x3a')}:{user}:"
        task = {
            "upid": upid,
            "node": "pbs",
            "worker_type": kind,
            "worker_id": worker_id,
            "user": user,
            "starttime": start,
            "endtime": start + int(seconds * self.rng.uniform(0.8, 1.2)),
            "status": status,
        }
        self.tasks.append(task)
        self.logs[upid] = log or [f"starting {kind} {worker_id}", "TASK OK" if status == "OK" else status]
        return upid

    def job_state(self, kind, wid, schedule, period):
        t = self.last(kind, wid)
        nxt = (time.time() // period + 1) * period + {"garbage_collection": 11000, "verificationjob": 18000}.get(
            kind, 10800
        ) % period
        if not t:
            return {"schedule": schedule, "next-run": int(nxt)}
        state = "ok" if t["status"] == "OK" else t["status"].replace("TASK ERROR: ", "")
        return {
            "schedule": schedule,
            "last-run-upid": t["upid"],
            "last-run-state": state,
            "last-run-endtime": t["endtime"],
            "next-run": int(nxt),
            "duration": t["endtime"] - t["starttime"],
        }

    def gc_status(self, store):
        s = self.stores[store]
        factor = 2.64 if store == "main" else 1.9
        return {
            "upid": None,
            "index-file-count": 2210 if store == "main" else 4120,
            "index-data-bytes": int(s["used"] * factor),
            "disk-bytes": int(s["used"]),
            "disk-chunks": int(s["used"] / (2 * 1024**2)),
            "removed-bytes": int(6.2 * GiB),
            "removed-chunks": 3121,
            "pending-bytes": int(1.1 * GiB),
            "pending-chunks": 412,
            "removed-bad": 0,
            "still-bad": 2 if store == "archive" else 0,
        }

    def usage(self, store):
        s = self.stores[store]
        start = (time.time() // 86400 - 30) * 86400
        history = [min(1.0, (s["used"] - (30 - i) * s["growth"]) / s["total"] + 0.002 * math.sin(i)) for i in range(31)]
        days_left = (s["total"] - s["used"]) / s["growth"]
        return {
            "store": store,
            "total": int(s["total"]),
            "used": int(s["used"]),
            "avail": int(s["total"] - s["used"]),
            "history": history,
            "history-start": int(start),
            "history-delta": 86400,
            "estimated-full-date": int(time.time() + days_left * 86400),
            "gc-status": self.gc_status(store),
        }


def build_app(pbs: MockPBS) -> web.Application:
    app = web.Application()

    @web.middleware
    async def auth(request, handler):
        if request.headers.get("Authorization") != TOKEN:
            return web.json_response({"data": None}, status=401, reason="authentication failure")
        try:
            result = await handler(request)
        except (KeyError, StopIteration):
            return web.json_response({"data": None}, status=400, reason="no such object")
        return result if isinstance(result, web.StreamResponse) else web.json_response({"data": result})

    app.middlewares.append(auth)
    P = "/api2/json"

    async def version(_):
        return {"version": "4.0.18", "release": "4.0", "repoid": "3f2b7c11"}

    async def status(_):
        return {
            "cpu": 0.04 + 0.03 * math.sin(time.time() / 300),
            "wait": 0.002,
            "loadavg": [0.31, 0.27, 0.22],
            "uptime": int(time.time() - pbs.started),
            "memory": {"total": 32 * GiB, "used": int(11.3 * GiB), "free": int(20.7 * GiB)},
            "swap": {"total": 8 * GiB, "used": 0, "free": 8 * GiB},
            "root": {"total": int(110 * GiB), "used": int(9.8 * GiB), "avail": int(100.2 * GiB)},
            "kversion": "Linux 6.14.11-4-pve #1 SMP PREEMPT_DYNAMIC PMX 6.14.11-4",
            "info": {"fingerprint": "AB:" * 31 + "CD"},
            "cpuinfo": {"model": "Intel(R) Xeon(R) E-2236 CPU @ 3.40GHz", "cpus": 12, "sockets": 1},
            "boot-info": {"mode": "efi", "secureboot": 0},
            "current-kernel": {"release": "6.14.11-4-pve", "machine": "x86_64"},
        }

    async def usage(_):
        return [pbs.usage(s) for s in pbs.stores]

    async def datastores(_):
        return [{"store": s, "comment": v["comment"]} for s, v in pbs.stores.items()]

    async def store_status(request):
        store = request.match_info["store"]
        u = pbs.usage(store)
        counts = (
            {
                "vm": {"groups": 8, "snapshots": 88},
                "ct": {"groups": 9, "snapshots": 99},
                "host": {"groups": 1, "snapshots": 14},
            }
            if store == "main"
            else {"vm": {"groups": 8, "snapshots": 232}, "ct": {"groups": 9, "snapshots": 261}}
        )
        return {
            "total": u["total"],
            "used": u["used"],
            "avail": u["avail"],
            "gc-status": pbs.gc_status(store),
            "counts": counts,
        }

    async def gc(_):
        return [
            {
                "store": s,
                **pbs.job_state(
                    "garbage_collection",
                    s,
                    "daily" if s == "main" else "sat 06:00",
                    86400 if s == "main" else 7 * 86400,
                ),
                **{k: v for k, v in pbs.gc_status(s).items() if k in ("removed-bytes", "pending-bytes")},
            }
            for s in pbs.stores
        ]

    async def verify(_):
        return [
            {
                "id": "v-daily",
                "store": "main",
                "ignore-verified": True,
                "outdated-after": 30,
                **pbs.job_state("verificationjob", "main:v-daily", "daily", 86400),
            },
            {
                "id": "v-weekly",
                "store": "archive",
                "ignore-verified": False,
                **pbs.job_state("verificationjob", "archive:v-weekly", "sun 05:00", 7 * 86400),
            },
        ]

    async def prune(_):
        return [
            {
                "id": "prune-daily",
                "store": "main",
                "keep-daily": 7,
                "keep-weekly": 4,
                "keep-monthly": 6,
                **pbs.job_state("prunejob", "main:prune-daily", "daily", 86400),
            }
        ]

    async def sync(_):
        return [
            {
                "id": "local-sync",
                "store": "archive",
                "remote-store": "main",
                "remove-vanished": False,
                "comment": "Copy to the USB disk",
                **pbs.job_state("syncjob", "main:archive:local-sync", "04:00", 86400),
            }
        ]

    async def tasks(request):
        limit = int(request.query.get("limit", 50))
        return sorted(pbs.tasks, key=lambda t: -t["starttime"])[:limit]

    async def task_status(request):
        t = next(t for t in pbs.tasks if t["upid"] == request.match_info["upid"])
        return {
            "upid": t["upid"],
            "type": t["worker_type"],
            "id": t["worker_id"],
            "user": t["user"],
            "starttime": t["starttime"],
            "status": "stopped",
            "exitstatus": "OK" if t["status"] == "OK" else t["status"].replace("TASK ERROR: ", ""),
        }

    async def task_log(request):
        return [{"n": i + 1, "t": line} for i, line in enumerate(pbs.logs[request.match_info["upid"]])]

    async def updates(_):
        return [
            {
                "Package": "proxmox-backup-server",
                "OldVersion": "4.0.18-1",
                "Version": "4.0.19-1",
                "Title": "Proxmox Backup Server daemon with tools and GUI",
                "Origin": "Proxmox",
            }
        ]

    async def subscription(_):
        return {"status": "notfound", "message": "There is no subscription key"}

    for method, path, handler in [
        ("GET", "/version", version),
        ("GET", "/nodes/localhost/status", status),
        ("GET", "/status/datastore-usage", usage),
        ("GET", "/admin/datastore", datastores),
        ("GET", "/admin/datastore/{store}/status", store_status),
        ("GET", "/admin/gc", gc),
        ("GET", "/admin/verify", verify),
        ("GET", "/admin/prune", prune),
        ("GET", "/admin/sync", sync),
        ("GET", "/nodes/localhost/tasks", tasks),
        ("GET", "/nodes/localhost/tasks/{upid}/status", task_status),
        ("GET", "/nodes/localhost/tasks/{upid}/log", task_log),
        ("GET", "/nodes/localhost/apt/update", updates),
        ("GET", "/nodes/localhost/subscription", subscription),
    ]:
        app.router.add_route(method, P + path, handler)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=18007)
    args = parser.parse_args()
    web.run_app(build_app(MockPBS()), port=args.port)


if __name__ == "__main__":
    main()
