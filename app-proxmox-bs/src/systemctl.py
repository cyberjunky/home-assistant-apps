"""The app's systemctl: what PBS asks of systemd, done without systemd.

PBS calls systemctl to mount the disks it sets up (daemon-reload, enable, start a .mount unit;
disable and stop one when a directory is removed), and to show and reload its own two services.
Everything else is accepted and does nothing, as there is nothing else to manage in the app.
"""

import os
import signal
import subprocess
import sys
from pathlib import Path

# Run as a script by /usr/bin/systemctl: make the package importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import mounts

# PBS's services, and the s6 service that runs each
SERVICES = {
    "proxmox-backup.service": ("pbs-api", "Proxmox Backup API Server", "/run/proxmox-backup/api.pid"),
    "proxmox-backup-proxy.service": ("pbs-proxy", "Proxmox Backup API Proxy Server", "/run/proxmox-backup/proxy.pid"),
}


def service_name(name: str) -> str:
    return name if "." in name else f"{name}.service"


def show(name: str, props: list[str]) -> str:
    unit = service_name(name)
    if unit in SERVICES:
        info = {
            "Id": unit,
            "Names": unit,
            "Description": SERVICES[unit][1],
            "LoadState": "loaded",
            "ActiveState": "active",
            "SubState": "running",
            "UnitFileState": "enabled",
        }
    elif unit.endswith(".mount") and (u := mounts.find_unit(unit)):
        active = u.where in mounts.mount_table()
        info = {
            "Id": unit,
            "Names": unit,
            "Description": f"Mount {u.where}",
            "LoadState": "loaded",
            "ActiveState": "active" if active else "inactive",
            "SubState": "mounted" if active else "dead",
            "UnitFileState": "enabled",
        }
    else:
        info = {"Id": unit, "Names": unit, "LoadState": "not-found", "ActiveState": "inactive", "SubState": "dead"}
    keys = props or list(info)
    return "\n".join(f"{k}={info.get(k, '')}" for k in keys)


def signal_service(unit: str, how: str) -> int:
    service, _, pidfile = SERVICES[unit]
    if how == "reload":
        try:
            pid = int(Path(pidfile).read_text().strip())
            os.kill(pid, signal.SIGHUP)
            return 0
        except (OSError, ValueError):
            pass
    # A restart: s6 starts the service again
    return subprocess.run(["s6-svc", "-r", f"/run/service/{service}"], check=False).returncode


def main(argv: list[str]) -> int:
    props: list[str] = []
    args: list[str] = []
    it = iter(argv)
    for a in it:
        if a in ("-p", "--property"):
            props += next(it, "").split(",")
        elif a.startswith("--property="):
            props += a.split("=", 1)[1].split(",")
        elif not a.startswith("-"):
            args.append(a)
    if not args:
        return 0
    command, names = args[0], args[1:]

    if command in ("daemon-reload", "reset-failed", "mask", "unmask", "enable", "disable", "daemon-reexec"):
        return 0
    if command == "show":
        print("\n\n".join(show(n, props) for n in names))
        return 0
    if command == "is-enabled":
        ok = all(service_name(n) in SERVICES or mounts.find_unit(service_name(n)) for n in names)
        print("enabled" if ok else "disabled")
        return 0 if ok else 1
    if command == "is-active":
        states = []
        for n in names:
            unit = service_name(n)
            u = mounts.find_unit(unit) if unit.endswith(".mount") else None
            states.append("active" if unit in SERVICES or (u and u.where in mounts.mount_table()) else "inactive")
        print("\n".join(states))
        return 0 if all(s == "active" for s in states) else 3

    code = 0
    for n in names:
        unit = service_name(n)
        if unit.endswith(".mount"):
            u = mounts.find_unit(unit)
            if u is None:
                print(f"Unit {unit} not found.", file=sys.stderr)
                code = 5
                continue
            if command in ("start", "restart", "reload-or-restart", "try-restart"):
                if command == "restart":
                    mounts.unmount(u.where)
                ok, message = mounts.mount(u)
            elif command == "stop":
                ok, message = mounts.unmount(u.where)
            else:
                ok, message = True, ""
            if not ok:
                print(f"{unit}: {message}", file=sys.stderr)
                code = 1
        elif unit in SERVICES and command in (
            "reload",
            "restart",
            "reload-or-restart",
            "try-restart",
            "try-reload-or-restart",
        ):
            code = signal_service(unit, "reload" if "reload" in command else "restart") or code
        # Starting or stopping anything else: there is nothing else in the app
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
