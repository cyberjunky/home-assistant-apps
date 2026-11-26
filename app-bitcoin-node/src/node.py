"""The node as a whole: Bitcoin Core, LND and Tor together, as the pages and MQTT see it.

The three clients each keep their own state. This puts it together, stores a sample for the
charts every two minutes, keeps the list of what happened, and judges the node's health.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path

from .bitcoin import BitcoinClient
from .config import APP_VERSION, Config, host_of
from .database import Database
from .lightning import LndClient
from .pool import PoolClient
from .tor import TorClient

logger = logging.getLogger(__name__)

# No block for this long is worth a warning: it happens about once in 400 blocks by chance
LATE_BLOCK_SECONDS = 3600
FEW_PEERS = 8


def public_config(config: Config) -> dict:
    """The options to show. Passwords and the macaroon stay out."""
    return {
        "rpc_url": config.bitcoin_url,
        "rpc_user": config.bitcoin_user,
        "lnd_url": config.lnd_url,
        "lnd_tls_cert": bool(config.lnd_tls_cert),
        "lnd_read_only": config.lnd_read_only,
        "tor_control": config.tor_control,
        "pool_url": config.pool_url,
        "poll_seconds": config.poll_seconds,
        "history_days": config.history_days,
        "mqtt_enabled": config.mqtt_enabled,
        "mqtt_base_topic": config.mqtt_base_topic,
        "mqtt_discovery_prefix": config.mqtt_discovery_prefix,
        "log_level": config.log_level,
    }


class Node:
    def __init__(self, config: Config, database: Database, data_dir: Path):
        self.config = config
        self.database = database
        # Called with every event and its extra fields; MQTT listens here
        self.listeners: list[Callable[[dict, dict], None]] = []
        self.bitcoin = BitcoinClient(config, database, self.emit)
        self.lightning = LndClient(config, Path(data_dir) / "channel-backups", self.emit)
        self.tor = TorClient(config, self.emit)
        self.pool = PoolClient(config, self.emit, lambda: self.bitcoin.chain.get("difficulty"))

    def emit(self, source: str, kind: str, level: str, text: str, ts: float | None = None, **extra) -> None:
        """Something happened: keep it for the Activity lists and pass it on."""
        event = {"ts": ts or time.time(), "source": source, "type": kind, "level": level, "text": text}
        try:
            self.database.add_event(event)
        except Exception:
            logger.exception("Could not store an event")
        for listener in self.listeners:
            listener(event, extra)

    async def run(self) -> None:
        """Store a sample for the charts whenever one is due."""
        while True:
            await asyncio.sleep(min(30, self.config.poll_seconds))
            if self.bitcoin.connected or self.lightning.connected or self.tor.connected or self.pool.connected:
                await asyncio.to_thread(self.database.sample, self.sample_values())

    def sample_values(self) -> dict:
        values: dict = {}
        btc = self.bitcoin
        if btc.connected:
            mempool, net = btc.mempool_status(), btc.net_status()
            values.update(
                mempool_tx=mempool["tx"],
                mempool_vsize=mempool["vsize"],
                fee_next=btc.fees.get("next"),
                fee_hour=btc.fees.get("hour"),
                fee_day=btc.fees.get("day"),
                peers_in=net["in"],
                peers_out=net["out"],
                recv_rate=btc.recv_rate,
                sent_rate=btc.sent_rate,
                size_on_disk=btc.chain.get("size_on_disk"),
            )
        if self.lightning.connected:
            totals = self.lightning.totals()
            values.update(
                ln_local=totals["local"], ln_remote=totals["remote"], ln_onchain=self.lightning.onchain["confirmed"]
            )
        if self.tor.connected:
            values.update(tor_read_rate=self.tor.read_rate, tor_written_rate=self.tor.written_rate)
        if self.pool.connected and not self.pool.stale:
            pool = self.pool.status(None)
            values.update(
                pool_hashrate=pool["hashrate"].get("5m"),
                pool_hashrate_hour=pool["hashrate"].get("1hr"),
                pool_workers=pool["active_workers"],
            )
        return values

    def onion_services(self) -> list[dict]:
        services = []
        address = self.bitcoin.onion_address()
        if address:
            services.append({"name": "Bitcoin Core, peers", "address": address["address"], "port": address.get("port")})
        uri = self.lightning.onion_uri()
        if uri:
            host, _, port = uri.split("@")[-1].rpartition(":")
            services.append(
                {"name": "LND, Lightning peers", "address": host, "port": int(port) if port.isdigit() else None}
            )
        return services

    def health(self, now: float) -> list[dict]:
        """A line per thing worth watching. state: ok, warn, bad, or empty for a plain fact."""
        checks = []

        def add(name: str, state: str, text: str) -> None:
            checks.append({"name": name, "state": state, "text": text})

        btc = self.bitcoin
        if not btc.configured:
            add("Bitcoin Core", "warn", "Not set up: enter its address in the app's configuration")
        elif not btc.connected:
            add("Bitcoin Core", "bad", btc.error or "No answer yet")
        else:
            net = btc.net_status()
            if btc.synced:
                add("Chain", "ok", f"In sync at block {btc.height:,}")
                age = now - (btc.blocks[0]["time"] if btc.blocks else now)
                late = age > LATE_BLOCK_SECONDS
                add("Last block", "warn" if late else "ok", f"{int(age // 60)} min ago" if age >= 60 else "just now")
            else:
                add("Chain", "warn", f"Syncing: {(btc.chain.get('verificationprogress') or 0) * 100:.2f}% done")
            peers = net["peers"]
            add(
                "Peers",
                "ok" if peers >= FEW_PEERS else "warn" if peers else "bad",
                f"{peers} connected, {net['out']} outbound",
            )
            onion = any(n["name"] == "onion" and n["reachable"] for n in net["networks"])
            if btc.onion_address():
                add("Onion address", "ok", "Reachable through its onion address")
            elif onion:
                add("Onion address", "", "Connects through Tor, but has no onion address of its own")
            clear = sum(count for name, count in net["peer_networks"].items() if name in ("ipv4", "ipv6"))
            if clear:
                add("Clearnet", "", f"{clear} peers over IPv4 or IPv6 see the node's IP address")
            elif onion:
                add("Clearnet", "ok", "No IPv4 or IPv6 peers: the home address stays private")
            if btc.txindex is not None:
                add(
                    "Transaction index",
                    "ok" if btc.txindex else "",
                    "Ready" if btc.txindex else "Off or still building",
                )

        ln = self.lightning
        if ln.configured:
            if not ln.connected:
                add("Lightning", "bad", ln.error or "No answer yet")
            else:
                info = ln.info
                both = info.get("synced_to_chain") and info.get("synced_to_graph")
                behind = "the chain" if not info.get("synced_to_chain") else "the graph"
                add(
                    "Lightning",
                    "ok" if both else "warn",
                    "In sync with the chain and the graph" if both else f"Still syncing {behind}",
                )
                totals = ln.totals()
                count = totals["active"] + totals["inactive"]
                text = f"{totals['active']} of {count} active" + (
                    f", {totals['pending']} pending" if totals["pending"] else ""
                )
                add(
                    "Channels",
                    "warn" if totals["inactive"] else "ok" if count else "",
                    text if count else "No channels yet",
                )
                backup = ln.status()["backup"]
                if backup.get("error"):
                    add("Channel backup", "warn", backup["error"])
                elif backup.get("available"):
                    complete = backup.get("complete", True)
                    add(
                        "Channel backup",
                        "ok" if complete else "warn",
                        "Holds every channel" if complete else "Misses a channel opened since",
                    )
        tor = self.tor
        if tor.configured:
            if not tor.connected:
                add("Tor", "bad", tor.error or "No answer yet")
            else:
                ready = tor.bootstrap == 100
                add(
                    "Tor",
                    "ok" if ready else "warn",
                    "Connected to the Tor network" if ready else f"Starting: {tor.bootstrap}%",
                )
        pool = self.pool
        if pool.configured:
            if not pool.connected:
                add("Mining pool", "bad", pool.error or "No answer yet")
            elif pool.stale:
                add("Mining pool", "bad", "ckpool stopped writing its status: is it running?")
            else:
                workers = pool.workers()
                active = sum(1 for w in workers if w["active"])
                add(
                    "Mining pool",
                    "ok" if active and active == len(workers) else "warn",
                    f"{active} of {len(workers)} workers mining" if workers else "No miner connected yet",
                )
        return checks

    def status(self, mqtt=None) -> dict:
        now = time.time()
        btc = self.bitcoin
        return {
            "version": APP_VERSION,
            "now": now,
            "poll_seconds": self.config.poll_seconds,
            "config": public_config(self.config),
            "hosts": {"bitcoin": host_of(self.config.bitcoin_url), "lightning": host_of(self.config.lnd_url)},
            "node": btc.node_status(now),
            "mempool": btc.mempool_status(),
            "net": btc.net_status(),
            "lightning": self.lightning.status(),
            "tor": self.tor.status(self.onion_services()),
            "pool": self.pool.status(btc.hashrate),
            "health": self.health(now),
            "mqtt": {"connected": bool(mqtt and mqtt.connected), "broker": mqtt.broker if mqtt else ""},
            "database": self.database.counts(),
        }
