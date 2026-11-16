"""The app with stand-ins for Home Assistant and the scanners, for development and for watching the screens.

    python3 -m tools.mock            # then open http://localhost:8099

It starts a stand-in Supervisor on port 8124 with about twenty device trackers (phones and a laptop that
come and go, a guest phone that turns up after a minute, one with a public address that is never scanned),
fills the database with a month of history, and runs the app against them. nuclei and naabu are replaced by
a fake that answers in seconds with believable ports and findings, so scans can be watched as they go.
--fresh starts with an empty database and no history.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import random
import sys
import tempfile
import time
from pathlib import Path

from aiohttp import web

HA_PORT = 8124
TOKEN = "mock-token"

# name, entity, ip, mac, profile: always / phone (comes and goes) / later (turns up after a minute) / away
TRACKERS = [
    ("Router", "router", "192.168.1.1", "f4:e2:c6:10:00:01", "always"),
    ("Home Assistant", "homeassistant", "192.168.1.10", "dc:a6:32:00:00:10", "always"),
    ("Synology NAS", "nas", "192.168.1.20", "00:11:32:aa:bb:20", "always"),
    ("Proxmox", "proxmox", "192.168.1.21", "3c:ec:ef:00:00:21", "always"),
    ("Printer office", "printer_office", "192.168.1.30", "a4:5d:36:00:00:30", "always"),
    ("Hikvision camera garden", "camera_garden", "192.168.1.40", "c0:56:e3:00:00:40", "always"),
    ("Doorbell", "doorbell", "192.168.1.41", "9c:8e:cd:00:00:41", "always"),
    ("Living room TV", "tv_living_room", "192.168.1.50", "a8:23:fe:00:00:50", "always"),
    ("Sonos kitchen", "sonos_kitchen", "192.168.1.51", "48:a6:b8:00:00:51", "always"),
    ("Toon thermostat", "toon", "192.168.1.60", "00:0f:11:00:00:60", "always"),
    ("GoodWe inverter", "goodwe", "192.168.1.61", "34:ea:e7:00:00:61", "always"),
    ("Bitaxe miner", "bitaxe", "192.168.1.62", "24:dc:c3:00:00:62", "always"),
    ("ESP garage door", "esp_garage", "192.168.1.70", "84:cc:a8:00:00:70", "always"),
    ("Old Windows PC", "windows_pc", "192.168.1.80", "00:1a:a0:00:00:80", "always"),
    ("Ron's phone", "phone_ron", "192.168.1.100", "7a:11:22:33:44:01", "phone"),
    ("Partner's phone", "phone_partner", "192.168.1.101", "7a:11:22:33:44:02", "phone"),
    ("Work laptop", "laptop_work", "192.168.1.110", "f0:18:98:00:01:10", "phone"),
    ("Guest phone", "guest_phone", "192.168.1.150", "92:aa:bb:cc:dd:50", "later"),
    ("Kindle", "kindle", "192.168.1.120", "44:65:0d:00:01:20", "away"),
    ("VPS (public)", "vps", "185.199.108.25", None, "always"),
]

# ip -> open ports the fake naabu reports
PORTS = {
    "192.168.1.1": [22, 53, 80, 443],
    "192.168.1.10": [22, 8123],
    "192.168.1.20": [22, 80, 139, 445, 5000, 5001],
    "192.168.1.21": [22, 111, 8006],
    "192.168.1.30": [80, 443, 631, 9100, 161],
    "192.168.1.40": [80, 554, 8000],
    "192.168.1.41": [80, 443],
    "192.168.1.50": [8008, 8009, 9000],
    "192.168.1.51": [1400, 1443],
    "192.168.1.60": [22, 80, 10080],
    "192.168.1.61": [8899],
    "192.168.1.62": [80],
    "192.168.1.70": [23, 80, 6053],
    "192.168.1.80": [135, 139, 445, 3389, 5900],
    "192.168.1.100": [],
    "192.168.1.101": [62078],
    "192.168.1.110": [22, 5000, 7000],
    "192.168.1.150": [8080],
}


def finding(tid, name, sev, desc, fix, cve=None, cvss=None, path="", refs=()):
    return {"template-id": tid, "name": name, "severity": sev, "description": desc, "remediation": fix,
            "cve": cve, "cvss": cvss, "path": path, "refs": list(refs)}  # fmt: skip


# ip:port -> what the fake nuclei finds there
FINDINGS = {
    "192.168.1.1:80": [
        finding(
            "http-missing-security-headers",
            "HTTP security headers missing",
            "low",
            "The web interface does not send headers such as Content-Security-Policy or X-Frame-Options.",
            "Enable the security headers in the router's web server settings, or reach it only over HTTPS.",
        )
    ],
    "192.168.1.1:443": [
        finding(
            "weak-cipher-suites",
            "Weak TLS cipher suites",
            "medium",
            "The TLS service accepts cipher suites considered weak (CBC with SHA1).",
            "Turn off TLS 1.0/1.1 and the weak suites in the router's management settings.",
        )
    ],
    "192.168.1.20:5000": [
        finding(
            "CVE-2024-10443",
            "Synology DSM - RCE in Replicator (RISK:STATION)",
            "critical",
            "A zero-click flaw in the Replicator service of Synology DSM lets an attacker run commands without logging in.",
            "Update DSM to 7.2.2-72806 Update 2 or later.",
            "CVE-2024-10443",
            9.8,
            "/webapi/entry.cgi",
            ["https://www.synology.com/en-global/security/advisory/Synology_SA_24_20"],
        )
    ],
    "192.168.1.20:445": [
        finding(
            "smb-signing-not-required",
            "SMB signing not required",
            "medium",
            "The SMB server does not require signing, which allows relay attacks on the local network.",
            "Require SMB signing in Control Panel → File Services → SMB → Advanced.",
        )
    ],
    "192.168.1.21:8006": [
        finding(
            "proxmox-ve-version",
            "Proxmox VE login page exposed",
            "low",
            "The Proxmox VE management interface is reachable from the whole network.",
            "Limit access to port 8006 to a management VLAN or a few hosts.",
        )
    ],
    "192.168.1.30:161": [
        finding(
            "snmp-default-community",
            "SNMP public community string",
            "high",
            "The printer answers SNMP with the default community string 'public', which discloses configuration.",
            "Change or turn off SNMPv1/v2c and use SNMPv3.",
            None,
            7.5,
        )
    ],
    "192.168.1.30:80": [
        finding(
            "CVE-2023-2533",
            "PaperCut-style printer web CSRF",
            "medium",
            "The printer's embedded web server is vulnerable to cross-site request forgery on its settings page.",
            "Install the latest firmware from the printer maker.",
            "CVE-2023-2533",
            6.5,
            "/hp/device/save",
        )
    ],
    "192.168.1.40:80": [
        finding(
            "CVE-2021-36260",
            "Hikvision IP camera - command injection",
            "critical",
            "A command injection in the web server of many Hikvision cameras gives full control without a login.",
            "Update the camera firmware to the version in Hikvision's advisory HSRC-202109-01.",
            "CVE-2021-36260",
            9.8,
            "/SDK/webLanguage",
            ["https://www.hikvision.com/en/support/cybersecurity/security-advisory/"],
        ),
        finding(
            "hikvision-default-login",
            "Hikvision default login (admin/12345)",
            "high",
            "The camera accepts its factory default password.",
            "Set a strong password on the camera.",
        ),
    ],
    "192.168.1.40:554": [
        finding(
            "rtsp-unauthenticated",
            "RTSP stream without authentication",
            "high",
            "The camera's video stream can be opened by anyone on the network without a password.",
            "Turn on RTSP authentication in the camera's network settings.",
        )
    ],
    "192.168.1.41:443": [
        finding(
            "expired-ssl",
            "Expired TLS certificate",
            "low",
            "The device's certificate expired; clients learn to ignore certificate warnings.",
            "Renew the certificate or update the firmware.",
        )
    ],
    "192.168.1.50:8008": [
        finding(
            "chromecast-api-exposed",
            "Google Cast setup API unauthenticated",
            "low",
            "The Cast setup API answers without authentication and reveals device details.",
            "Nothing to change on the TV; keep it on a separate IoT network.",
        )
    ],
    "192.168.1.60:22": [
        finding(
            "ssh-password-auth",
            "SSH allows password logins",
            "medium",
            "The SSH server accepts passwords, so it can be brute-forced.",
            "Use SSH keys and set PasswordAuthentication no.",
        )
    ],
    "192.168.1.60:10080": [
        finding(
            "CVE-2019-11477",
            "Linux kernel TCP SACK panic (old kernel)",
            "high",
            "The device runs a Linux kernel vulnerable to SACK Panic, a remote denial of service.",
            "Update the device's firmware; there is no newer firmware for a rooted Toon, so isolate it.",
            "CVE-2019-11477",
            7.5,
        )
    ],
    "192.168.1.62:80": [
        finding(
            "bitaxe-axeos-unauth",
            "AxeOS API without authentication",
            "medium",
            "Anyone on the network can change the miner's pool, frequency and voltage through its API.",
            "Keep the miner on a separate network; AxeOS has no login.",
        )
    ],
    "192.168.1.70:80": [
        finding(
            "esphome-web-unauth",
            "ESPHome web server without password",
            "medium",
            "The ESPHome web server lets anyone switch the garage door relay.",
            "Set web_server: auth: in the ESPHome YAML, or remove the web server.",
        )
    ],
    "192.168.1.80:3389": [
        finding(
            "CVE-2019-0708",
            "BlueKeep - RDP remote code execution",
            "critical",
            "This Windows version's Remote Desktop service allows code execution without logging in.",
            "Install the Windows update for CVE-2019-0708, or turn off Remote Desktop.",
            "CVE-2019-0708",
            9.8,
            "",
            ["https://msrc.microsoft.com/update-guide/vulnerability/CVE-2019-0708"],
        )
    ],
    "192.168.1.80:445": [
        finding(
            "CVE-2017-0144",
            "EternalBlue - SMBv1 remote code execution",
            "critical",
            "SMBv1 on this host is vulnerable to EternalBlue, used by WannaCry.",
            "Install MS17-010 and turn off SMBv1.",
            "CVE-2017-0144",
            8.1,
        )
    ],
    "192.168.1.80:5900": [
        finding(
            "vnc-no-auth",
            "VNC without authentication",
            "high",
            "The VNC server accepts connections without a password.",
            "Set a VNC password or turn the service off.",
        )
    ],
    "192.168.1.110:5000": [
        finding(
            "airplay-receiver-exposed",
            "AirPlay receiver open to the network",
            "info",
            "AirPlay receiver is on.",
            "Turn off AirPlay Receiver when not needed.",
        )
    ],
    "192.168.1.150:8080": [
        finding(
            "CVE-2024-4577",
            "PHP-CGI argument injection",
            "critical",
            "A development web server on the guest phone runs PHP-CGI vulnerable to argument injection.",
            "Ask the guest to stop the server; it shouldn't run on a phone at all.",
            "CVE-2024-4577",
            9.8,
            "/index.php",
        )
    ],
}


class FakeScanners:
    """Answers the argv the app builds for nuclei and naabu, with a short wait so a scan can be watched."""

    def __init__(self, speed: float = 1.0):
        self.speed = speed
        self.templates_dir: Path | None = None

    async def run(self, argv: list[str], on_line) -> tuple[int, str]:
        prog = argv[0]
        if prog == "naabu" and "-version" in argv:
            return 0, "Current Version: 2.7.0"
        if prog == "nuclei" and "-version" in argv:
            return 0, "Nuclei Engine Version: v3.11.1"
        if prog == "nuclei" and "-templates-version" in argv:
            return 0, "Public nuclei-templates version: v10.3.4"
        if prog == "nuclei" and "-update-templates" in argv:
            await asyncio.sleep(4 * self.speed)
            self._touch_templates()
            return 0, ""
        if prog == "naabu":
            hosts = argv[argv.index("-host") + 1].split(",")
            await asyncio.sleep(random.uniform(4, 8) * self.speed)
            for host in hosts:
                for port in PORTS.get(host, []):
                    on_line(json.dumps({"ip": host, "port": port}))
            return 0, ""
        if prog == "nuclei":
            targets = Path(argv[argv.index("-l") + 1]).read_text().split()
            severities = argv[argv.index("-s") + 1].split(",")
            for target in targets:
                await asyncio.sleep(random.uniform(2, 5) * self.speed)
                host, port = target.rsplit(":", 1)
                for f in FINDINGS.get(target, []):
                    if f["severity"] not in severities:
                        continue
                    on_line(json.dumps({
                        "template-id": f["template-id"], "ip": host, "host": host, "port": port,
                        "matched-at": f"http://{target}{f['path']}" if f["path"] else target,
                        "info": {"name": f["name"], "severity": f["severity"], "description": f["description"],
                                 "remediation": f["remediation"], "reference": f["refs"],
                                 "classification": {"cve-id": [f["cve"]] if f["cve"] else [], "cvss-score": f["cvss"]}},
                    }))  # fmt: skip
            return 0, ""
        return 1, f"{prog}: not faked"

    def _touch_templates(self) -> None:
        assert self.templates_dir
        stamp = self.templates_dir.parent / "templates-updated-at"
        stamp.write_text(str(int(time.time())))


def fake_template_stats() -> dict:
    return {"total": 11873, "cves": 3264, "newest_cve": "CVE-2026-41820"}


async def fake_latest(repo: str) -> str | None:
    return {"projectdiscovery/nuclei": "3.11.2", "projectdiscovery/naabu": "2.7.0",
            "projectdiscovery/nuclei-templates": "10.3.4"}.get(repo)  # fmt: skip


# -- the stand-in Supervisor / Home Assistant ---------------------------------------------------------
START = time.time()


def tracker_home(profile: str, entity: str) -> bool:
    if profile == "always":
        return True
    if profile == "away":
        return False
    if profile == "later":
        return time.time() - START > 60
    # phones: home most of the time, away for a few minutes now and then, each on its own rhythm
    seed = int(hashlib.md5(entity.encode()).hexdigest()[:4], 16)
    return ((time.time() + seed) % 600) > 140


async def states(request: web.Request) -> web.Response:
    if request.headers.get("Authorization") != f"Bearer {TOKEN}":
        return web.json_response({"message": "401: Unauthorized"}, status=401)
    out = []
    for name, entity, ip, mac, profile in TRACKERS:
        home = tracker_home(profile, entity)
        attributes = {"friendly_name": name, "source_type": "router"}
        if home:
            attributes["ip"] = ip
        if mac:
            attributes["mac"] = mac
        out.append({"entity_id": f"device_tracker.{entity}", "state": "home" if home else "not_home",
                    "attributes": attributes})  # fmt: skip
    out.append({"entity_id": "sensor.not_a_tracker", "state": "3", "attributes": {}})
    return web.json_response(out)


async def start_ha() -> None:
    app = web.Application()
    app.router.add_get("/core/api/states", states)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", HA_PORT).start()


# -- a month of history ------------------------------------------------------------------------------
def seed(database) -> None:
    """Thirty days of the trend, ending at today's issues: worse a month ago, better as things got fixed."""
    from src import score

    now = int(time.time())
    rng = random.Random(7)
    counts = database.counts()
    for back in range(1, 31):
        # Walking back in time: things fixed since then were still there
        if back in (3, 8, 15, 23):
            counts["critical"] += 1
        if back % 5 == 0:
            counts["high"] += 1
        counts["medium"] = max(4, counts["medium"] + rng.choice((-1, 0, 1, 1)))
        counts["low"] = max(2, counts["low"] + rng.choice((-1, 0, 1)))
        stamp = time.strftime("%Y-%m-%d", time.localtime(now - back * 86400))
        database._db.execute(
            "INSERT OR REPLACE INTO daily VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (stamp, score.score(counts), counts["critical"], counts["high"], counts["medium"], counts["low"],
             rng.randint(11, 14), rng.randint(1, 4)),
        )  # fmt: skip
    database._db.commit()


async def preload(engine, database, scanned_ago: dict[str, int]) -> None:
    """Scan some devices at full speed before the app starts, then move their dates back."""
    from src.trackers import parse_states

    class Req:
        headers = {"Authorization": f"Bearer {TOKEN}"}

    trackers = parse_states(json.loads((await states(Req())).body))
    database.sync_trackers(trackers)
    for device in database.devices():
        if device["entity_id"] in scanned_ago and device["scannable"]:
            await engine.scan_device(device, "rescan")
    for entity, days in scanned_ago.items():
        when = int(time.time()) - days * 86400
        row = database._row("SELECT id FROM devices WHERE entity_id = ?", (entity,))
        if not row:
            continue
        database._db.execute(
            "UPDATE devices SET last_scan_at = ?, first_seen = ? WHERE id = ?", (when, when - 20 * 86400, row["id"])
        )
        database._db.execute(
            "UPDATE issues SET first_seen = ?, last_seen = ? WHERE device_id = ?", (when - days * 3600, when, row["id"])
        )
        database._db.execute(
            "UPDATE scans SET started_at = ?, finished_at = ? + 300 WHERE device_id = ?", (when, when, row["id"])
        )
    # Some issues were seen and accepted before; one was fixed last week
    database._db.execute(
        "UPDATE issues SET state = 'acknowledged', acknowledged_at = first_seen + 86400"
        " WHERE template_id IN ('chromecast-api-exposed', 'bitaxe-axeos-unauth', 'open-port-111', 'proxmox-ve-version')"
    )
    database._db.execute(
        "INSERT INTO issues (fingerprint, device_id, kind, template_id, name, severity, cve, cvss, description,"
        " remediation, state, first_seen, last_seen, resolved_at, port)"
        " SELECT 'seed-fixed', id, 'vulnerability', 'CVE-2023-4966', 'Citrix Bleed-style session leak', 'critical',"
        " 'CVE-2023-4966', 9.4, 'Fixed by a firmware update.', 'Update the firmware.', 'resolved', ?, ?, ?, 443"
        " FROM devices WHERE entity_id = 'device_tracker.router'",
        (int(time.time()) - 25 * 86400, int(time.time()) - 6 * 86400, int(time.time()) - 5 * 86400),
    )
    database._db.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fresh", action="store_true", help="start with an empty database")
    parser.add_argument("--speed", type=float, default=1.0, help="how slow the fake scans are (1 = a minute or so)")
    parser.add_argument("--data", default=os.path.join(tempfile.gettempdir(), "nuclei-mock"), help="data folder")
    args = parser.parse_args()

    data = Path(args.data)
    data.mkdir(parents=True, exist_ok=True)
    for name in ("scanner.db", "scanner.db-wal", "scanner.db-shm", "templates-updated-at"):
        (data / name).unlink(missing_ok=True)
    os.environ["NUCLEI_APP_DATA_DIR"] = str(data)
    os.environ["NUCLEI_APP_SUPERVISOR_URL"] = f"http://127.0.0.1:{HA_PORT}"
    os.environ.pop("SUPERVISOR_TOKEN", None)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%H:%M:%S")

    # Imported after the environment is set: the app reads its folders when it loads
    from src import __main__ as app
    from src import scanner as scanner_module
    from src.config import Config
    from src.database import Database
    from src.engine import Engine
    from src.freshness import Freshness
    from src.trackers import Trackers

    fake = FakeScanners(args.speed)
    fake.templates_dir = scanner_module.TEMPLATES_DIR
    scanner_module.Scanner.template_stats = staticmethod(fake_template_stats)
    scanner_module.Scanner.template_count = staticmethod(lambda: 11873)
    config = Config(rescan_days=7)

    async def go() -> int:
        await start_ha()
        if not args.fresh:
            (data / "templates-updated-at").write_text(str(int(time.time()) - 5 * 3600))
            quick = FakeScanners(0)
            quick.templates_dir = fake.templates_dir
            database = Database(data / "scanner.db")
            engine = Engine(
                database, Config(rescan_days=7, update_templates=False), scanner_module.Scanner(config, run=quick.run)
            )
            await preload(engine, database, {
                "device_tracker.router": 2, "device_tracker.homeassistant": 1, "device_tracker.nas": 3,
                "device_tracker.proxmox": 2, "device_tracker.printer_office": 4, "device_tracker.camera_garden": 1,
                "device_tracker.tv_living_room": 5, "device_tracker.sonos_kitchen": 5, "device_tracker.goodwe": 6,
                "device_tracker.bitaxe": 2, "device_tracker.windows_pc": 3, "device_tracker.phone_partner": 9,
                "device_tracker.laptop_work": 12,
            })  # fmt: skip
            database.snapshot_day()
            seed(database)
            database.close()
        scanner = scanner_module.Scanner(config, run=fake.run)
        print(f"\n  Nuclei mock: open http://localhost:{app.WEB_PORT}\n", flush=True)
        return await app.run(
            config, scanner=scanner, trackers=Trackers(TOKEN), freshness=Freshness(scanner, fake_latest)
        )

    try:
        return asyncio.run(go())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
