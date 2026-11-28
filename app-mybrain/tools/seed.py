"""Fill a development instance with real links and notes, spread over the last months, for trying the
pages and for screenshots. Shares go through tools/mock_ha.py, as from a phone; afterwards the dates
and statuses are spread out in the database directly (development only).

    python3 -m tools.seed <data dir>      # with tools.mock_ha and the app running
"""

import json
import random
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

MOCK = "http://127.0.0.1:18123/mock/share"
APP = "http://127.0.0.1:18124/api/status"

SHARES = [
    {"url": "https://github.com/home-assistant/core", "caller": "com.android.chrome"},
    {"url": "https://github.com/esphome/esphome", "caller": "com.android.chrome"},
    {"url": "https://github.com/karakeep-app/karakeep", "caller": "com.android.chrome"},
    {"url": "https://github.com/Koenkk/zigbee2mqtt", "caller": "com.android.chrome"},
    {"url": "https://github.com/freqtrade/freqtrade", "caller": "com.github.android"},
    {"url": "https://github.com/bitcoin/bitcoin", "caller": "com.github.android"},
    {"url": "https://www.home-assistant.io/integrations/ai_task/", "caller": "com.android.chrome"},
    {"url": "https://www.home-assistant.io/blog/", "caller": "com.android.chrome"},
    {"url": "https://esphome.io/", "caller": "com.android.chrome"},
    {"url": "https://en.wikipedia.org/wiki/Heat_pump", "caller": "com.android.chrome"},
    {"url": "https://en.wikipedia.org/wiki/Sourdough", "caller": "com.android.chrome"},
    {"url": "https://en.wikipedia.org/wiki/Lisbon", "caller": "com.android.chrome"},
    {"url": "https://en.wikipedia.org/wiki/Raspberry_Pi", "caller": "com.android.chrome"},
    {"url": "https://en.wikipedia.org/wiki/Solar_panel", "caller": "com.android.chrome"},
    {"url": "https://en.wikipedia.org/wiki/Zigbee", "caller": "com.android.chrome"},
    {"url": "https://en.wikipedia.org/wiki/Risotto", "caller": "com.android.chrome"},
    {"url": "https://en.wikipedia.org/wiki/Lofoten", "caller": "com.android.chrome"},
    {"url": "https://en.wikipedia.org/wiki/Marathon", "caller": "com.android.chrome"},
    {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "caller": "com.google.android.youtube"},
    {"url": "https://docs.python.org/3/library/asyncio.html", "caller": "com.android.chrome"},
    {"url": "https://www.raspberrypi.com/products/raspberry-pi-5/", "caller": "com.android.chrome"},
    {"url": "https://www.proxmox.com/en/", "caller": "com.android.chrome"},
    {"url": "https://x.com/SpaceX/status/1732824684683784516", "caller": "com.twitter.android"},
    {
        "text": "https://www.reddit.com/r/homeassistant/comments/1h2k3l4/my_energy_dashboard_after_one_year_of_solar/",
        "caller": "com.reddit.frontpage",
    },
    {
        "text": "https://www.reddit.com/r/selfhosted/comments/1g7x8y9/what_do_you_use_to_save_links_for_later/",
        "caller": "com.reddit.frontpage",
    },
    {
        "text": "https://www.reddit.com/r/BitcoinMining/comments/1f5e6d7/bitaxe_gamma_running_on_solar_surplus/",
        "caller": "com.reddit.frontpage",
    },
    {"text": "todo: replace the zigbee coordinator with an SLZB-06 before winter", "caller": "com.google.android.keep"},
    {
        "text": "Idea: stop the miners when the power price goes negative and charge the car instead",
        "caller": "com.google.android.keep",
    },
    {"text": "Gift idea for mom: a pottery class, ask Anne which studio", "caller": "com.google.android.keep"},
    {"text": "Look at this for the garage https://www.raspberrypi.com/products/raspberry-pi-5/", "caller": ""},
]


def wait_until_done() -> None:
    for _ in range(120):
        with urllib.request.urlopen(APP) as response:
            if json.load(response)["queue"] == 0:
                return
        time.sleep(1)


def main(data_dir: Path) -> None:
    for share in SHARES:
        request = urllib.request.Request(MOCK, json.dumps(share).encode(), {"Content-Type": "application/json"})
        urllib.request.urlopen(request).read()
        time.sleep(0.2)
    time.sleep(2)
    wait_until_done()

    random.seed(7)
    db = sqlite3.connect(data_dir / "mybrain.db")
    now = time.time()
    ids = [r[0] for r in db.execute("SELECT id FROM items ORDER BY id")]
    for n, item_id in enumerate(ids):
        # The last few are from today and this week; the rest spread back over four months
        age = random.uniform(0, 0.3) if n >= len(ids) - 4 else random.uniform(0.5, 120) ** 1.0
        created = now - age * 86400
        roll = random.random()
        status = "inbox"
        if age > 3:
            status = "done" if roll < 0.3 else "archived" if roll < 0.42 else "later" if roll < 0.58 else "inbox"
        done_at = created + random.uniform(0.2, min(age, 30)) * 86400 if status in ("done", "archived") else None
        opened = created + 3600 if status in ("done", "later") or random.random() < 0.2 else None
        priority = 2 if random.random() < 0.18 else 1
        db.execute(
            "UPDATE items SET created_at = ?, updated_at = ?, status = ?, done_at = ?, opened_at = ?, priority = ?"
            " WHERE id = ?",
            (created, done_at or created, status, done_at, opened, priority, item_id),
        )
        db.execute("UPDATE events SET at = ? WHERE item_id = ? AND what = 'saved'", (created, item_id))
        db.execute("UPDATE events SET at = ? WHERE item_id = ? AND what = 'sorted'", (created + 20, item_id))
        if opened:
            db.execute("INSERT INTO events(item_id, at, what) VALUES (?, ?, 'opened')", (item_id, opened))
        if done_at:
            db.execute("INSERT INTO events(item_id, at, what) VALUES (?, ?, ?)", (item_id, done_at, status))
    # A few open items get a date, as if set on them or found by the AI
    day = 86400
    opened = [r[0] for r in db.execute("SELECT id FROM items WHERE status IN ('inbox', 'later') ORDER BY id")]
    for item_id, (kind, offset, time_of_day) in zip(
        opened,
        [("due", -2, ""), ("happens_on", 1, "T20:00"), ("due", 4, ""), ("happens_on", 12, ""), ("due", 30, "")],
        strict=False,
    ):
        value = time.strftime("%Y-%m-%d", time.localtime(now + offset * day)) + time_of_day
        db.execute(f"UPDATE items SET {kind} = ? WHERE id = ?", (value, item_id))
    db.commit()
    print(f"{len(ids)} items")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
