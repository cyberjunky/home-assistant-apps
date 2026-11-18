"""Mosquitto's log, from its stdout (an s6 pipeline), to two places.

- The app's log (stdout): every line, except the subscribe and unsubscribe lines when the options
  don't ask for them (the dashboard needs them, so the broker always writes them).
- A file for the dashboard, /run/mqtt/broker.log, moved to broker.log.1 at 4 MiB.

It must never block the broker: lines are written as they come, and a failing write is dropped.
"""

import os
import re
import sys
from pathlib import Path

LOG = Path(os.environ.get("MQTT_BROKER_LOG", "/run/mqtt/broker.log"))
MAX_SIZE = 4 * 1024 * 1024
# "<time>: <client> <qos> <topic>" and "<time>: <client> <topic>"
_SUBSCRIPTION = re.compile(r"^\S+: (\S+ \d+ \S+|\S+ \S+)$")
_BROKER_LINE = re.compile(r"^\S+: (Bridge|Persistence|TLS|TLS-PSK|Websockets|Opening|Info:|Warning:|Error:|Config)\b")


_PROBE = re.compile(
    r"^\S+: (New connection from (127\.0\.0\.1|::1):\d+ on port \d+\."
    r"|Client (127\.0\.0\.1|::1) \[\S+\] disconnected.*"
    r"|Protocol error from (127\.0\.0\.1|::1):\d+: First packet not CONNECT.*)$"
)


def is_subscription(line: str) -> bool:
    return bool(_SUBSCRIPTION.match(line)) and not _BROKER_LINE.match(line)


def is_probe(line: str) -> bool:
    """The health check every 30 seconds, and bashio's port probes: connections that never log in."""
    return bool(_PROBE.match(line))


def main() -> None:
    show_subscriptions = Path("/run/mqtt/show_subscriptions").exists() or "--show-subscriptions" in sys.argv
    LOG.parent.mkdir(parents=True, exist_ok=True)
    out = LOG.open("a", buffering=1, errors="replace")
    stdin = open(sys.stdin.fileno(), errors="replace", buffering=1, closefd=False)  # noqa: SIM115
    for line in stdin:
        text = line.rstrip("\n")
        if not is_probe(text) and (show_subscriptions or not is_subscription(text)):
            try:
                sys.stdout.write(line)
                sys.stdout.flush()
            except OSError:
                pass
        try:
            out.write(line)
            if out.tell() > MAX_SIZE:
                out.close()
                os.replace(LOG, LOG.with_name(LOG.name + ".1"))
                out = LOG.open("a", buffering=1, errors="replace")
        except OSError:
            try:
                out.close()
                out = LOG.open("a", buffering=1, errors="replace")
            except OSError:
                pass


if __name__ == "__main__":
    main()
