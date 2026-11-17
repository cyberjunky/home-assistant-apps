"""LND, read over its REST API with a macaroon.

Every poll reads the node, the balances and the channels. Every few minutes it reads what was
forwarded, paid and received, and the channel backup, which is kept in the app's storage whenever
it changed.

This version only reads, so the read-only macaroon is all it needs. Everything that would change
the wallet belongs in `LndClient.change()`: the one place that checks the read_only option, so a
later version that can pay or open channels stays off until that option is turned off.
"""

import asyncio
import base64
import hashlib
import json
import logging
import ssl
import time
from collections.abc import Callable
from pathlib import Path

import aiohttp

from .config import Config

logger = logging.getLogger(__name__)

SLOW_SECONDS = 300
FORWARD_DAYS = 30
FORWARD_PAGE = 1000
# Never more forwards than this in memory: a very busy routing node gets a shorter history
FORWARDS_MAX = 100_000
RECENT_KEPT = 60
BACKUPS_KEPT = 20
TIMEOUT = aiohttp.ClientTimeout(total=45, connect=10)
# Polls that may fail in a row before the service counts as not answering
MISSES_ALLOWED = 2


class LndError(Exception):
    """LND can't be reached, or refused a request."""


class ReadOnly(LndError):
    """A change was asked for while the app is set to read-only."""


def sat(value) -> int:
    """LND sends its amounts as text."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def opened_height(chan_id) -> int | None:
    """The block a channel was opened in: the top three bytes of its id."""
    height = sat(chan_id) >> 40
    # Channels with an alias id (zero-conf) don't carry a real height
    return height if 0 < height < 16_000_000 else None


def day_start(ts: float) -> int:
    """Midnight of that day, local time."""
    t = time.localtime(ts)
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))


def forwards_per_day(forwards: list[dict], now: float, days: int = FORWARD_DAYS) -> list[dict]:
    """A row per day, oldest first, also for days on which nothing was forwarded."""
    rows: dict[int, dict] = {}
    for back in range(days - 1, -1, -1):
        day = day_start(now - back * 86400)
        rows[day] = {"day": day, "count": 0, "volume": 0, "fees": 0.0}
    for forward in forwards:
        row = rows.get(day_start(forward["ts"]))
        if row:
            row["count"] += 1
            row["volume"] += forward["amount"]
            row["fees"] += forward["fee"]
    for row in rows.values():
        row["fees"] = round(row["fees"], 3)
    return list(rows.values())


class LndClient:
    """LND's state, kept current by run()."""

    def __init__(self, config: Config, backup_dir: Path, emit: Callable[..., None]):
        self.config = config
        self.backup_dir = Path(backup_dir)
        self.emit = emit
        self.configured = bool(config.lnd_url)
        self.read_only = config.lnd_read_only
        self.connected = False
        self.error = ""
        self.updated = 0.0
        self.info: dict = {}
        self.onchain = {"confirmed": 0, "unconfirmed": 0}
        self.balance: dict = {}
        self.channels: list[dict] = []
        self.pending: list[dict] = []
        self.fee_report = {"day": 0, "week": 0, "month": 0}
        self.forwards: list[dict] = []  # oldest first
        self.payments: list[dict] = []
        self.invoices: list[dict] = []
        self.backup: dict = {"available": False}
        self._session: aiohttp.ClientSession | None = None
        self._aliases: dict[str, str] = {}
        self._policies: dict[str, dict] = {}
        self._slow_at = 0.0
        self._forward_keys: set[str] = set()
        self._forwards_read = False
        self._last_payment: int | None = None
        self._last_settled: int | None = None
        self._names: dict[str, str] = {}
        self._channel_state: dict[str, bool] | None = None
        self._ever_connected = False
        self._misses = 0
        self._load_backup()

    # ------------------------------------------------------------------ REST
    def _ssl(self):
        if not self.config.lnd_url.startswith("https"):
            return None
        if self.config.lnd_tls_cert:
            # LND's own certificate is the only one trusted: nobody else can answer in its place
            return ssl.create_default_context(cadata=self.config.lnd_tls_cert)
        return False

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            async with self._session.request(method, self.config.lnd_url + path, **kwargs) as response:
                status = response.status
                try:
                    answer = await response.json(content_type=None)
                except ValueError:
                    answer = None
        except aiohttp.ClientConnectorCertificateError as e:
            raise LndError(f"the certificate of {self.config.lnd_url} is not the one in tls_cert: {e}") from e
        except (aiohttp.ClientError, TimeoutError) as e:
            raise LndError(f"no answer from {self.config.lnd_url}: {str(e) or type(e).__name__}") from e
        if status == 200 and isinstance(answer, dict):
            return answer
        message = (answer or {}).get("message") or (answer or {}).get("error") or f"HTTP {status}"
        if "permission denied" in str(message):
            raise LndError(f"the macaroon does not allow {path.split('?')[0]}")
        if "wallet locked" in str(message) or "wallet is encrypted" in str(message):
            raise LndError("LND's wallet is locked: unlock it on the node")
        raise LndError(f"{path.split('?')[0]}: {message}")

    async def get(self, path: str, **params) -> dict:
        return await self._request("GET", path, params={k: str(v) for k, v in params.items()} or None)

    async def read(self, path: str, body: dict) -> dict:
        """A question LND wants as a POST, such as the forwarding history. It changes nothing."""
        return await self._request("POST", path, json=body)

    async def change(self, method: str, path: str, body: dict | None = None) -> dict:
        """Everything that changes the wallet or a channel goes through here. Nothing calls it yet."""
        if self.read_only:
            raise ReadOnly("This app is set to read-only")
        return await self._request(method, path, json=body or {})

    async def _try(self, path: str, **params) -> dict | None:
        try:
            return await self.get(path, **params)
        except LndError as e:
            logger.debug(f"LND: {e}")
            return None

    # ------------------------------------------------------------------ polling
    async def run(self) -> None:
        if not self.configured:
            return
        headers = {"Grpc-Metadata-macaroon": self.config.lnd_macaroon}
        connector = aiohttp.TCPConnector(ssl=self._ssl())
        if self.config.lnd_url.startswith("https") and not self.config.lnd_tls_cert:
            logger.warning("LND: no tls_cert set, so its certificate is not checked")
        async with aiohttp.ClientSession(headers=headers, timeout=TIMEOUT, connector=connector) as session:
            self._session = session
            while True:
                try:
                    await self.poll()
                    self._set_link(True, "")
                except LndError as e:
                    self._set_link(False, str(e))
                except Exception as e:
                    logger.exception("LND: reading failed")
                    self._set_link(False, repr(e))
                await asyncio.sleep(self.config.poll_seconds)

    def _set_link(self, connected: bool, error: str) -> None:
        if connected:
            self._misses = 0
        elif self.connected:
            # A busy node answers late now and then: one missed poll is not an outage.
            # What was read last stays on the pages until MISSES_ALLOWED polls in a row failed
            self._misses += 1
            if self._misses <= MISSES_ALLOWED:
                logger.debug(f"LND: missed a poll ({self._misses}): {error}")
                return
        if connected and not self.connected:
            logger.info(f"LND {self.version} answers as {self.info.get('alias', '')!r}")
            if self._ever_connected:
                self.emit("lightning", "node", "ok", "LND answers again")
            self._ever_connected = True
        elif not connected and (self.connected or error != self.error):
            logger.warning(f"LND: {error}")
            if self.connected:
                self.emit("lightning", "node", "bad", f"LND does not answer: {error}")
        self.connected, self.error = connected, error

    async def poll(self) -> None:
        self.info = await self.get("/v1/getinfo")
        chain = await self.get("/v1/balance/blockchain")
        self.onchain = {
            "confirmed": sat(chain.get("confirmed_balance")),
            "unconfirmed": sat(chain.get("unconfirmed_balance")),
        }
        self.balance = await self.get("/v1/balance/channels")
        listed = await self.get("/v1/channels", peer_alias_lookup="true")
        pending = await self.get("/v1/channels/pending")
        now = time.time()
        if now - self._slow_at > SLOW_SECONDS or not self._policies:
            fees = await self._try("/v1/fees")
            if fees:
                self._policies = {str(f.get("chan_id")): f for f in fees.get("channel_fees") or []}
                self.fee_report = {k: sat(fees.get(f"{k}_fee_sum")) for k in ("day", "week", "month")}
        self.channels = [self._channel(c) for c in listed.get("channels") or []]
        self.pending = await self._pending(pending)
        self._channel_events()
        self.updated = now
        if now - self._slow_at > SLOW_SECONDS:
            self._slow_at = now
            await self._read_forwards(now)
            await self._read_payments()
            await self._read_invoices()
            await self._read_backup()

    def _channel(self, c: dict) -> dict:
        pubkey = c.get("remote_pubkey", "")
        alias = c.get("peer_alias") or self._aliases.get(pubkey) or ""
        if alias and "unable to lookup" not in alias:
            self._aliases[pubkey] = alias
        else:
            alias = pubkey[:12]
        policy = self._policies.get(str(c.get("chan_id")), {})
        lifetime = sat(c.get("lifetime"))
        return {
            "chan_id": str(c.get("chan_id", "")),
            "channel_point": c.get("channel_point", ""),
            "alias": alias,
            "pubkey": pubkey,
            "capacity": sat(c.get("capacity")),
            "local": sat(c.get("local_balance")),
            "remote": sat(c.get("remote_balance")),
            "unsettled": sat(c.get("unsettled_balance")),
            "active": bool(c.get("active")),
            "private": bool(c.get("private")),
            "initiator": bool(c.get("initiator")),
            "fee_base_msat": sat(policy.get("base_fee_msat")) if policy else None,
            "fee_ppm": sat(policy.get("fee_per_mil")) if policy else None,
            "sent": sat(c.get("total_satoshis_sent")),
            "received": sat(c.get("total_satoshis_received")),
            # The share of the time LND watched the peer that it was online
            "uptime": round(sat(c.get("uptime")) / lifetime, 4) if lifetime else None,
            "opened_height": opened_height(c.get("chan_id")),
        }

    async def _alias(self, pubkey: str) -> str:
        if pubkey not in self._aliases:
            node = await self._try(f"/v1/graph/node/{pubkey}")
            self._aliases[pubkey] = ((node or {}).get("node") or {}).get("alias") or pubkey[:12]
        return self._aliases[pubkey]

    async def _pending(self, answer: dict) -> list[dict]:
        out = []
        kinds = (("pending_open_channels", "opening"), ("waiting_close_channels", "closing"))
        kinds += (("pending_force_closing_channels", "force closing"),)
        for key, kind in kinds:
            for item in answer.get(key) or []:
                channel = item.get("channel") or {}
                out.append(
                    {
                        "alias": await self._alias(channel.get("remote_node_pub", "")),
                        "pubkey": channel.get("remote_node_pub", ""),
                        "channel_point": channel.get("channel_point", ""),
                        "capacity": sat(channel.get("capacity")),
                        "local": sat(channel.get("local_balance")),
                        "kind": kind,
                    }
                )
        return out

    def _channel_events(self) -> None:
        state = {c["chan_id"]: c["active"] for c in self.channels}
        names = {c["chan_id"]: c["alias"] for c in self.channels}
        previous, self._channel_state = self._channel_state, state
        if previous is None:
            self._names = names
            return
        for chan_id, active in state.items():
            name = names[chan_id]
            if chan_id not in previous:
                self.emit("lightning", "channel_opened", "ok", f"Channel with {name} is open", peer=name)
            elif active != previous[chan_id]:
                kind, level = ("channel_online", "ok") if active else ("channel_offline", "warn")
                self.emit(
                    "lightning",
                    kind,
                    level,
                    f"Channel with {name} {'is back' if active else 'went offline'}",
                    peer=name,
                )
        for chan_id in previous.keys() - state.keys():
            name = self._names.get(chan_id, chan_id)
            self.emit("lightning", "channel_closed", "warn", f"Channel with {name} is closed", peer=name)
        self._names = {**self._names, **names}

    async def _read_forwards(self, now: float) -> None:
        """What was forwarded in the last 30 days. After the first time only what is new is asked for."""
        horizon = now - FORWARD_DAYS * 86400
        start = int(self.forwards[-1]["ts"]) if self.forwards else int(horizon)
        offset, fresh = 0, []
        while len(self.forwards) + len(fresh) < FORWARDS_MAX:
            body = {"start_time": str(start), "index_offset": offset, "num_max_events": FORWARD_PAGE}
            try:
                page = await self.read("/v1/switch", {**body, "peer_alias_lookup": True})
            except LndError as e:
                logger.debug(f"LND: {e}")
                return
            events = page.get("forwarding_events") or []
            for event in events:
                key = str(
                    event.get("timestamp_ns")
                    or f"{event.get('timestamp')}-{event.get('chan_id_in')}-{event.get('amt_out')}"
                )
                if key in self._forward_keys:
                    continue
                self._forward_keys.add(key)
                ts = sat(event.get("timestamp_ns")) / 1e9 or float(sat(event.get("timestamp")))
                names = [self._forward_peer(event, side) for side in ("in", "out")]
                fresh.append(
                    {
                        "key": key,
                        "ts": ts,
                        "amount": sat(event.get("amt_out")),
                        "fee": sat(event.get("fee_msat")) / 1000,
                        "text": f"{names[0]} → {names[1]}",
                    }
                )
            if len(events) < FORWARD_PAGE:
                break
            offset = sat(page.get("last_offset_index")) or offset + len(events)
        fresh.sort(key=lambda f: f["ts"])
        if self._forwards_read:
            for forward in fresh:
                self.emit(
                    "lightning",
                    "forward",
                    "ok",
                    f"Forwarded {forward['amount']:,} sat, earned {forward['fee']:,.3f} sat · {forward['text']}",
                    ts=forward["ts"],
                    amount_sat=forward["amount"],
                    fee_sat=forward["fee"],
                    route=forward["text"],
                )
        self._forwards_read = True
        self.forwards = [f for f in [*self.forwards, *fresh] if f["ts"] >= horizon]
        self._forward_keys = {f["key"] for f in self.forwards}

    def _forward_peer(self, event: dict, side: str) -> str:
        alias = event.get(f"peer_alias_{side}") or ""
        if alias and "unable to lookup" not in alias:
            return alias
        chan_id = str(event.get(f"chan_id_{side}", ""))
        return next((c["alias"] for c in self.channels if c["chan_id"] == chan_id), chan_id)

    async def _read_payments(self) -> None:
        answer = await self._try("/v1/payments", max_payments=30, reversed="true", include_incomplete="false")
        if answer is None:
            return
        payments = []
        for p in answer.get("payments") or []:
            if p.get("status") not in (None, "SUCCEEDED"):
                continue
            hops = next(
                (
                    ((h.get("route") or {}).get("hops") or [])
                    for h in p.get("htlcs") or []
                    if h.get("status") == "SUCCEEDED"
                ),
                [],
            )
            first = str(hops[0].get("chan_id", "")) if hops else ""
            through = next((c["alias"] for c in self.channels if c["chan_id"] == first), "")
            payments.append(
                {
                    "index": sat(p.get("payment_index")),
                    "ts": sat(p.get("creation_time_ns")) / 1e9 or float(sat(p.get("creation_date"))),
                    "amount": sat(p.get("value_sat")),
                    "fee": sat(p.get("fee_msat")) / 1000 if p.get("fee_msat") else float(sat(p.get("fee_sat"))),
                    "text": f"paid through {through}" if through else "paid",
                }
            )
        newest = max((p["index"] for p in payments), default=0)
        if self._last_payment is not None:
            for p in sorted(payments, key=lambda p: p["index"]):
                if p["index"] > self._last_payment:
                    self.emit(
                        "lightning",
                        "payment",
                        "",
                        f"Paid {p['amount']:,} sat · fee {p['fee']:,.3f} sat",
                        ts=p["ts"],
                        amount_sat=p["amount"],
                        fee_sat=p["fee"],
                    )
        self._last_payment = max(newest, self._last_payment or 0)
        self.payments = payments

    async def _read_invoices(self) -> None:
        answer = await self._try("/v1/invoices", num_max_invoices=50, reversed="true")
        if answer is None:
            return
        invoices = []
        for i in answer.get("invoices") or []:
            if i.get("state") != "SETTLED" and not i.get("settled"):
                continue
            invoices.append(
                {
                    "index": sat(i.get("settle_index")),
                    "ts": float(sat(i.get("settle_date"))),
                    "amount": sat(i.get("amt_paid_sat")) or sat(i.get("value")),
                    "text": i.get("memo") or "received",
                }
            )
        newest = max((i["index"] for i in invoices), default=0)
        if self._last_settled is not None:
            for i in sorted(invoices, key=lambda i: i["index"]):
                if i["index"] > self._last_settled:
                    self.emit(
                        "lightning",
                        "received",
                        "ok",
                        f"Received {i['amount']:,} sat · {i['text']}",
                        ts=i["ts"],
                        amount_sat=i["amount"],
                        memo=i["text"],
                    )
        self._last_settled = max(newest, self._last_settled or 0)
        self.invoices = invoices

    # ------------------------------------------------------------------ channel backup
    def _backup_files(self) -> list[Path]:
        return sorted(self.backup_dir.glob("channel-*.backup"))

    def _load_backup(self) -> None:
        """What is on disk from an earlier run."""
        files = self._backup_files()
        if not files:
            return
        latest = files[-1]
        try:
            data = latest.read_bytes()
        except OSError:
            return
        self.backup = {
            "available": True,
            "saved_at": int(latest.stat().st_mtime),
            "size": len(data),
            "hash": hashlib.sha256(data).hexdigest(),
            "copies": len(files),
            "channels": None,
            "points": None,
        }
        try:
            # Which channels that copy holds, so a restart doesn't store the same backup again
            points = json.loads((self.backup_dir / "index.json").read_text()).get("points")
        except (OSError, ValueError, AttributeError):
            points = None
        if isinstance(points, list):
            self.backup.update(points=points, channels=len(points))

    async def _read_backup(self) -> None:
        """LND's static channel backup of all channels. A new copy is stored when it differs from the last."""
        try:
            answer = await self.get("/v1/channels/backup")
        except LndError as e:
            self.backup = {**self.backup, "error": str(e)}
            return
        multi = answer.get("multi_chan_backup") or {}
        points = sorted(
            f"{p.get('funding_txid_str', '')}:{p.get('output_index', 0)}" for p in multi.get("chan_points") or []
        )
        try:
            data = base64.b64decode(multi.get("multi_chan_backup") or "")
        except ValueError:
            data = b""
        if not data:
            self.backup = {**self.backup, "error": "", "points": points, "channels": len(points)}
            return
        # The file is encrypted with a fresh nonce every time, so its bytes always differ: compare the channels
        changed = points != self.backup.get("points") or not self.backup.get("available")
        if changed:
            first = not self.backup.get("available")
            await asyncio.to_thread(self._save_backup, data, points)
            if not first:
                self.emit("lightning", "backup", "ok", f"Channel backup saved: {len(points)} channels")
        self.backup = {
            **self.backup,
            "available": True,
            "error": "",
            "points": points,
            "channels": len(points),
            "checked_at": int(time.time()),
        }

    def _save_backup(self, data: bytes, points: list[str]) -> None:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        target = self.backup_dir / f"channel-{time.strftime('%Y%m%d-%H%M%S')}.backup"
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
        (self.backup_dir / "index.json").write_text(json.dumps({"points": points}))
        for old in self._backup_files()[:-BACKUPS_KEPT]:
            old.unlink(missing_ok=True)
        self.backup = {
            **self.backup,
            "available": True,
            "saved_at": int(time.time()),
            "size": len(data),
            "hash": hashlib.sha256(data).hexdigest(),
            "copies": len(self._backup_files()),
        }
        logger.info(f"LND: channel backup saved as {target.name}")

    def latest_backup(self) -> Path | None:
        files = self._backup_files()
        return files[-1] if files else None

    # ------------------------------------------------------------------ what the pages get
    @property
    def version(self) -> str:
        return str(self.info.get("version") or "").split(" ")[0]

    def onion_uri(self) -> str | None:
        return next((u for u in self.info.get("uris") or [] if ".onion" in u), None)

    def totals(self) -> dict:
        def amount(key: str) -> int:
            return sat((self.balance.get(key) or {}).get("sat"))

        active = sum(1 for c in self.channels if c["active"])
        return {
            "active": active,
            "inactive": len(self.channels) - active,
            "pending": len(self.pending),
            "local": amount("local_balance"),
            "remote": amount("remote_balance"),
            "capacity": sum(c["capacity"] for c in self.channels),
            "unsettled": amount("unsettled_local_balance"),
            "pending_local": amount("pending_open_local_balance"),
        }

    def status(self) -> dict:
        if not self.configured:
            return {"configured": False, "connected": False}
        info = self.info
        public = {k: v for k, v in self.backup.items() if k not in ("points", "hash")}
        current = sorted(c["channel_point"] for c in [*self.channels, *self.pending] if c["channel_point"])
        if self.backup.get("points") is not None:
            # Each channel in the backup must be one LND has now, and the other way round
            public["complete"] = set(current) <= set(self.backup["points"])
        return {
            "configured": True,
            "connected": self.connected,
            "error": self.error,
            "read_only": self.read_only,
            "verified": bool(self.config.lnd_tls_cert) or not self.config.lnd_url.startswith("https"),
            "alias": info.get("alias", ""),
            "color": info.get("color", ""),
            "pubkey": info.get("identity_pubkey", ""),
            "version": self.version,
            "synced_chain": bool(info.get("synced_to_chain")),
            "synced_graph": bool(info.get("synced_to_graph")),
            "block_height": info.get("block_height"),
            "uris": info.get("uris") or [],
            "peers": info.get("num_peers"),
            "onchain": self.onchain,
            "channels": self.totals(),
            "fees": self.fee_report,
            "forwards_30d": len(self.forwards),
            "volume_30d": sum(f["amount"] for f in self.forwards),
            "backup": public,
        }

    def details(self, now: float, tip: int | None) -> dict:
        channels = []
        for c in self.channels:
            height = c["opened_height"]
            # A block every ten minutes: close enough to tell this year from last
            opened = int(now - (tip - height) * 600) if tip and height and tip >= height else None
            channels.append({**c, "opened": opened})
        recent = [
            {"ts": f["ts"], "kind": "forward", "amount": f["amount"], "fee": f["fee"], "text": f["text"]}
            for f in self.forwards[-RECENT_KEPT:]
        ]
        recent += [
            {"ts": p["ts"], "kind": "payment", "amount": -p["amount"], "fee": -p["fee"], "text": p["text"]}
            for p in self.payments
        ]
        recent += [
            {"ts": i["ts"], "kind": "invoice", "amount": i["amount"], "fee": 0, "text": i["text"]}
            for i in self.invoices
        ]
        recent.sort(key=lambda r: -r["ts"])
        return {
            "channels": channels,
            "pending": self.pending,
            "forwards_daily": forwards_per_day(self.forwards, now),
            "recent": recent[:RECENT_KEPT],
        }
