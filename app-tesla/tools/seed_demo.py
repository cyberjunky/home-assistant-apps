#!/usr/bin/env python3
"""Fill a database with invented history, for developing the dashboard.

    TESLA_DATA_DIR=/tmp/tesla-demo python3 tools/seed_demo.py [months]

Creates one car with about a year of commutes, weekend trips, home charging and
the odd fast charge. Refuses to touch a database that already has cars.
"""

import itertools
import math
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import Database

EFFICIENCY = 152.0
FULL_RANGE_NEW = 507.0

HOME = (52.0844, 5.1109)
# name, place label, waypoints from home (straight lines between them are good enough for a demo)
ROUTES = {
    "work": (
        "Stationsplein 9, Amersfoort",
        [
            (52.0935, 5.1390),
            (52.0990, 5.1720),
            (52.1080, 5.2100),
            (52.1190, 5.2600),
            (52.1330, 5.3100),
            (52.1470, 5.3560),
            (52.1561, 5.3878),
        ],
    ),
    "shop": ("Winkelcentrum Kanaleneiland, Utrecht", [(52.0800, 5.1050), (52.0745, 5.0990), (52.0712, 5.0962)]),
    "amsterdam": (
        "Museumplein, Amsterdam",
        [
            (52.0990, 5.0700),
            (52.1500, 5.0200),
            (52.2300, 4.9700),
            (52.3000, 4.9300),
            (52.3400, 4.8900),
            (52.3579, 4.8813),
        ],
    ),
    "arnhem": (
        "Velperplein, Arnhem",
        [
            (52.0700, 5.1800),
            (52.0500, 5.3500),
            (52.0300, 5.5500),
            (52.0000, 5.7500),
            (51.9870, 5.8800),
            (51.9843, 5.9120),
        ],
    ),
    "zwolle": (
        "Grote Markt, Zwolle",
        [
            (52.0990, 5.1720),
            (52.1561, 5.3878),
            (52.2500, 5.5800),
            (52.3600, 5.7800),
            (52.4500, 5.9800),
            (52.5126, 6.0936),
        ],
    ),
    "maastricht": (
        "Vrijthof, Maastricht",
        [
            (52.0300, 5.1600),
            (51.8500, 5.2800),
            (51.6900, 5.3000),
            (51.4400, 5.4700),
            (51.2000, 5.7000),
            (50.9800, 5.7600),
            (50.8492, 5.6889),
        ],
    ),
}
SUPERCHARGER = ((51.4480, 5.4500), "Tesla Supercharger, Eindhoven")


def haversine_km(a, b) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def outside_temp(ts: float) -> float:
    """Dutch weather, more or less: 3 °C in January, 18 °C in July, colder at night."""
    year_part = (time.localtime(ts).tm_yday - 15) / 365 * 2 * math.pi
    hour = time.localtime(ts).tm_hour
    return 10.5 - 7.5 * math.cos(year_part) + 3 * math.sin((hour - 9) / 24 * 2 * math.pi) + random.uniform(-3, 3)


class Seeder:
    def __init__(self, db: Database, months: int):
        self.db = db
        self.car_id = db.upsert_car(1492931365719407, 1234567890, "5YJRE7EB0PF000001", "Mockingbird")
        db.update_car(self.car_id, model="Roadster", trim="", color="RedMulticoat", wheels="")
        db.apply_efficiency(self.car_id, EFFICIENCY)
        self.start = time.time() - months * 30.4 * 86400
        self.months = months
        self.odometer = 31250.0
        self.range_km = 0.8 * FULL_RANGE_NEW
        self.place = HOME

    def full_range(self, ts: float) -> float:
        """The battery loses about 3% over the seeded period."""
        age = (ts - self.start) / (self.months * 30.4 * 86400)
        return FULL_RANGE_NEW * (1 - 0.03 * age)

    def soc(self, ts: float) -> int:
        return max(1, min(100, round(self.range_km / self.full_range(ts) * 100)))

    def drive(self, ts: float, waypoints: list, place_from: str, place_to: str) -> float:
        """Record a drive along the waypoints starting at ts. Returns when it ends."""
        path = [self.place, *waypoints]
        temp = outside_temp(ts)
        # Cold costs range: about 30% more around freezing than at 20 °C
        factor = 1.0 + max(0.0, 18 - temp) * 0.017 + random.uniform(-0.05, 0.08)
        highway = sum(haversine_km(a, b) for a, b in itertools.pairwise(path)) > 15
        cruise = random.uniform(98, 122) if highway else random.uniform(32, 48)

        sample = {
            "ts": int(ts),
            "lat": path[0][0],
            "lon": path[0][1],
            "odometer_km": self.odometer,
            "soc": self.soc(ts),
            "range_km": self.range_km,
        }
        drive_id = self.db.open_drive(self.car_id, sample)
        now = ts
        for a, b in itertools.pairwise(path):
            leg = haversine_km(a, b) * 1.18  # roads aren't straight
            steps = max(2, int(leg / 0.6))
            heading = int(math.degrees(math.atan2((b[1] - a[1]) * math.cos(math.radians(a[0])), b[0] - a[0])) % 360)
            for step in range(1, steps + 1):
                t = step / steps
                speed = max(8.0, cruise * min(1.0, 0.35 + 3 * min(t, 1 - t) + 0.4) + random.uniform(-9, 9))
                km = leg / steps
                now += km / speed * 3600
                self.odometer += km
                self.range_km -= km * factor
                self.db.add_position(
                    self.car_id,
                    {
                        "ts": int(now),
                        "lat": a[0] + (b[0] - a[0]) * t + random.uniform(-0.0004, 0.0004),
                        "lon": a[1] + (b[1] - a[1]) * t + random.uniform(-0.0004, 0.0004),
                        "heading": heading,
                        "elevation_m": 6
                        + 5 * math.sin(self.odometer / 3)
                        + (40 * t if place_to.endswith("Maastricht") else 0),
                        "speed_kmh": speed,
                        "power_kw": speed / 5.5 * factor + random.uniform(-6, 9),
                        "odometer_km": self.odometer,
                        "soc": self.soc(now),
                        "range_km": self.range_km,
                        "est_range_km": self.range_km * 0.92,
                        "outside_temp": temp + random.uniform(-0.4, 0.4),
                        "inside_temp": 20.5 + random.uniform(-0.5, 0.5),
                    },
                    drive_id,
                )
        self.db.close_drive(drive_id, EFFICIENCY)
        self.db.execute(
            "UPDATE drives SET start_place = ?, end_place = ? WHERE id = ?", (place_from, place_to, drive_id)
        )
        self.db.set_state(self.car_id, "driving", int(ts))
        self.db.set_state(self.car_id, "online", int(now))
        self.db.set_state(self.car_id, "asleep", int(now) + 1500)
        self.place = path[-1]
        return now

    def charge(self, ts: float, place: str, target_soc: int, dc: bool) -> float:
        """Record a charging session up to target_soc. Returns when it ends."""
        sample = {
            "ts": int(ts),
            "lat": self.place[0],
            "lon": self.place[1],
            "odometer_km": self.odometer,
            "soc": self.soc(ts),
            "range_km": self.range_km,
        }
        charge_id = self.db.open_charge(self.car_id, sample, "DC" if dc else "AC", "Tesla" if dc else None)
        temp = outside_temp(ts)
        added = 0.0
        now = ts
        step = 60 if dc else 300
        while self.soc(now) < target_soc:
            soc = self.soc(now)
            # A fast charger tapers off as the battery fills; AC is flat
            power = max(28.0, 170.0 * (1 - soc / 105)) if dc else 11.04
            loss = 0.97 if dc else 0.90
            added += power * loss * step / 3600
            self.range_km += power * loss * step / 3600 / (EFFICIENCY / 1000)
            now += step
            self.db.add_charge_sample(
                charge_id,
                {
                    "ts": int(now),
                    "soc": self.soc(now),
                    "power_kw": power,
                    "voltage": 398 if dc else 231,
                    "current": power * 1000 / 398 if dc else 16,
                    "phases": None if dc else 3,
                    "energy_added_kwh": added,
                    "range_km": self.range_km,
                    "outside_temp": temp,
                },
            )
        self.db.close_charge(charge_id, 0.52 if dc else 0.24)
        self.db.execute("UPDATE charges SET place = ? WHERE id = ?", (place, charge_id))
        self.db.set_state(self.car_id, "charging", int(ts))
        self.db.set_state(self.car_id, "online", int(now))
        self.db.set_state(self.car_id, "asleep", int(now) + 1200)
        return now

    def scatter_parked(self) -> None:
        """Stationary points where the car spends time, so the heatmap shows real hotspots
        (home, work, the places it visits and the chargers) instead of only the driving lines."""
        hotspots = [
            (HOME, 900, "home"),
            (ROUTES["work"][1][-1], 500, "work"),
            (ROUTES["shop"][1][-1], 150, "visit"),
            (ROUTES["amsterdam"][1][-1], 110, "visit"),
            (ROUTES["arnhem"][1][-1], 80, "visit"),
            (ROUTES["zwolle"][1][-1], 70, "visit"),
            (ROUTES["maastricht"][1][-1], 60, "visit"),
            # Superchargers and fast-charge stops - the hot spots the map should light up
            (SUPERCHARGER[0], 220, "supercharger"),
            ((52.1561, 5.3878), 180, "supercharger"),  # Amersfoort
            ((52.0411, 5.1360), 150, "supercharger"),  # Nieuwegein
            ((52.3579, 4.8900), 130, "supercharger"),  # Amsterdam Zuid
            ((52.2210, 5.1719), 110, "supercharger"),  # Hilversum
        ]
        ts = int(self.start)
        for (lat, lon), count, _kind in hotspots:
            for _ in range(count):
                ts += 90
                self.db.add_position(
                    self.car_id,
                    {
                        "ts": ts,
                        "lat": lat + random.gauss(0, 0.0011),
                        "lon": lon + random.gauss(0, 0.0017),
                        "heading": 0,
                        "elevation_m": 6,
                        "speed_kmh": 0.0,
                        "power_kw": 0.0,
                        "odometer_km": self.odometer,
                        "soc": 70,
                        "range_km": self.range_km,
                        "est_range_km": self.range_km * 0.92,
                        "outside_temp": 11.0,
                        "inside_temp": 18.0,
                    },
                )

    def run(self) -> None:
        day = self.start - self.start % 86400
        today = time.time() - 3600
        versions = [
            "2025.38.9",
            "2025.44.25",
            "2026.2.6",
            "2026.8.3",
            "2026.14.9",
            "2026.20.7",
            "2026.26.5",
            "2026.32.3",
            "2026.38.4",
        ]
        self.db.set_state(self.car_id, "asleep", int(day))
        while day < today:
            local = time.localtime(day + 43200)
            weekday = local.tm_wday
            version = versions[
                min(len(versions) - 1, int((day - self.start) / (self.months * 30.4 * 86400) * len(versions)))
            ]
            self.db.record_version(self.car_id, version, int(day + 3 * 3600))
            # Standby losses: about a kilometre of range a day
            self.range_km -= random.uniform(0.5, 1.6)

            if weekday < 5 and random.random() < 0.82:
                label, waypoints = ROUTES["work"]
                out = day + 7.3 * 3600 + random.uniform(-1200, 1800)
                arrived = self.drive(out, waypoints, "Home", label)
                back = max(arrived + 7 * 3600, day + 16.8 * 3600 + random.uniform(-1800, 3600))
                if back < today:
                    self.drive(back, [*reversed(waypoints[:-1]), HOME], label, "Home")
            elif weekday >= 5 and random.random() < 0.55:
                name = random.choices(["amsterdam", "arnhem", "zwolle", "maastricht", "shop"], [3, 2, 2, 1, 3])[0]
                label, waypoints = ROUTES[name]
                out = day + 10 * 3600 + random.uniform(0, 7200)
                arrived = self.drive(out, waypoints, "Home", label)
                if name == "maastricht" and arrived < today:
                    # Too far for one battery in winter: fast charge on the way back
                    back = arrived + 4 * 3600
                    mid = self.drive(back, [*reversed(waypoints[3:-1]), SUPERCHARGER[0]], label, SUPERCHARGER[1])
                    done = self.charge(mid + 180, SUPERCHARGER[1], 78, dc=True)
                    self.drive(done + 240, [*reversed(waypoints[:3]), HOME], SUPERCHARGER[1], "Home")
                elif arrived + 3 * 3600 < today:
                    self.drive(arrived + random.uniform(2, 5) * 3600, [*reversed(waypoints[:-1]), HOME], label, "Home")
            if random.random() < 0.25 and day + 19 * 3600 < today:
                label, waypoints = ROUTES["shop"]
                arrived = self.drive(day + 19 * 3600 + random.uniform(0, 1800), waypoints, "Home", label)
                self.drive(arrived + 1500, [*reversed(waypoints[:-1]), HOME], label, "Home")

            # Plug in at home when the battery is getting low
            if self.soc(day + 86000) < 55 and self.place == HOME and day + 86400 < today:
                self.charge(
                    day + 22.5 * 3600 + random.uniform(0, 3600), "Home", random.choice([80, 80, 80, 90]), dc=False
                )
            day += 86400

        self.scatter_parked()

        # What the dashboard shows for the car right now
        self.db.save_snapshot(
            self.car_id,
            {
                "ts": int(today),
                "name": "Mockingbird",
                "model": "Model 3",
                "trim": "74d",
                "color": "MidnightSilver",
                "lat": self.place[0],
                "lon": self.place[1],
                "heading": 40,
                "odometer_km": self.odometer,
                "soc": self.soc(today),
                "usable_soc": self.soc(today) - 1,
                "range_km": self.range_km,
                "est_range_km": self.range_km * 0.92,
                "charge_limit_soc": 80,
                "charging_state": "Disconnected",
                "plugged_in": False,
                "inside_temp": 16.5,
                "outside_temp": 11.0,
                "climate_on": False,
                "locked": True,
                "sentry": False,
                "doors_open": False,
                "windows_open": False,
                "trunk_open": False,
                "frunk_open": False,
                "tpms": {"fl": 2.9, "fr": 2.9, "rl": 2.85, "rr": 2.875},
                "version": "2026.38.4",
                "speed_kmh": 0.0,
            },
        )


def main() -> None:
    months = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    random.seed(7)
    os.environ.setdefault("TZ", "Europe/Amsterdam")
    db = Database()
    if db.list_cars():
        sys.exit(f"{db.path} already has cars - not adding demo data to it")
    db.execute("BEGIN")
    Seeder(db, months).run()
    db.execute("COMMIT")
    counts = db.query_one(
        "SELECT (SELECT COUNT(*) FROM drives) AS drives, (SELECT COUNT(*) FROM charges) AS charges, (SELECT COUNT(*) FROM positions) AS positions"
    )
    print(f"Seeded {db.path}: {counts}")


if __name__ == "__main__":
    main()
