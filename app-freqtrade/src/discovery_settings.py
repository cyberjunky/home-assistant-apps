"""Validated, opt-in discovery of a bounded set of API ports on one IP address."""

import ipaddress
from dataclasses import dataclass, field


@dataclass
class DiscoveryConfig:
    enabled: bool = False
    host: str = ""
    ports: tuple[int, ...] = (8080,)
    interval_seconds: int = 120
    scheme: str = "http"
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    ws_token: str = field(default="", repr=False)
    verify_ssl: bool = True

    def url(self, port: int) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{host}:{port}"


def parse_ports(value: str) -> tuple[int, ...]:
    ports = set()
    try:
        for part in value.split(","):
            bounds = part.strip().split("-")
            if len(bounds) not in (1, 2):
                raise ValueError
            start, end = int(bounds[0]), int(bounds[-1])
            if not 1 <= start <= end <= 65535 or end - start >= 4096:
                raise ValueError
            ports.update(range(start, end + 1))
            if len(ports) > 4096:
                raise ValueError
    except (ValueError, AttributeError) as exc:
        raise ValueError("discovery.ports: use ports or ranges, at most 4096 ports (1-65535)") from exc
    if not ports:
        raise ValueError("discovery.ports must not be empty")
    return tuple(sorted(ports))


def parse_discovery(options: dict, shared: dict | None = None) -> DiscoveryConfig:
    if not options.get("enabled", False):
        return DiscoveryConfig()
    try:
        address = ipaddress.ip_address(str(options.get("host", "")))
        if address.is_multicast or address.is_unspecified:
            raise ValueError
    except ValueError as exc:
        raise ValueError("discovery.host must be one unicast IP address (not a hostname or network)") from exc
    scheme = options.get("scheme", "http")
    interval = int(options.get("interval_seconds", 120))
    if scheme not in ("http", "https") or not 30 <= interval <= 3600:
        raise ValueError("discovery requires http/https and an interval of 30-3600 seconds")
    own_login = bool(options.get("username") or options.get("password"))
    login = options if own_login else (shared or {})
    if not login.get("username") or not login.get("password"):
        raise ValueError("discovery requires both username and password")
    return DiscoveryConfig(
        enabled=True,
        host=str(address),
        ports=parse_ports(options.get("ports", "8080")),
        interval_seconds=interval,
        scheme=scheme,
        username=str(login["username"]),
        password=str(login["password"]),
        ws_token=str(options.get("ws_token", "" if own_login else login.get("ws_token", "")) or ""),
        verify_ssl=bool(options.get("verify_ssl", True)),
    )
