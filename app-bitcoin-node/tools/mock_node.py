"""A stand-in Bitcoin node for development: Bitcoin Core's JSON-RPC, LND's REST API and Tor's
control port, with a chain, a wallet and peers that only exist here.

    python3 -m tools.mock_node                      # RPC on 18443, LND on 18080, Tor control on 19051
    python3 -m tools.mock_node --fast               # a block every 40 seconds or so
    python3 -m tools.mock_node --seed-db /tmp/bn/bitcoin-node.db   # also 30 days of history for the charts

RPC user "homeassistant", password "secret". LND takes any macaroon, over plain http. Tor's control
password is "torpass". The numbers move while it runs: the mempool, the peers, a new block, a
forwarded payment.
"""

import argparse
import asyncio
import base64
import contextlib
import itertools
import json
import math
import random
import sys
import time
from pathlib import Path

from aiohttp import web

RPC_USER, RPC_PASSWORD, TOR_PASSWORD = "homeassistant", "secret", "torpass"
STEP = 300
DAYS = 30
RETARGET = 2016
B32 = "abcdefghijklmnopqrstuvwxyz234567"
HEX = "0123456789abcdef"
ALIASES = [
    "northwind-routing",
    "aurora.ln",
    "tulip-node",
    "satoshi-diner",
    "polder-lightning",
    "kiwi-hub",
    "zebra-relay",
    "harbour-sats",
]
CLIENTS = [
    ("/Satoshi:32.1.0/", 34),
    ("/Satoshi:32.0.0/", 18),
    ("/Satoshi:31.1.0/", 16),
    ("/Satoshi:30.2.0/", 12),
    ("/Satoshi:29.2.0/", 8),
    ("/Satoshi:28.3.0/", 4),
    ("/Satoshi:29.2.0/Knots:20260210/", 8),
]
FEE_BANDS = (0.1, 1, 2, 3, 4, 5, 6, 8, 10, 15, 20, 30, 50, 100, 500)
MEMOS = ("coffee", "podcast boost", "test invoice", "sats back from a friend", "donation")
RELAYS = [
    "Quintex",
    "NTH",
    "relayon",
    "artikel10",
    "DigiGesTor",
    "hviv",
    "bauruine",
    "Unnamed",
    "ForPrivacyNET",
    "TorNodeNL",
]
PURPOSES = (
    ["GENERAL"] * 4 + ["HS_SERVICE_INTRO"] * 6 + ["HS_SERVICE_REND"] * 5 + ["HS_CLIENT_REND"] * 4 + ["HS_VANGUARDS"] * 3
)


class MockNode:
    def __init__(self, seed: int, fast: bool, syncing: bool = False):
        self.rng = random.Random(seed)
        # A node early in its first sync: an old tip, no mempool, the headers far ahead
        self.syncing = syncing
        self.block_mean = 40 if fast else 600
        now = time.time()
        self.started = now - 23 * 86400 - 5 * 3600
        self.clock = now
        self._init_history(now)
        self._init_blocks(now)
        self._init_peers(now)
        self._init_lightning(now)
        self._init_tor(now)
        self.next_block_at = now + self.rng.expovariate(1 / self.block_mean)
        self.next_peer_change = now + self.rng.uniform(20, 90)
        self.next_payment = now + self.rng.uniform(30, 120)

    def hexs(self, n: int) -> str:
        return "".join(self.rng.choice(HEX) for _ in range(n))

    def onion(self) -> str:
        return "".join(self.rng.choice(B32) for _ in range(56)) + ".onion"

    # ------------------------------------------------------------------ history
    def _sample(self, t: float, prev: dict | None) -> dict:
        rng = self.rng
        hour = (t % 86400) / 3600
        weekday = int(t // 86400 + 4) % 7
        target = 16 + 12 * math.sin((hour - 9) / 24 * 2 * math.pi) - (5 if weekday >= 5 else 0)
        x = prev["_x"] if prev else target
        x += 0.03 * (target - x) + rng.gauss(0, 0.9)
        if rng.random() < 0.0015:
            x += rng.uniform(25, 70)  # a burst of transactions
        x = max(0.6, x)
        fee_next = max(0.5, 0.6 + 2.6 * (x / 12) ** 1.55 + rng.gauss(0, 0.25))
        peers_in = int(min(46, max(9, (prev["peers_in"] if prev else 24) + rng.choice((-1, 0, 0, 0, 0, 1)))))
        burst = rng.random() < 0.02
        recv = max(4, 55 + 30 * math.sin(hour / 24 * 2 * math.pi) + rng.gauss(0, 14) + (220 if burst else 0))
        sent = max(6, 140 + 60 * math.sin((hour - 3) / 24 * 2 * math.pi) + rng.gauss(0, 30) + (600 if burst else 0))
        ln_local = prev["ln_local"] if prev else 4_150_000
        ln_remote = prev["ln_remote"] if prev else 5_420_000
        if rng.random() < 0.04:
            shift = int(rng.uniform(-90_000, 90_000))
            shift = max(-ln_local + 400_000, min(ln_remote - 400_000, shift))
            ln_local, ln_remote = ln_local + shift, ln_remote - shift
        return {
            "_x": x,
            "mempool_vsize": round(x, 2),
            "mempool_tx": int(x * 2550),
            "fee_next": round(fee_next, 1),
            "fee_hour": round(max(0.4, fee_next * 0.68), 1),
            "fee_day": round(max(0.2, fee_next * 0.3), 1),
            "peers_in": peers_in,
            "peers_out": 10,
            "recv_rate": round(recv, 1),
            "sent_rate": round(sent, 1),
            "ln_local": ln_local,
            "ln_remote": ln_remote,
            "ln_onchain": prev["ln_onchain"] if prev else 1_284_500,
            "tor_read_rate": round(recv * 1.06, 1),
            "tor_written_rate": round(sent * 1.06, 1),
        }

    def _init_history(self, now: float) -> None:
        self.ts: list[int] = []
        self.samples: list[dict] = []
        t = int(now - DAYS * 86400) // STEP * STEP
        deposit = int(now - 12 * 86400) // STEP * STEP
        prev = None
        while t <= now:
            prev = self._sample(t, prev)
            if t == deposit:
                prev["ln_onchain"] += 750_000  # a deposit, twelve days ago
            self.ts.append(t)
            self.samples.append(prev)
            t += STEP

    def _at(self, t: float) -> dict:
        i = int((t - self.ts[0]) // STEP)
        return self.samples[max(0, min(len(self.samples) - 1, i))]

    # ------------------------------------------------------------------ blocks
    def _block(self, height: int, t: float) -> dict:
        rng = self.rng
        median = round(max(0.3, self._at(t)["fee_next"] * rng.uniform(0.75, 1.1)), 1)
        weight = int(rng.uniform(3_980_000, 3_998_000)) if rng.random() > 0.08 else int(rng.uniform(900_000, 3_600_000))
        low = round(max(0.1, median * rng.uniform(0.35, 0.7)), 1)
        high = round(median * rng.uniform(6, 40))
        return {
            "height": height,
            "blockhash": "0" * 20 + self.hexs(44),
            "time": int(t),
            "txs": int(weight / 4_000_000 * rng.uniform(2300, 4600)),
            "total_size": int(weight / rng.uniform(2.1, 2.9)),
            "total_weight": weight,
            "totalfee": int(weight / 4 * median * rng.uniform(1.15, 1.7)),
            "subsidy": 50 * 100_000_000 >> (height // 210_000),
            "feerate_percentiles": [
                low,
                round((low + median) / 2, 1),
                median,
                round(median * 1.6, 1),
                round(median * 3, 1),
            ],
            "minfeerate": low,
            "maxfeerate": high,
        }

    def _init_blocks(self, now: float) -> None:
        tip = int(840_000 + (now - 1_713_571_767) / 598)
        times = [now - self.rng.uniform(40, 700)]
        for _ in range(159):
            times.append(times[-1] - max(15, self.rng.expovariate(1 / 600)))
        self.blocks = {tip - i: self._block(tip - i, times[i]) for i in range(160)}
        self.tip = tip
        done = tip % RETARGET
        self.epoch_time = int(self.blocks[tip]["time"] - done * self.rng.uniform(585, 612))

    # ------------------------------------------------------------------ peers
    def _peer(self, now: float, inbound: bool, block_relay: bool = False) -> dict:
        rng = self.rng
        self.peer_id += 1
        age = rng.uniform(600, 9 * 86400)
        return {
            "id": self.peer_id,
            "addr": f"127.0.0.1:{rng.randint(32000, 60999)}" if inbound else f"{self.onion()}:8333",
            "network": "onion",
            "inbound": inbound,
            "connection_type": "inbound" if inbound else "block-relay-only" if block_relay else "outbound-full-relay",
            "subver": rng.choices([c[0] for c in CLIENTS], [c[1] for c in CLIENTS])[0],
            "version": 70016,
            "transport_protocol_type": "v2" if rng.random() < 0.8 else "v1",
            "pingtime": round(rng.uniform(0.18, 1.4), 3),
            "bytessent": int(age * rng.uniform(300, 9000)),
            "bytesrecv": int(age * rng.uniform(200, 4000)),
            "conntime": int(now - age),
        }

    def _init_peers(self, now: float) -> None:
        self.peer_id = 100
        self.peers = [self._peer(now, False) for _ in range(8)] + [self._peer(now, False, True) for _ in range(2)]
        self.peers += [self._peer(now, True) for _ in range(self.samples[-1]["peers_in"])]
        self.p2p_onion = self.onion()
        self.recv_total = 41.3e9
        self.sent_total = 118.6e9

    # ------------------------------------------------------------------ lightning
    def _init_lightning(self, now: float) -> None:
        rng = self.rng
        local = self.samples[-1]["ln_local"]
        capacities = [3_000_000, 2_000_000, 1_500_000, 1_000_000, 1_000_000, 600_000, 500_000]
        shares = [0.62, 0.21, 0.48, 0.09, 0.71, 0.55, 0.34]
        scale = local / sum(c * s for c, s in zip(capacities, shares, strict=True))
        self.channels = []
        for i, (cap, share) in enumerate(zip(capacities, shares, strict=True)):
            usable = cap - 2_000
            loc = int(min(usable - 5_000, usable * share * scale))
            opened = self.tip - int(rng.uniform(20, 400) * 144)
            lifetime = int(rng.uniform(3, 20) * 86400)
            self.channels.append(
                {
                    "active": i != 5,
                    "remote_pubkey": rng.choice(("02", "03")) + self.hexs(64),
                    "channel_point": f"{self.hexs(64)}:{rng.randint(0, 1)}",
                    "chan_id": str((opened << 40) | (rng.randint(1, 3000) << 16) | rng.randint(0, 1)),
                    "capacity": str(cap),
                    "local_balance": str(loc),
                    "remote_balance": str(usable - loc),
                    "unsettled_balance": "12500" if i == 0 else "0",
                    "total_satoshis_sent": str(int(rng.uniform(0.2, 6) * cap)),
                    "total_satoshis_received": str(int(rng.uniform(0.2, 6) * cap)),
                    "private": i == 6,
                    "initiator": i % 3 != 1,
                    "lifetime": str(lifetime),
                    "uptime": str(int(lifetime * (rng.uniform(0.93, 1.0) if i != 5 else 0.41))),
                    "peer_alias": ALIASES[i],
                    "_ppm": rng.choice((50, 120, 250, 400)),
                }
            )
        self.pending_open = [
            {
                "channel": {
                    "remote_node_pub": "02" + self.hexs(64),
                    "channel_point": f"{self.hexs(64)}:0",
                    "capacity": "2000000",
                    "local_balance": "1998000",
                    "remote_balance": "0",
                }
            }
        ]
        self.graph = {self.pending_open[0]["channel"]["remote_node_pub"]: ALIASES[7]}
        self.ln_pubkey = "03" + self.hexs(64)
        self.ln_onion = self.onion()
        self.forwards: list[dict] = []
        self.payments: list[dict] = []
        self.invoices: list[dict] = []
        t = now - DAYS * 86400
        while t < now - 600:
            t += rng.expovariate(1 / 7500)
            if t < now - 600:
                self._forward(t)
        for _ in range(9):
            self._payment(now - rng.uniform(1200, 6 * 86400))
        for _ in range(9):
            self._invoice(now - rng.uniform(1200, 6 * 86400))
        self.payments.sort(key=lambda p: int(p["creation_date"]))
        self.invoices.sort(key=lambda i: int(i["settle_date"]))
        for n, p in enumerate(self.payments, 1):
            p["payment_index"] = str(n)
        for n, i in enumerate(self.invoices, 1):
            i["settle_index"] = str(n)

    def _forward(self, t: float) -> None:
        rng = self.rng
        a, b = rng.sample([c for c in self.channels if c["active"]], 2)
        amount = int(rng.uniform(8_000, 320_000))
        fee_msat = int(amount * rng.uniform(90, 260) / 1000) + 1000
        self.forwards.append(
            {
                "timestamp": str(int(t)),
                "timestamp_ns": str(int(t * 1e9)),
                "chan_id_in": a["chan_id"],
                "chan_id_out": b["chan_id"],
                "amt_in": str(amount + fee_msat // 1000),
                "amt_out": str(amount),
                "fee": str(fee_msat // 1000),
                "fee_msat": str(fee_msat),
                "peer_alias_in": a["peer_alias"],
                "peer_alias_out": b["peer_alias"],
            }
        )

    def _payment(self, t: float) -> None:
        rng = self.rng
        amount = rng.choice((1_000, 2_100, 5_000, 21_000, 50_000, 150_000))
        channel = rng.choice([c for c in self.channels if c["active"]])
        fee_msat = int(amount * 0.4) + 1000
        self.payments.append(
            {
                "payment_index": str(len(self.payments) + 1),
                "payment_hash": self.hexs(64),
                "value_sat": str(amount),
                "fee_sat": str(fee_msat // 1000),
                "fee_msat": str(fee_msat),
                "creation_date": str(int(t)),
                "creation_time_ns": str(int(t * 1e9)),
                "status": "SUCCEEDED",
                "htlcs": [
                    {"status": "SUCCEEDED", "route": {"hops": [{"chan_id": channel["chan_id"]}, {"chan_id": "1"}]}}
                ],
            }
        )

    def _invoice(self, t: float) -> None:
        rng = self.rng
        amount = rng.choice((1_000, 2_100, 5_000, 21_000, 50_000, 150_000))
        self.invoices.append(
            {
                "memo": rng.choice(MEMOS),
                "value": str(amount),
                "amt_paid_sat": str(amount),
                "state": "SETTLED",
                "settled": True,
                "creation_date": str(int(t - 60)),
                "settle_date": str(int(t)),
                "settle_index": str(len(self.invoices) + 1),
            }
        )

    # ------------------------------------------------------------------ tor
    def _circuit(self, now: float, number: int, purpose: str) -> dict:
        rng = self.rng
        hops = 4 if purpose.startswith("HS_") and purpose != "HS_VANGUARDS" else 3
        return {
            "id": number,
            "status": "BUILT" if rng.random() > 0.06 else "EXTENDED",
            "purpose": purpose,
            "path": [(self.hexs(40).upper(), f"{rng.choice(RELAYS)}{rng.randint(1, 60)}") for _ in range(hops)],
            "created": now - rng.uniform(30, 3400),
        }

    def _init_tor(self, now: float) -> None:
        self.circuits = [self._circuit(now, 400 + i * 3, purpose) for i, purpose in enumerate(PURPOSES)]
        self.tor_read = 44.9e9
        self.tor_written = 126.2e9

    # ------------------------------------------------------------------ live
    def tick(self) -> None:
        now = time.time()
        rng = self.rng
        elapsed, self.clock = now - self.clock, now
        while self.ts[-1] + STEP <= now:
            self.ts.append(self.ts[-1] + STEP)
            self.samples.append(self._sample(self.ts[-1], self.samples[-1]))
        s = self.samples[-1]
        while now >= self.next_block_at:
            self.tip += 1
            self.blocks[self.tip] = self._block(self.tip, self.next_block_at)
            self.blocks.pop(self.tip - 200, None)
            s["_x"] = max(0.6, s["_x"] - 1.0)
            if rng.random() < 0.3:
                self.pending_open.clear()  # the channel being opened got its confirmations
            self.next_block_at += max(8, rng.expovariate(1 / self.block_mean))
        if now >= self.next_peer_change:
            inbound = [p for p in self.peers if p["inbound"]]
            if len(inbound) > 12 and rng.random() < 0.5:
                self.peers.remove(rng.choice(inbound))
            else:
                peer = self._peer(now, True)
                peer.update(conntime=int(now), bytessent=2400, bytesrecv=1900)
                self.peers.append(peer)
            self.next_peer_change = now + rng.uniform(25, 120)
        if now >= self.next_payment:
            kind = rng.choices(("forward", "payment", "invoice"), (7, 2, 2))[0]
            {"forward": self._forward, "payment": self._payment, "invoice": self._invoice}[kind](now)
            self.next_payment = now + rng.uniform(40, 240)
        for p in self.peers:
            p["bytessent"] += int(rng.uniform(0, 6000) * elapsed)
            p["bytesrecv"] += int(rng.uniform(0, 2400) * elapsed)
            p["pingtime"] = round(max(0.12, p["pingtime"] + rng.gauss(0, 0.02)), 3)
        self.recv_total += s["recv_rate"] * 1000 * elapsed
        self.sent_total += s["sent_rate"] * 1000 * elapsed
        self.tor_read += s["tor_read_rate"] * 1000 * elapsed
        self.tor_written += s["tor_written_rate"] * 1000 * elapsed

    def vsize(self) -> float:
        now = time.time()
        wobble = math.sin(now / 7) * 0.5 + math.sin(now / 3.1) * 0.3
        return max(0.5, self.samples[-1]["_x"] + wobble * 0.15)

    def histogram(self) -> list[tuple[float, float, float]]:
        """Virtual megabytes waiting per fee band: most of it sits below the fee of the next block."""
        fee_next, weights = self.samples[-1]["fee_next"], []
        for lo, hi in itertools.pairwise(FEE_BANDS):
            mid = (lo + hi) / 2
            spread = (math.log(mid) - math.log(max(0.6, fee_next * 0.45))) ** 2
            weights.append(math.exp(-spread / 1.1) * (hi - lo) ** 0.35)
        total, vsize = sum(weights), self.vsize()
        return [(lo, hi, vsize * w / total) for (lo, hi), w in zip(itertools.pairwise(FEE_BANDS), weights, strict=True)]

    # ------------------------------------------------------------------ Bitcoin Core's JSON-RPC
    def rpc(self, method: str, params: list):
        s = self.samples[-1]
        tip = self.blocks[self.tip]
        vsize = self.vsize()
        if method == "getblockchaininfo":
            return {
                "chain": "main",
                "blocks": self.tip,
                "headers": self.tip,
                "bestblockhash": tip["blockhash"],
                "difficulty": 1.4627e14,
                "time": tip["time"],
                "mediantime": tip["time"] - 3000,
                "verificationprogress": 0.999998,
                "initialblockdownload": False,
                "size_on_disk": int(792.4e9 + (time.time() - self.started) * 2700),
                "pruned": False,
            } | (
                {
                    "blocks": 174_238,
                    "time": 1_333_500_000,
                    "verificationprogress": 0.0019,
                    "initialblockdownload": True,
                    "size_on_disk": 1_260_000_000,
                }
                if self.syncing
                else {}
            )
        if method == "getnetworkinfo":
            tor = "127.0.0.1:9050"
            return {
                "version": 320100,
                "subversion": "/Satoshi:32.1.0/",
                "connections": len(self.peers),
                "connections_in": sum(1 for p in self.peers if p["inbound"]),
                "connections_out": sum(1 for p in self.peers if not p["inbound"]),
                "networks": [
                    {"name": "ipv4", "limited": True, "reachable": False, "proxy": tor},
                    {"name": "ipv6", "limited": True, "reachable": False, "proxy": tor},
                    {"name": "onion", "limited": False, "reachable": True, "proxy": tor},
                    {"name": "i2p", "limited": True, "reachable": False, "proxy": ""},
                    {"name": "cjdns", "limited": True, "reachable": False, "proxy": ""},
                ],
                "relayfee": 0.000001,
                "localaddresses": [{"address": self.p2p_onion, "port": 8333, "score": 4}],
            }
        if method == "getmempoolinfo":
            return {
                "loaded": True,
                "size": max(300, int(vsize * 2550)),
                "bytes": int(vsize * 1e6),
                "usage": int(vsize * 1e6 * 5.4),
                "total_fee": round(vsize * 1e6 * s["fee_next"] * 0.55 / 1e8, 8),
                "maxmempool": 300_000_000,
                "mempoolminfee": 0.000001,
                "minrelaytxfee": 0.000001,
                "unbroadcastcount": 0,
            }
        if method == "getnettotals":
            return {
                "totalbytesrecv": int(self.recv_total),
                "totalbytessent": int(self.sent_total),
                "timemillis": int(time.time() * 1000),
            }
        if method == "uptime":
            return int(time.time() - self.started)
        if method == "getpeerinfo":
            return self.peers
        if method == "estimatesmartfee":
            blocks = int(params[0])
            factor = (
                1.0 if blocks <= 2 else 0.85 if blocks <= 3 else 0.68 if blocks <= 6 else 0.45 if blocks <= 36 else 0.3
            )
            return {"feerate": round(max(0.2, s["fee_next"] * factor) / 1e5, 8), "blocks": blocks}
        if method == "getblockstats":
            block = self.blocks.get(int(params[0]))
            if block is None:
                raise KeyError("Target block height after current tip")
            return block
        if method == "getmininginfo":
            return {"blocks": self.tip, "difficulty": 1.4627e14, "networkhashps": 1.4627e14 * 2**32 / 600}
        if method == "getchaintxstats":
            return {"time": tip["time"], "txcount": 1_412_000_000 + (self.tip - 975_000) * 3400, "txrate": 5.6}
        if method == "getindexinfo":
            return {"txindex": {"synced": True, "best_block_height": self.tip}}
        if method == "getblockhash":
            return f"epoch{int(params[0])}"
        if method == "getblockheader":
            return {"hash": params[0], "time": self.epoch_time}
        if method == "getrawmempool":
            entries = {}
            for lo, hi, megabytes in self.histogram():
                for n in range(24):
                    size = megabytes * 1e6 / 24
                    rate = lo + (hi - lo) * (n + 0.5) / 24
                    entries[f"{lo}-{n}"] = {"vsize": int(size), "fees": {"base": size * rate / 1e8}}
            return entries
        raise KeyError("Method not found")

    # ------------------------------------------------------------------ LND's REST API
    def lnd(self, path: str, body: dict) -> dict:
        s = self.samples[-1]
        channels = [{k: v for k, v in c.items() if not k.startswith("_")} for c in self.channels]
        if path == "/v1/getinfo":
            return {
                "version": "0.21.8-beta commit=v0.21.8-beta",
                "identity_pubkey": self.ln_pubkey,
                "alias": "polder-node",
                "color": "#f7931a",
                "num_pending_channels": len(self.pending_open),
                "num_active_channels": sum(1 for c in channels if c["active"]),
                "num_inactive_channels": sum(1 for c in channels if not c["active"]),
                "num_peers": len(channels) + 1,
                "block_height": self.tip,
                "synced_to_chain": True,
                "synced_to_graph": True,
                "uris": [f"{self.ln_pubkey}@{self.ln_onion}:9735"],
            }
        if path == "/v1/balance/blockchain":
            return {
                "total_balance": str(s["ln_onchain"]),
                "confirmed_balance": str(s["ln_onchain"]),
                "unconfirmed_balance": "0",
            }
        if path == "/v1/balance/channels":
            pending = sum(int(p["channel"]["local_balance"]) for p in self.pending_open)
            return {
                "local_balance": {"sat": str(sum(int(c["local_balance"]) for c in channels))},
                "remote_balance": {"sat": str(sum(int(c["remote_balance"]) for c in channels))},
                "unsettled_local_balance": {"sat": "12500"},
                "pending_open_local_balance": {"sat": str(pending)},
            }
        if path == "/v1/channels":
            return {"channels": channels}
        if path == "/v1/channels/pending":
            return {
                "pending_open_channels": self.pending_open,
                "waiting_close_channels": [],
                "pending_force_closing_channels": [],
            }
        if path == "/v1/fees":
            now = time.time()

            def earned(days: float) -> str:
                return str(
                    sum(int(f["fee_msat"]) for f in self.forwards if int(f["timestamp"]) > now - days * 86400) // 1000
                )

            return {
                "channel_fees": [
                    {
                        "chan_id": c["chan_id"],
                        "channel_point": c["channel_point"],
                        "base_fee_msat": "1000",
                        "fee_per_mil": str(c["_ppm"]),
                    }
                    for c in self.channels
                ],
                "day_fee_sum": earned(1),
                "week_fee_sum": earned(7),
                "month_fee_sum": earned(30),
            }
        if path == "/v1/switch":
            start, offset = int(body.get("start_time") or 0), int(body.get("index_offset") or 0)
            limit = int(body.get("num_max_events") or 100)
            events = [f for f in self.forwards if int(f["timestamp"]) >= start][offset : offset + limit]
            return {"forwarding_events": events, "last_offset_index": offset + len(events)}
        if path == "/v1/payments":
            return {"payments": self.payments[-30:]}
        if path == "/v1/invoices":
            return {"invoices": self.invoices[-50:]}
        if path == "/v1/channels/backup":
            points = [c["channel_point"] for c in channels] + [p["channel"]["channel_point"] for p in self.pending_open]
            blob = bytes(self.rng.randrange(256) for _ in range(180 + 120 * len(points)))
            return {
                "multi_chan_backup": {
                    "chan_points": [
                        {"funding_txid_str": p.split(":")[0], "output_index": int(p.split(":")[1])} for p in points
                    ],
                    "multi_chan_backup": base64.b64encode(blob).decode(),
                }
            }
        if path.startswith("/v1/graph/node/"):
            return {"node": {"alias": self.graph.get(path.rsplit("/", 1)[1], "")}}
        raise KeyError(path)

    # ------------------------------------------------------------------ Tor's control port
    def tor_info(self, key: str) -> str:
        if key == "version":
            return "0.4.8.21"
        if key == "status/bootstrap-phase":
            return 'NOTICE BOOTSTRAP PROGRESS=100 TAG=done SUMMARY="Done"'
        if key == "traffic/read":
            return str(int(self.tor_read))
        if key == "traffic/written":
            return str(int(self.tor_written))
        raise KeyError(key)

    def tor_circuits(self) -> list[str]:
        lines = []
        for c in self.circuits:
            path = ",".join(f"${fingerprint}~{name}" for fingerprint, name in c["path"])
            created = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(c["created"])) + ".000000"
            lines.append(
                f"{c['id']} {c['status']} {path} BUILD_FLAGS=NEED_CAPACITY PURPOSE={c['purpose']} TIME_CREATED={created}"
            )
        return lines


def rpc_app(node: MockNode) -> web.Application:
    expected = "Basic " + base64.b64encode(f"{RPC_USER}:{RPC_PASSWORD}".encode()).decode()

    def answer(item: dict) -> dict:
        try:
            return {
                "result": node.rpc(item.get("method", ""), item.get("params") or []),
                "error": None,
                "id": item.get("id"),
            }
        except KeyError as e:
            return {"result": None, "error": {"code": -32601, "message": str(e).strip("'")}, "id": item.get("id")}

    async def handle(request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != expected:
            return web.Response(status=401)
        node.tick()
        payload = json.loads(await request.read())
        if isinstance(payload, list):
            return web.json_response([answer(item) for item in payload])
        result = answer(payload)
        return web.json_response(result, status=500 if result["error"] else 200)

    app = web.Application(client_max_size=4 * 1024 * 1024)
    app.router.add_post("/", handle)
    return app


def lnd_app(node: MockNode) -> web.Application:
    async def handle(request: web.Request) -> web.Response:
        if not request.headers.get("Grpc-Metadata-macaroon"):
            return web.json_response({"code": 2, "message": "expected 1 macaroon, got 0"}, status=500)
        node.tick()
        body = await request.json() if request.method == "POST" else {}
        try:
            return web.json_response(node.lnd(request.path, body))
        except KeyError:
            return web.json_response({"code": 5, "message": "Not Found"}, status=404)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handle)
    return app


async def tor_server(node: MockNode, host: str, port: int) -> asyncio.Server:
    async def session(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        authenticated = False
        try:
            while line := (await reader.readline()).decode().strip():
                command, _, rest = line.partition(" ")
                command = command.upper()
                if command == "AUTHENTICATE":
                    authenticated = rest.strip('"') == TOR_PASSWORD
                    reply = ["250 OK"] if authenticated else ["515 Authentication failed: Password did not match"]
                elif command == "QUIT":
                    writer.write(b"250 closing connection\r\n")
                    break
                elif not authenticated:
                    reply = ["514 Authentication required."]
                elif command == "GETINFO":
                    node.tick()
                    reply = []
                    try:
                        for key in rest.split():
                            if key == "circuit-status":
                                reply += ["250+circuit-status=", *node.tor_circuits(), "."]
                            else:
                                reply.append(f"250-{key}={node.tor_info(key)}")
                        reply.append("250 OK")
                    except KeyError as e:
                        reply = [f"552 Unrecognized key {e}"]
                else:
                    reply = [f'510 Unrecognized command "{command}"']
                writer.write(("\r\n".join(reply) + "\r\n").encode())
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()

    return await asyncio.start_server(session, host, port)


def seed_database(node: MockNode, path: Path) -> None:
    """Thirty days of samples and a few events in the app's own database, so the charts aren't empty."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from src.database import SERIES, Database

    path.parent.mkdir(parents=True, exist_ok=True)
    database = Database(path)
    with database._lock:
        database._db.execute("DELETE FROM samples")
        database._db.execute("DELETE FROM events")
        for index, (ts, s) in enumerate(zip(node.ts, node.samples, strict=True)):
            values = {**s, "size_on_disk": 792.4e9 - (len(node.ts) - index) * STEP * 2700}
            row = (ts, *(values.get(name) for name in SERIES))
            database._db.execute(f"INSERT OR REPLACE INTO samples VALUES ({', '.join('?' * len(row))})", row)
        database._db.commit()
    for height in sorted(node.blocks)[-6:]:
        b = node.blocks[height]
        text = f"Block {height:,} · {b['txs']:,} transactions · median {b['feerate_percentiles'][2]} sat/vB"
        database.add_event({"ts": b["time"], "source": "bitcoin", "type": "block", "level": "", "text": text})
    for f in node.forwards[-4:]:
        text = f"Forwarded {int(f['amt_out']):,} sat, earned {int(f['fee_msat']) / 1000:,.3f} sat · {f['peer_alias_in']} → {f['peer_alias_out']}"
        database.add_event(
            {"ts": int(f["timestamp"]), "source": "lightning", "type": "forward", "level": "ok", "text": text}
        )
    database.close()
    print(f"Seeded {len(node.ts)} samples into {path}")


async def serve(args) -> None:
    node = MockNode(args.seed, args.fast, args.syncing)
    if args.seed_db:
        seed_database(node, Path(args.seed_db))
    for app, port in ((rpc_app(node), args.rpc_port), (lnd_app(node), args.lnd_port)):
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, args.host, port).start()
    await tor_server(node, args.host, args.tor_port)
    print(
        f"Mock node: Bitcoin Core RPC on {args.host}:{args.rpc_port}, LND REST on {args.lnd_port}, Tor control on {args.tor_port}"
    )
    await asyncio.Event().wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--rpc-port", type=int, default=18443)
    parser.add_argument("--lnd-port", type=int, default=18080)
    parser.add_argument("--tor-port", type=int, default=19051)
    parser.add_argument("--seed", type=int, default=21)
    parser.add_argument(
        "--fast", action="store_true", help="a new block every 40 seconds or so instead of every 10 minutes"
    )
    parser.add_argument("--syncing", action="store_true", help="answer as a node early in its first sync")
    parser.add_argument("--seed-db", help="write 30 days of history into this app database first")
    args = parser.parse_args()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(args))


if __name__ == "__main__":
    main()
