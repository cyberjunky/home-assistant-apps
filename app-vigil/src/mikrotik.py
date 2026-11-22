"""The MikroTik poller (RouterOS API, with librouteros in a thread: it is a blocking library).

Every 30 s: CPU, memory, disk, uptime and health (temperature, voltage), every interface's
traffic, errors and state, DHCP leases, Wi-Fi clients and the firewall's connection count. The
names follow the MKTXP exporter's (mktxp_system_cpu_load, mktxp_interface_rx_byte_total{name}),
so dashboards and rules made for MKTXP work with either. Read-only: it only runs `print`.

Labels: `host` is the router's identity (System → Identity); interfaces add `name`.
"""

import asyncio
import contextlib
import logging
import re
import ssl
from collections.abc import Callable
from datetime import UTC, datetime

from .config import Config
from .records import Sample

logger = logging.getLogger(__name__)

INTERVAL = 30

_UPTIME_PART = re.compile(r"(\d+)([wdhms])")
_UNIT_SECONDS = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_uptime(text: str) -> float | None:
    """RouterOS uptime as seconds: '1w2d03:04:05' (RouterOS 7), '1w2d3h4m5s' (6), '05:06:07'."""
    text = (text or "").strip()
    if not text:
        return None
    seconds = 0
    clock = re.search(r"(\d+):(\d\d):(\d\d)$", text)
    if clock:
        h, m, s = (int(x) for x in clock.groups())
        seconds += h * 3600 + m * 60 + s
        text = text[: clock.start()]
    parts = _UPTIME_PART.findall(text)
    if not parts and not clock:
        return None
    return float(seconds + sum(int(n) * _UNIT_SECONDS[u] for n, u in parts))


def _number(value) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).strip().rstrip("%CVW").strip())
    except (TypeError, ValueError):
        return None


def _true(value) -> bool:
    return value is True or str(value).lower() in ("true", "yes")


def collect(api, now: datetime) -> list[Sample]:
    """Read one round from a connected API (anything with `path(*parts)` returning rows of dicts)."""
    rows = lambda *path: list(api.path(*path))  # noqa: E731

    identity = "mikrotik"
    with contextlib.suppress(Exception):
        identity = rows("system", "identity")[0].get("name") or identity
    base = {"host": identity}
    samples: list[Sample] = []

    def add(metric, value, unit=None, kind="gauge", **labels):
        number = _number(value)
        if number is not None:
            samples.append(Sample(metric, {**base, **labels}, number, now, unit, kind))

    def section(what, read):
        try:
            read()
        except Exception as e:  # a menu the router doesn't have must not stop the others
            logger.debug(f"MikroTik {what}: {e}")

    def resources():
        r = rows("system", "resource")[0]
        add("mktxp_system_cpu_load", r.get("cpu-load"), "%")
        add("mktxp_system_cpu_count", r.get("cpu-count"))
        add("mktxp_system_cpu_frequency", r.get("cpu-frequency"), "MHz")
        add("mktxp_system_free_memory", r.get("free-memory"), "B")
        add("mktxp_system_total_memory", r.get("total-memory"), "B")
        add("mktxp_system_free_hdd_space", r.get("free-hdd-space"), "B")
        add("mktxp_system_total_hdd_space", r.get("total-hdd-space"), "B")
        add("mktxp_system_uptime", parse_uptime(str(r.get("uptime", ""))), "s")

    def health():
        found = rows("system", "health")
        # RouterOS 7: one row per sensor ({name, value, type}); RouterOS 6: one row of fields
        if found and "name" in found[0] and "value" in found[0]:
            for sensor in found:
                name = str(sensor.get("name", "")).replace("-", "_")
                unit = sensor.get("type") or None
                if name:
                    add(f"mktxp_system_{name}", sensor.get("value"), unit)
        elif found:
            for key, value in found[0].items():
                if key in ("temperature", "cpu-temperature", "voltage", "power-consumption", "fan1-speed"):
                    add(f"mktxp_system_{key.replace('-', '_')}", value)

    def interfaces():
        for i in rows("interface"):
            name = i.get("name")
            if not name or _true(i.get("disabled")):
                continue
            labels = {"name": str(name), "type": str(i.get("type", ""))}
            add("mktxp_interface_running", 1 if _true(i.get("running")) else 0, **labels)
            for field, metric in (
                ("rx-byte", "mktxp_interface_rx_byte_total"),
                ("tx-byte", "mktxp_interface_tx_byte_total"),
                ("rx-packet", "mktxp_interface_rx_packet_total"),
                ("tx-packet", "mktxp_interface_tx_packet_total"),
                ("rx-error", "mktxp_interface_rx_error_total"),
                ("tx-error", "mktxp_interface_tx_error_total"),
                ("rx-drop", "mktxp_interface_rx_drop_total"),
                ("tx-drop", "mktxp_interface_tx_drop_total"),
            ):
                add(metric, i.get(field), "B" if "byte" in field else None, "counter", **labels)

    def leases():
        bound = [lease for lease in rows("ip", "dhcp-server", "lease") if str(lease.get("status")) == "bound"]
        add("mktxp_dhcp_lease_active_count", len(bound))

    def wifi():
        # RouterOS 7 wifi (wifiwave2) and the older wireless package; whichever the router has
        counted = False
        for path in (("interface", "wifi", "registration-table"), ("interface", "wireless", "registration-table")):
            with contextlib.suppress(Exception):
                clients = rows(*path)
                per_interface: dict[str, int] = {}
                for client in clients:
                    key = str(client.get("interface", ""))
                    per_interface[key] = per_interface.get(key, 0) + 1
                for interface, count in per_interface.items():
                    add("mktxp_wlan_clients_count", count, name=interface)
                counted = counted or bool(clients)
        if not counted:
            add("mktxp_wlan_clients_count", 0)

    def connections():
        tracking = rows("ip", "firewall", "connection", "tracking")
        if tracking:
            add("mktxp_firewall_connections_total", tracking[0].get("total-entries"))

    section("resources", resources)
    section("health", health)
    section("interfaces", interfaces)
    section("DHCP leases", leases)
    section("Wi-Fi clients", wifi)
    section("connections", connections)
    return samples


class MikroTik:
    def __init__(
        self,
        config: Config,
        put_samples: Callable[[list[Sample]], None],
        report: Callable[[int, str | None], None],
    ):
        self.config = config
        self.put_samples = put_samples
        self.report = report  # (records, error)
        self._api = None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self.config.mikrotik_host:
            self._task = asyncio.create_task(self._loop(), name="mikrotik")
            logger.info(f"Polling the MikroTik at {self.config.mikrotik_host} (RouterOS API)")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        self._close()

    def _close(self) -> None:
        if self._api is not None:
            with contextlib.suppress(Exception):
                self._api.close()
            self._api = None

    def _connect(self):
        import librouteros

        if self._api is None:
            wrapper = None
            if self.config.mikrotik_ssl:
                # RouterOS' API-SSL certificate is the router's own, normally self-signed
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                wrapper = context.wrap_socket
            self._api = librouteros.connect(
                host=self.config.mikrotik_host,
                username=self.config.mikrotik_user,
                password=self.config.mikrotik_password,
                port=self.config.mikrotik_port or (8729 if self.config.mikrotik_ssl else 8728),
                ssl_wrapper=wrapper,
                timeout=10,
            )
        return self._api

    def _poll(self) -> list[Sample]:
        try:
            return collect(self._connect(), datetime.now(UTC))
        except Exception:
            self._close()  # reconnect next round
            raise

    async def _loop(self) -> None:
        while True:
            try:
                samples = await asyncio.to_thread(self._poll)
                if not samples:
                    raise RuntimeError("the router answered nothing (does the user have the read policy?)")
                self.put_samples(samples)
                self.report(len(samples), None)
            except Exception as e:
                message = str(e) or type(e).__name__
                logger.warning(f"MikroTik: {message}")
                self.report(0, message[:300])
            await asyncio.sleep(INTERVAL)
