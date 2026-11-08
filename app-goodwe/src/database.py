"""SQLite storage: snapshots of the inverter, daily summaries, forecast log and events.

Dates are local calendar days throughout (the inverter's own day counters reset at
local midnight). Raw snapshots are kept for a limited number of days; the daily
summaries are kept forever and carry the statistics.
"""

import json
import logging
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

SNAPSHOT_DAYS = 14
SNAPSHOT_KEYS = (
    "ppv",
    "ppv1",
    "ppv2",
    "ppv3",
    "pgrid",
    "pbattery1",
    "battery_soc",
    "load_ptotal",
    "backup_ptotal",
    "e_day",
    "vgrid",
    "fgrid",
    "vbattery1",
    "temperature",
    "battery_temperature",
    # Stamped by the battery controller: the surplus signal and the curtailment estimate
    "surplus",
    "curtailed_w",
    # Stamped by the miners module: whether the surplus loads run, and their power
    "miners_on",
    "miners_w",
)
DAY_KEYS = ("e_day", "e_day_imp", "e_day_exp", "e_load_day", "e_bat_charge_day", "e_bat_discharge_day")
# Daily statistics that only exist as derived from the snapshots: hours the surplus signal was on,
# solar energy that went to the home meanwhile (the loads switched on for it), and the estimated
# solar lost to curtailment under zero export, and the hours and energy of the miners (surplus loads)
DERIVED_KEYS = ("surplus_hours", "e_surplus_day", "e_curtailed_day", "miners_hours", "e_miners_day")
# Everything that is summed per period
TOTAL_KEYS = (*DAY_KEYS, *DERIVED_KEYS)
# A gap between snapshots longer than this is not integrated (the add-on or inverter was down)
MAX_GAP = 120


def _day_bounds(day: str | None) -> tuple[str, int, int]:
    """(date, start_ts, end_ts) of a local calendar day; today when the date is missing or invalid."""
    try:
        start = datetime.strptime(day or "", "%Y-%m-%d")
    except ValueError:
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.strftime("%Y-%m-%d"), int(start.timestamp()), int(end.timestamp())


def _period_key(day: str, bucket: str) -> str:
    """The period a date belongs to: the date, the Monday of its week, the first of its month or year."""
    if bucket == "day":
        return day
    d = date.fromisoformat(day)
    if bucket == "week":
        return (d - timedelta(days=d.weekday())).isoformat()
    if bucket == "year":
        return f"{d.year}-01-01"
    return f"{day[:7]}-01"


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.con = sqlite3.connect(str(path), check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self._inserts = 0
        self._migrate()

    def _migrate(self) -> None:
        self.con.executescript(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                ts   INTEGER PRIMARY KEY,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS daily_summary (
                date                TEXT PRIMARY KEY,
                e_day               REAL DEFAULT 0,
                e_day_exp           REAL DEFAULT 0,
                e_day_imp           REAL DEFAULT 0,
                e_load_day          REAL DEFAULT 0,
                e_bat_charge_day    REAL DEFAULT 0,
                e_bat_discharge_day REAL DEFAULT 0,
                ppv_max             REAL DEFAULT 0,
                pgrid_min           REAL DEFAULT 0,
                pgrid_max           REAL DEFAULT 0,
                soc_min             REAL,
                soc_max             REAL,
                imp_by_hour         TEXT,
                surplus_hours       REAL DEFAULT 0,
                e_surplus_day       REAL DEFAULT 0,
                e_curtailed_day     REAL DEFAULT 0,
                miners_hours        REAL DEFAULT 0,
                e_miners_day        REAL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS forecast_log (
                date         TEXT PRIMARY KEY,
                forecast_kwh REAL,
                actual_kwh   REAL
            );
            CREATE TABLE IF NOT EXISTS events (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      INTEGER NOT NULL,
                type    TEXT NOT NULL,
                level   TEXT NOT NULL,
                message TEXT NOT NULL,
                data    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
            """
        )
        columns = {row[1] for row in self.con.execute("PRAGMA table_info(daily_summary)")}
        for column in DERIVED_KEYS:
            if column not in columns:
                self.con.execute(f"ALTER TABLE daily_summary ADD COLUMN {column} REAL DEFAULT 0")
        self.con.commit()

    def close(self) -> None:
        self.con.close()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    def insert_snapshot(self, data: dict) -> None:
        ts = int(time.time())
        slim = {k: data[k] for k in SNAPSHOT_KEYS if k in data}
        self.con.execute("INSERT OR REPLACE INTO snapshots (ts, data) VALUES (?, ?)", (ts, json.dumps(slim)))
        self._upsert_daily(data)
        self.con.commit()
        self._inserts += 1
        if self._inserts % 200 == 1:
            self.prune()

    def _upsert_daily(self, data: dict) -> None:
        day = datetime.now().strftime("%Y-%m-%d")
        soc = data.get("battery_soc")
        self.con.execute(
            """
            INSERT INTO daily_summary(date, e_day, e_day_exp, e_day_imp, e_load_day, e_bat_charge_day,
                e_bat_discharge_day, ppv_max, pgrid_min, pgrid_max, soc_min, soc_max)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                e_day               = MAX(e_day, excluded.e_day),
                e_day_exp           = MAX(e_day_exp, excluded.e_day_exp),
                e_day_imp           = MAX(e_day_imp, excluded.e_day_imp),
                e_load_day          = MAX(e_load_day, excluded.e_load_day),
                e_bat_charge_day    = MAX(e_bat_charge_day, excluded.e_bat_charge_day),
                e_bat_discharge_day = MAX(e_bat_discharge_day, excluded.e_bat_discharge_day),
                ppv_max             = MAX(ppv_max, excluded.ppv_max),
                pgrid_min           = MIN(pgrid_min, excluded.pgrid_min),
                pgrid_max           = MAX(pgrid_max, excluded.pgrid_max),
                soc_min             = MIN(COALESCE(soc_min, excluded.soc_min), excluded.soc_min),
                soc_max             = MAX(COALESCE(soc_max, excluded.soc_max), excluded.soc_max)
            """,
            (
                day,
                *(float(data.get(k) or 0) for k in DAY_KEYS),
                float(data.get("ppv") or 0),
                float(data.get("pgrid") or 0),
                float(data.get("pgrid") or 0),
                soc,
                soc,
            ),
        )

    def prune(self) -> None:
        cutoff = int(time.time()) - SNAPSHOT_DAYS * 86400
        self.con.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,))
        self.con.execute("DELETE FROM events WHERE ts < ?", (int(time.time()) - 365 * 86400,))
        self.con.commit()

    # ------------------------------------------------------------------
    # Energy flows from the snapshots (the ES has no import/export counters)
    # ------------------------------------------------------------------
    def _integrate(self, start: int, end: int) -> tuple[dict, list[float], int, dict]:
        """Integrate the snapshots in [start, end) into energy flows (Wh) with a priority model per instant:
        solar → house, then battery, then grid; battery discharge → house; grid import → house, then battery.
        Battery power comes from the energy balance (solar + grid - house), not the inverter's battery
        reading, so it is right regardless of platform sign quirks. Also returns grid import per local hour
        and the surplus statistics (seconds the signal was on, solar to the house meanwhile, curtailed Wh).
        """
        rows = self.con.execute(
            "SELECT ts, data FROM snapshots WHERE ts >= ? AND ts < ? ORDER BY ts", (start, end)
        ).fetchall()
        links = {
            "solar_load": 0.0,
            "solar_batt": 0.0,
            "solar_grid": 0.0,
            "batt_load": 0.0,
            "grid_load": 0.0,
            "grid_batt": 0.0,
        }
        imp_by_hour = [0.0] * 24
        extra = {"surplus_s": 0.0, "surplus_solar_wh": 0.0, "curtailed_wh": 0.0, "miners_s": 0.0, "miners_wh": 0.0}
        prev = None
        for row in rows:
            ts = row["ts"]
            if prev is not None and 0 < ts - prev <= MAX_GAP:
                d = json.loads(row["data"])
                h = (ts - prev) / 3600.0
                solar = max(float(d.get("ppv") or 0), 0)
                pg = float(d.get("pgrid") or 0)
                gi, ge = max(pg, 0), max(-pg, 0)
                load = max(float(d.get("load_ptotal") or 0), 0)
                batt_net = solar + pg - load  # > 0 charges, < 0 discharges
                bc, bd = max(batt_net, 0), max(-batt_net, 0)
                remaining = load
                s_load = min(solar, remaining)
                s = solar - s_load
                remaining -= s_load
                b_load = min(bd, remaining)
                remaining -= b_load
                g_load = min(gi, remaining)
                gi2 = gi - g_load
                s_batt = min(s, bc)
                s -= s_batt
                s_grid = min(s, ge)
                g_batt = min(gi2, bc - s_batt)
                links["solar_load"] += s_load * h
                links["solar_batt"] += s_batt * h
                links["solar_grid"] += s_grid * h
                links["batt_load"] += b_load * h
                links["grid_load"] += g_load * h
                links["grid_batt"] += g_batt * h
                imp_by_hour[datetime.fromtimestamp(ts).hour] += gi * h
                if d.get("surplus"):
                    extra["surplus_s"] += ts - prev
                    extra["surplus_solar_wh"] += s_load * h
                extra["curtailed_wh"] += max(float(d.get("curtailed_w") or 0), 0) * h
                if d.get("miners_on"):
                    extra["miners_s"] += ts - prev
                    extra["miners_wh"] += max(float(d.get("miners_w") or 0), 0) * h
            prev = ts
        return links, imp_by_hour, len(rows), extra

    def energy_flow(self, day: str | None = None) -> dict:
        """Sankey-style energy flows (kWh) for one local day."""
        day, start, end = _day_bounds(day)
        links, _, samples, _ = self._integrate(start, end)
        links = {k: round(v / 1000, 3) for k, v in links.items()}
        sources = {
            "solar": round(links["solar_load"] + links["solar_batt"] + links["solar_grid"], 3),
            "battery": round(links["batt_load"], 3),
            "grid": round(links["grid_load"] + links["grid_batt"], 3),
        }
        destinations = {
            "load": round(links["solar_load"] + links["batt_load"] + links["grid_load"], 3),
            "battery": round(links["solar_batt"] + links["grid_batt"], 3),
            "grid": round(links["solar_grid"], 3),
        }
        return {"date": day, "links": links, "sources": sources, "destinations": destinations, "samples": samples}

    def update_derived(self, day: str | None = None) -> None:
        """Fill a day's import, export, house and battery energies from its snapshots (MAX-merged with
        whatever the inverter reported itself, so a platform that has the counters keeps them)."""
        day, start, end = _day_bounds(day)
        links, imp_by_hour, samples, extra = self._integrate(start, end)
        if samples < 2:
            return

        def kwh(*keys):
            return round(sum(links[k] for k in keys) / 1000, 3)

        self.con.execute("INSERT INTO daily_summary(date) VALUES (?) ON CONFLICT(date) DO NOTHING", (day,))
        self.con.execute(
            """
            UPDATE daily_summary SET
                e_day_imp           = MAX(e_day_imp, ?),
                e_day_exp           = MAX(e_day_exp, ?),
                e_load_day          = MAX(e_load_day, ?),
                e_bat_charge_day    = MAX(e_bat_charge_day, ?),
                e_bat_discharge_day = MAX(e_bat_discharge_day, ?),
                imp_by_hour         = ?,
                surplus_hours       = MAX(COALESCE(surplus_hours, 0), ?),
                e_surplus_day       = MAX(COALESCE(e_surplus_day, 0), ?),
                e_curtailed_day     = MAX(COALESCE(e_curtailed_day, 0), ?),
                miners_hours        = MAX(COALESCE(miners_hours, 0), ?),
                e_miners_day        = MAX(COALESCE(e_miners_day, 0), ?)
            WHERE date = ?
            """,
            (
                kwh("grid_load", "grid_batt"),
                kwh("solar_grid"),
                kwh("solar_load", "batt_load", "grid_load"),
                kwh("solar_batt", "grid_batt"),
                kwh("batt_load"),
                json.dumps([round(v / 1000, 4) for v in imp_by_hour]),
                round(extra["surplus_s"] / 3600, 2),
                round(extra["surplus_solar_wh"] / 1000, 3),
                round(extra["curtailed_wh"] / 1000, 3),
                round(extra["miners_s"] / 3600, 2),
                round(extra["miners_wh"] / 1000, 3),
                day,
            ),
        )
        self.con.commit()

    def snapshot_dates(self) -> list[str]:
        """The local dates that still have snapshots."""
        row = self.con.execute("SELECT MIN(ts), MAX(ts) FROM snapshots").fetchone()
        if not row or row[0] is None:
            return []
        d, last = datetime.fromtimestamp(row[0]).date(), datetime.fromtimestamp(row[1]).date()
        out = []
        while d <= last:
            out.append(d.isoformat())
            d += timedelta(days=1)
        return out

    def energy_totals(self) -> dict:
        """Today's derived day energies and surplus statistics, plus lifetime import, export and battery
        sums since recording began (kWh), for the live data. The ES counts none of these itself."""
        today = self.day_row(datetime.now().strftime("%Y-%m-%d")) or {}
        total = self.con.execute(
            "SELECT SUM(e_day_imp), SUM(e_day_exp), SUM(e_bat_charge_day), SUM(e_bat_discharge_day) FROM daily_summary"
        ).fetchone()
        out = {k: float(today.get(k) or 0) for k in (*DAY_KEYS[1:], *DERIVED_KEYS)}
        for i, key in enumerate(("e_total_imp", "e_total_exp", "e_bat_charge_total", "e_bat_discharge_total")):
            out[key] = round(float(total[i] or 0), 2)
        return out

    # ------------------------------------------------------------------
    # Series for the charts
    # ------------------------------------------------------------------
    def day_series(self, day: str | None = None, bucket_min: int = 5) -> dict:
        """Power over one day in buckets, with the battery's state per bucket (charge / discharge / hold / idle).
        Battery power is the energy balance, independent of the inverter's sign convention."""
        day, start, end = _day_bounds(day)
        bucket = max(1, bucket_min) * 60
        rows = self.con.execute(
            "SELECT ts, data FROM snapshots WHERE ts >= ? AND ts < ? ORDER BY ts", (start, end)
        ).fetchall()
        agg: dict[int, dict] = {}
        for row in rows:
            d = json.loads(row["data"])
            b = (row["ts"] - start) // bucket
            a = agg.setdefault(
                b, {"ppv": 0.0, "pgrid": 0.0, "load": 0.0, "soc": 0.0, "miners": 0.0, "n": 0, "soc_n": 0}
            )
            a["ppv"] += float(d.get("ppv") or 0)
            a["pgrid"] += float(d.get("pgrid") or 0)
            a["load"] += float(d.get("load_ptotal") or 0)
            a["miners"] += float(d.get("miners_w") or 0)
            if d.get("battery_soc") is not None:
                a["soc"] += float(d["battery_soc"])
                a["soc_n"] += 1
            a["n"] += 1
        series = []
        for b in sorted(agg):
            a = agg[b]
            n = a["n"] or 1
            solar, pg, load = a["ppv"] / n, a["pgrid"] / n, a["load"] / n
            bnet = solar + pg - load
            if bnet > 100:
                state = "charge"
            elif bnet < -100:
                state = "discharge"
            elif pg > 100:
                state = "hold"  # battery idle while the grid covers the house
            else:
                state = "idle"
            series.append(
                {
                    "ts": start + b * bucket,
                    "solar": round(solar),
                    "load": round(load),
                    "grid": round(pg),
                    "battery": round(bnet),
                    "soc": round(a["soc"] / a["soc_n"]) if a["soc_n"] else None,
                    "state": state,
                    "miners": round(a["miners"] / n),
                }
            )
        return {"date": day, "bucket_min": bucket_min, "series": series}

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------
    def day_row(self, day: str) -> dict | None:
        row = self.con.execute("SELECT * FROM daily_summary WHERE date = ?", (day,)).fetchone()
        return dict(row) if row else None

    def daily_rows(self, start: str, end: str) -> list[dict]:
        """daily_summary rows with start <= date < end, oldest first."""
        rows = self.con.execute(
            "SELECT * FROM daily_summary WHERE date >= ? AND date < ? ORDER BY date", (start, end)
        ).fetchall()
        return [dict(r) for r in rows]

    def periods(self, bucket: str, start: str, end: str) -> list[dict]:
        """Energies per day, week, month or year between two dates."""
        buckets: dict[str, dict] = {}
        for row in self.daily_rows(start, end):
            key = _period_key(row["date"], bucket)
            b = buckets.setdefault(key, {"period": key, "days": 0, "ppv_max": 0.0, **{k: 0.0 for k in TOTAL_KEYS}})
            b["days"] += 1
            for k in TOTAL_KEYS:
                b[k] += float(row.get(k) or 0)
            b["ppv_max"] = max(b["ppv_max"], float(row.get("ppv_max") or 0))
        out = []
        for key in sorted(buckets):
            b = buckets[key]
            for k in TOTAL_KEYS:
                b[k] = round(b[k], 2)
            b["self_sufficiency"] = _self_sufficiency(b)
            b["self_consumption"] = _self_consumption(b)
            out.append(b)
        return out

    def summary(self, start: str, end: str) -> dict:
        """Totals and records over a date range."""
        rows = self.daily_rows(start, end)
        totals = {k: round(sum(float(r.get(k) or 0) for r in rows), 2) for k in TOTAL_KEYS}
        totals["days"] = len(rows)
        totals["self_sufficiency"] = _self_sufficiency(totals)
        totals["self_consumption"] = _self_consumption(totals)
        with_solar = [r for r in rows if (r.get("e_day") or 0) > 0]
        totals["average_kwh"] = round(sum(r["e_day"] for r in with_solar) / len(with_solar), 2) if with_solar else None

        def best(key, reverse=True):
            candidates = [r for r in rows if r.get(key)]
            if not candidates:
                return None
            r = max(candidates, key=lambda r: r[key]) if reverse else min(candidates, key=lambda r: r[key])
            return {"date": r["date"], "value": round(float(r[key]), 2)}

        totals["records"] = {
            "best_solar_day": best("e_day"),
            "peak_pv": best("ppv_max"),
            "most_import": best("e_day_imp"),
            "most_export": best("e_day_exp"),
            "most_load": best("e_load_day"),
            "most_charged": best("e_bat_charge_day"),
            "longest_surplus": best("surplus_hours"),
            "longest_miners": best("miners_hours"),
            "most_curtailed": best("e_curtailed_day"),
        }
        return totals

    def data_span(self) -> dict:
        row = self.con.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM daily_summary").fetchone()
        return {"first_date": row[0], "last_date": row[1], "days": row[2]}

    def peak_pv(self) -> float:
        row = self.con.execute("SELECT MAX(ppv_max) FROM daily_summary").fetchone()
        return float(row[0] or 0)

    def month_totals(self, year: int) -> list[dict]:
        rows = self.con.execute(
            "SELECT substr(date, 1, 7) AS month, SUM(e_day) AS e_day, SUM(e_load_day) AS e_load_day,"
            " SUM(e_day_exp) AS e_day_exp, SUM(e_day_imp) AS e_day_imp FROM daily_summary"
            " WHERE date >= ? AND date < ? GROUP BY month ORDER BY month",
            (f"{year}-01-01", f"{year + 1}-01-01"),
        ).fetchall()
        return [dict(r) for r in rows]

    def year_totals(self) -> list[dict]:
        rows = self.con.execute(
            "SELECT substr(date, 1, 4) AS year, SUM(e_day) AS e_day, SUM(e_load_day) AS e_load_day,"
            " SUM(e_day_exp) AS e_day_exp, SUM(e_day_imp) AS e_day_imp FROM daily_summary GROUP BY year ORDER BY year"
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Forecast log
    # ------------------------------------------------------------------
    def record_forecast(self, day: str, forecast_kwh: float) -> None:
        """The day's forecast, once (the first capture of the day wins: that is the real prediction)."""
        self.con.execute(
            "INSERT INTO forecast_log(date, forecast_kwh) VALUES (?, ?) ON CONFLICT(date) DO NOTHING",
            (day, round(float(forecast_kwh), 2)),
        )
        self.con.commit()

    def record_actual(self, day: str, actual_kwh: float) -> None:
        self.con.execute(
            "INSERT INTO forecast_log(date, actual_kwh) VALUES (?, ?)"
            " ON CONFLICT(date) DO UPDATE SET actual_kwh = MAX(COALESCE(actual_kwh, 0), excluded.actual_kwh)",
            (day, round(float(actual_kwh), 2)),
        )
        self.con.commit()

    def forecast_accuracy(self, days: int = 14) -> dict:
        rows = self.con.execute(
            "SELECT date, forecast_kwh, actual_kwh FROM forecast_log WHERE forecast_kwh IS NOT NULL"
            " ORDER BY date DESC LIMIT ?",
            (days,),
        ).fetchall()
        out = []
        for r in rows:
            f, a = r["forecast_kwh"], r["actual_kwh"]
            error = round((a - f) / f * 100, 1) if f and a is not None else None
            out.append({"date": r["date"], "forecast_kwh": f, "actual_kwh": a, "error_pct": error})
        errors = [o["error_pct"] for o in out if o["error_pct"] is not None and o["date"] != date.today().isoformat()]
        bias = round(sum(errors) / len(errors), 1) if errors else None
        return {"days": list(reversed(out)), "bias_pct": bias}

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def insert_event(self, event: dict) -> None:
        self.con.execute(
            "INSERT INTO events (ts, type, level, message, data) VALUES (?, ?, ?, ?, ?)",
            (
                event["ts"],
                event["type"],
                event["level"],
                event["message"],
                json.dumps(event.get("data") or {}, default=str),
            ),
        )
        self.con.commit()

    def events(self, limit: int = 100, kinds: tuple[str, ...] | None = None) -> list[dict]:
        if kinds:
            marks = ",".join("?" * len(kinds))
            rows = self.con.execute(
                f"SELECT * FROM events WHERE type IN ({marks}) ORDER BY ts DESC, id DESC LIMIT ?", (*kinds, limit)
            ).fetchall()
        else:
            rows = self.con.execute("SELECT * FROM events ORDER BY ts DESC, id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            try:
                item["data"] = json.loads(item["data"] or "{}")
            except ValueError:
                item["data"] = {}
            out.append(item)
        return out

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------
    def counts(self) -> dict:
        row = self.con.execute(
            "SELECT (SELECT COUNT(*) FROM snapshots), (SELECT COUNT(*) FROM daily_summary), (SELECT COUNT(*) FROM events)"
        ).fetchone()
        return {"snapshots": row[0], "days": row[1], "events": row[2]}

    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size + (
                self.path.with_name(self.path.name + "-wal").stat().st_size
                if self.path.with_name(self.path.name + "-wal").exists()
                else 0
            )
        except OSError:
            return 0

    def backup_to(self, path: Path) -> None:
        target = sqlite3.connect(str(path))
        try:
            self.con.backup(target)
        finally:
            target.close()


def _self_sufficiency(b: dict) -> float | None:
    """Share of the house's energy that did not come from the grid."""
    load = float(b.get("e_load_day") or 0)
    if load <= 0:
        return None
    return round(max(0.0, min(100.0, (1 - float(b.get("e_day_imp") or 0) / load) * 100)), 1)


def _self_consumption(b: dict) -> float | None:
    """Share of the solar energy that was not exported."""
    solar = float(b.get("e_day") or 0)
    if solar <= 0:
        return None
    return round(max(0.0, min(100.0, (1 - float(b.get("e_day_exp") or 0) / solar) * 100)), 1)
