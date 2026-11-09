"""Scan targets and the ownership gate.

A scan only ever runs against objects registered in the app, and only after the person has
acknowledged they are allowed to scan them (ScanObject.authorised). A public IP or an ASN cannot
be scanned until it is expanded to a list of hosts the person has seen and approved. Private ranges
(RFC1918, loopback, link-local) are treated as yours and need no expansion step, but still carry the
authorisation flag so nothing scans by accident.
"""

import ipaddress
import re

# Expanding one registered object may never produce more than this many hosts, so a wide prefix
# can't turn into a sweep of the internet by mistake.
MAX_HOSTS_PER_OBJECT = 4096

_HOSTNAME = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$")
_ASN = re.compile(r"^AS\d{1,10}$", re.IGNORECASE)


class TargetError(ValueError):
    """A target is malformed or too broad to scan."""


def classify(target: str) -> str:
    """What kind of target this is: 'ip', 'cidr', 'host', 'url', 'asn'. Raises TargetError otherwise."""
    target = target.strip()
    if not target:
        raise TargetError("empty target")
    if _ASN.match(target):
        return "asn"
    if target.startswith(("http://", "https://")):
        return "url"
    if "/" in target:
        try:
            ipaddress.ip_network(target, strict=False)
        except ValueError as e:
            raise TargetError(f"not a valid network: {target}") from e
        return "cidr"
    try:
        ipaddress.ip_address(target)
        return "ip"
    except ValueError:
        pass
    if _HOSTNAME.match(target):
        return "host"
    raise TargetError(f"not an IP, network, host name, URL or ASN: {target}")


def is_private(target: str) -> bool:
    """Whether a target is unquestionably yours (private, loopback or link-local), so it needs no
    ownership preview before scanning. Host names and URLs are never assumed private."""
    kind = classify(target)
    if kind == "ip":
        ip = ipaddress.ip_address(target)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    if kind == "cidr":
        net = ipaddress.ip_network(target, strict=False)
        return net.is_private or net.is_loopback or net.is_link_local
    return False


def hosts_of(target: str) -> list[str]:
    """The addresses a CIDR or IP target covers, for the approval preview. Raises TargetError when a
    network is too large, or when the target isn't something we expand (host, url, asn)."""
    kind = classify(target)
    if kind == "ip":
        return [target.strip()]
    if kind != "cidr":
        raise TargetError(f"{kind} targets are not expanded to hosts")
    net = ipaddress.ip_network(target.strip(), strict=False)
    count = net.num_addresses
    hosts = net.hosts() if count > 2 else net
    usable = count - 2 if count > 2 else count
    if usable > MAX_HOSTS_PER_OBJECT:
        raise TargetError(f"{target} covers {usable} hosts; split it into blocks of at most {MAX_HOSTS_PER_OBJECT}")
    return [str(h) for h in hosts]


def normalise(target: str) -> str:
    """A target in its canonical form, so the same object isn't registered twice spelled differently."""
    kind = classify(target)
    target = target.strip()
    if kind == "asn":
        return target.upper()
    if kind == "ip":
        return str(ipaddress.ip_address(target))
    if kind == "cidr":
        return str(ipaddress.ip_network(target, strict=False))
    if kind == "host":
        return target.lower()
    return target
