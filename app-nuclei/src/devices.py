"""What the open ports of a device are, and which of them should not be exposed on a network."""

# Port to its usual service name; a label for what a device exposes
SERVICE_NAMES = {
    21: "ftp",
    22: "ssh",
    23: "telnet",
    25: "smtp",
    53: "dns",
    69: "tftp",
    80: "http",
    110: "pop3",
    111: "rpcbind",
    123: "ntp",
    135: "msrpc",
    137: "netbios-ns",
    139: "netbios-ssn",
    143: "imap",
    161: "snmp",
    389: "ldap",
    443: "https",
    445: "smb",
    465: "smtps",
    512: "rexec",
    513: "rlogin",
    514: "rsh",
    548: "afp",
    554: "rtsp",
    587: "submission",
    631: "ipp",
    636: "ldaps",
    873: "rsync",
    993: "imaps",
    995: "pop3s",
    1433: "mssql",
    1521: "oracle",
    1723: "pptp",
    1883: "mqtt",
    1900: "ssdp",
    2049: "nfs",
    2375: "docker-api",
    3000: "http-dev",
    3306: "mysql",
    3389: "rdp",
    5000: "upnp",
    5060: "sip",
    5432: "postgres",
    5900: "vnc",
    5985: "winrm",
    6379: "redis",
    8000: "http-alt",
    8080: "http-proxy",
    8443: "https-alt",
    8554: "rtsp-alt",
    8883: "mqtts",
    9100: "jetdirect",
    9200: "elasticsearch",
    11211: "memcached",
    27017: "mongodb",
    32400: "plex",
}

# Services that shouldn't normally be exposed on a network. Each maps to (severity, why, what to do).
RISKY_SERVICES = {
    23: (
        "high",
        "Telnet sends everything, passwords included, in clear text.",
        "Turn Telnet off and use SSH. If the device only offers Telnet, isolate it on its own network.",
    ),
    21: ("medium", "FTP sends logins and files in clear text.", "Use SFTP or FTPS instead, or turn the service off."),
    69: ("medium", "TFTP has no authentication.", "Turn it off unless it is needed, and limit it to one host."),
    512: ("high", "rexec is a legacy remote shell with weak authentication.", "Turn it off and use SSH."),
    513: ("high", "rlogin is a legacy remote login with weak authentication.", "Turn it off and use SSH."),
    514: ("high", "rsh is a legacy remote shell with weak authentication.", "Turn it off and use SSH."),
    445: (
        "medium",
        "SMB file sharing is a common route for ransomware and lateral movement.",
        "Turn off SMBv1, require signing, and keep SMB off hosts that don't need file sharing.",
    ),
    139: ("medium", "NetBIOS exposes host and share information.", "Turn it off where it isn't needed."),
    3389: (
        "high",
        "Remote Desktop is a frequent target for password guessing and exploits.",
        "Put it behind a VPN, require network-level authentication, and limit who can reach it.",
    ),
    5900: (
        "high",
        "VNC often has weak or no authentication.",
        "Put it behind a VPN or SSH tunnel and set a strong password.",
    ),
    5985: ("medium", "WinRM allows remote administration over HTTP.", "Limit it to a management network."),
    161: (
        "medium",
        "SNMP with default community strings exposes device details.",
        "Use SNMPv3, change default community strings, and limit who can query.",
    ),
    2375: (
        "critical",
        "The Docker API is open without authentication: whoever reaches it controls the host.",
        "Turn off the TCP socket, or protect it with TLS client certificates.",
    ),
    6379: (
        "high",
        "Redis is often open without a password.",
        "Bind it to localhost or set a password and firewall it.",
    ),
    27017: ("high", "MongoDB is often open without authentication.", "Enable authentication and firewall the port."),
    11211: (
        "high",
        "Memcached has no authentication and is abused for attacks.",
        "Bind it to localhost or firewall it.",
    ),
    9200: ("high", "Elasticsearch is often open without authentication.", "Enable security and firewall the port."),
    1433: ("medium", "A database port reachable from the network.", "Limit it to the hosts that need it."),
    3306: ("medium", "A database port reachable from the network.", "Limit it to the hosts that need it."),
    5432: ("medium", "A database port reachable from the network.", "Limit it to the hosts that need it."),
    1521: ("medium", "A database port reachable from the network.", "Limit it to the hosts that need it."),
    2049: ("medium", "NFS can expose file systems to the network.", "Limit exports to specific hosts."),
    873: (
        "medium",
        "rsync can expose files without authentication.",
        "Require authentication and limit who can reach it.",
    ),
    111: ("low", "rpcbind lists services running on the host.", "Turn it off where it isn't needed."),
}


def service_name(port: int) -> str:
    return SERVICE_NAMES.get(port, "unknown")


def services(open_ports: list[int]) -> list[dict]:
    """What a host exposes: each open port with its usual service name and whether it is risky."""
    return [
        {"port": port, "service": service_name(port), "risky": port in RISKY_SERVICES}
        for port in sorted(set(open_ports))
    ]


def risky_findings(host: str, open_ports: list[int]) -> list[dict]:
    """Issues for services that shouldn't be exposed, one per open port."""
    out = []
    for port in sorted(set(open_ports)):
        if port not in RISKY_SERVICES:
            continue
        severity, why, fix = RISKY_SERVICES[port]
        name = service_name(port)
        out.append(
            {
                "host": host,
                "port": port,
                "kind": "open_port",
                "template_id": f"open-port-{port}",
                "name": f"{name.upper()} exposed on port {port}",
                "severity": severity,
                "cve": None,
                "cvss": None,
                "matched_at": f"{host}:{port}",
                "proof": f"Port {port} ({name}) accepted a connection on {host}.",
                "description": why,
                "remediation": fix,
                "reference": "",
            }
        )
    return out
