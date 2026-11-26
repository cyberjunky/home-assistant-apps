"""A made-up Garmin account with the same methods and answer shapes as python-garminconnect's Garmin.

For development and the tests only: run the app with GARMIN_FAKE=1 and sign in with any email
address and password. An address containing "mfa" asks for a code; 123456 is the right one.
Everything is generated from the date, so the same day always looks the same.
"""

import json
import math
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

CREATED = date(2023, 3, 14)
HOME = (52.0907, 5.1214)
TOKEN_FILE = "garmin_tokens.json"


class _Client:
    def __init__(self, owner):
        self.owner = owner

    def dump(self, path: str) -> None:
        target = Path(path)
        target.mkdir(parents=True, exist_ok=True)
        (target / TOKEN_FILE).write_text(
            json.dumps({"di_token": "fake", "di_refresh_token": "fake", "di_client_id": "fake"})
        )


def _rng(day: date, salt: str = "") -> random.Random:
    return random.Random(f"{day.isoformat()}{salt}")


def _days(start: str, end: str):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    for i in range((last - first).days + 1):
        yield first + timedelta(days=i)


def _ms(moment: datetime) -> int:
    return int(moment.replace(tzinfo=UTC).timestamp() * 1000)


def _gmt(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.0")


class FakeGarmin:
    def __init__(self, email=None, password=None, is_cn=False, prompt_mfa=None, return_on_mfa=False, **_):
        self.email = email
        self.password = password
        self.return_on_mfa = return_on_mfa
        self.client = _Client(self)
        self.display_name = "fake"
        self.full_name = "Alex Example"
        self.profile_id = 1
        self.requests = 0

    # Sign-in -------------------------------------------------------------
    def login(self, tokenstore=None):
        if tokenstore:
            if not (Path(tokenstore) / TOKEN_FILE).exists():
                raise FileNotFoundError(tokenstore)
            return None, None
        if self.password == "wrong":
            from garminconnect import GarminConnectAuthenticationError

            raise GarminConnectAuthenticationError("401")
        if self.email and "mfa" in self.email:
            return "needs_mfa", {"state": "fake"}
        return None, None

    def resume_login(self, client_state, mfa_code):
        if mfa_code != "123456":
            from garminconnect import GarminConnectAuthenticationError

            raise GarminConnectAuthenticationError("MFA code rejected")
        return None, None

    def get_full_name(self):
        return self.full_name

    def get_unit_system(self):
        return "metric"

    def get_user_timezone(self):
        return "Europe/Amsterdam"

    def get_user_profile_base(self):
        return {"createDate": f"{CREATED.isoformat()}T10:00:00.000+0000", "birthDate": "1980-06-01"}

    # The made-up person --------------------------------------------------
    def _day(self, day: date) -> dict:
        r = _rng(day)
        trend = math.sin((day - CREATED).days / 90)
        weekend = day.weekday() >= 5
        sick = r.random() < 0.02
        workout = r.random() < (0.55 if weekend else 0.4)
        steps = int(max(800, r.gauss(11000 if weekend else 8500, 3000) + trend * 1500 + (4000 if workout else 0)))
        rhr = round(52 - (day - CREATED).days / 400 + (6 if sick else 0) + r.gauss(0, 1.2))
        stress = int(max(10, min(70, r.gauss(28 if weekend else 34, 7) + (12 if sick else 0))))
        sleep_s = int(max(16000, r.gauss(27500 if weekend else 25000, 2600) - (4000 if sick else 0)))
        return {
            "r": r,
            "steps": steps,
            "rhr": rhr,
            "stress": stress,
            "sleep_s": sleep_s,
            "workout": workout,
            "sick": sick,
        }

    def get_daily_summaries(self, start, end):
        data = []
        for day in _days(start, end):
            if day < CREATED or day > date.today():
                continue
            p = self._day(day)
            r = p["r"]
            active = int(p["steps"] * 0.045 + (450 if p["workout"] else 0))
            midnight = datetime.combine(day, datetime.min.time()) - timedelta(hours=1)
            data.append(
                {
                    "calendarDate": day.isoformat(),
                    "includesWellnessData": True,
                    "wellnessChronology": {"startTimestampGmt": _gmt(midnight)},
                    "movement": {
                        "steps": {"goal": 8000, "value": p["steps"], "distanceInMeters": p["steps"] * 0.78},
                        "floorsAscended": {"goal": 10, "value": r.randint(2, 25), "distanceInMeters": 30.0},
                        "floorsDescended": {"value": r.randint(2, 25)},
                    },
                    "calories": {"burnedResting": 1850, "burnedActive": active, "burnedTotal": 1850 + active},
                    "heartRate": {
                        "minValue": p["rhr"] - 3,
                        "maxValue": 150 if p["workout"] else 115,
                        "restingValue": p["rhr"],
                    },
                    "intensityMinutes": {
                        "goal": 150,
                        "moderate": r.randint(0, 40) + (30 if p["workout"] else 0),
                        "vigorous": 25 if p["workout"] else 0,
                    },
                    "stress": {
                        "avgLevel": p["stress"],
                        "maxLevel": min(99, p["stress"] + 50),
                        "restDurationInMillis": int(8.5e7 * 0.45),
                        "lowStressDurationInMillis": int(8.5e7 * 0.3),
                        "mediumStressDurationInMillis": int(8.5e7 * 0.1),
                        "highStressDurationInMillis": int(8.5e7 * 0.04 * p["stress"] / 30),
                        "activityDurationInMillis": int(8.5e7 * 0.05),
                    },
                    "bodyBattery": {
                        "minValue": max(5, 30 - p["stress"] // 3),
                        "maxValue": min(100, 60 + int(p["sleep_s"] / 1200) - (25 if p["sick"] else 0)),
                        "chargedValue": int(p["sleep_s"] / 500) - (25 if p["sick"] else 0),
                        "drainedValue": 40 + p["stress"] // 2,
                    },
                    "respiration": {"avgValue": 14 + r.randint(0, 2), "minValue": 9, "maxValue": 22},
                    "pulseOx": {"avgValue": 96 + r.randint(0, 2), "minValue": 88},
                    "hydration": {"valueInML": r.randint(1200, 2600), "goalInML": 2000},
                }
            )
        return {"data": data}

    def get_sleep_daily(self, start, end):
        result = []
        for day in _days(start, end):
            if day < CREATED or day > date.today():
                continue
            yesterday = self._day(day - timedelta(days=1))
            p = self._day(day)
            r = p["r"]
            bed = datetime.combine(day - timedelta(days=1), datetime.min.time()) + timedelta(
                hours=22.3 + r.gauss(0, 0.6) + (1.2 if day.weekday() >= 5 else 0)
            )
            wake = bed + timedelta(seconds=p["sleep_s"] + 1800)
            score = int(
                max(
                    30,
                    min(
                        98,
                        60
                        + (p["sleep_s"] - 23000) / 250
                        - (yesterday["stress"] - 30) * 0.6
                        + (5 if yesterday["workout"] else 0),
                    ),
                )
            )
            result.append(
                {
                    "calendarDate": day.isoformat(),
                    "values": {
                        "sleepScore": score,
                        "totalSleepTimeInSeconds": p["sleep_s"],
                        "deepTime": int(p["sleep_s"] * 0.18),
                        "lightTime": int(p["sleep_s"] * 0.55),
                        "remTime": int(p["sleep_s"] * 0.22),
                        "awakeTime": 1800,
                        "sleepNeed": 480,
                        "avgHeartRate": p["rhr"] + 5,
                        "respiration": 14.5,
                        "avgOvernightHrv": round(42 - (p["rhr"] - 50) * 1.5 + r.gauss(0, 3)),
                        "restingHeartRate": p["rhr"],
                        "bodyBatteryChange": int(p["sleep_s"] / 500),
                        "skinTempC": round(r.gauss(0, 0.3), 1),
                        "localSleepStartTimeInMillis": _ms(bed),
                        "localSleepEndTimeInMillis": _ms(wake),
                    },
                }
            )
        return result

    def get_hrv_data_range(self, start, end):
        summaries = []
        for day in _days(start, end):
            if CREATED <= day <= date.today():
                p = self._day(day)
                night = round(42 - (p["rhr"] - 50) * 1.5 + p["r"].gauss(0, 3))
                summaries.append(
                    {
                        "calendarDate": day.isoformat(),
                        "lastNightAvg": night,
                        "weeklyAvg": 40,
                        "lastNight5MinHigh": night + 25,
                        "baseline": {"balancedLow": 35, "balancedUpper": 46},
                        "status": "BALANCED" if night >= 35 else "UNBALANCED",
                    }
                )
        return {"hrvSummaries": summaries}

    def get_activities_by_date(self, startdate, enddate=None, activitytype=None, sortorder=None):
        result = []
        for day in _days(startdate, enddate or date.today().isoformat()):
            if day < CREATED or day > date.today():
                continue
            p = self._day(day)
            if not p["workout"]:
                continue
            r = _rng(day, "activity")
            kind = r.choice(["running", "running", "cycling", "walking", "hiking", "strength_training", "lap_swimming"])
            hour = r.choice([7, 8, 12, 18, 19, 20]) if day.weekday() < 5 else r.choice([9, 10, 11, 15])
            start = datetime.combine(day, datetime.min.time()) + timedelta(hours=hour, minutes=r.randint(0, 59))
            km = {
                "running": r.uniform(4, 16),
                "cycling": r.uniform(15, 80),
                "walking": r.uniform(3, 9),
                "hiking": r.uniform(6, 20),
            }.get(kind, 0)
            speed = {"running": 2.9, "cycling": 7.2, "walking": 1.4, "hiking": 1.2}.get(kind, 0)
            duration = km * 1000 / speed if speed else r.uniform(1800, 3600)
            lat = HOME[0] + r.uniform(-0.05, 0.05)
            lon = HOME[1] + r.uniform(-0.08, 0.08)
            result.append(
                {
                    "activityId": int(day.strftime("%Y%m%d")) * 10,
                    "activityName": f"{['Morning', 'Lunch', 'Evening'][0 if hour < 11 else 1 if hour < 16 else 2]} {kind.replace('_', ' ').title()}",
                    "startTimeLocal": start.strftime("%Y-%m-%d %H:%M:%S"),
                    "startTimeGMT": (start - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
                    "activityType": {"typeKey": kind, "parentTypeId": 17},
                    "distance": km * 1000,
                    "duration": duration,
                    "movingDuration": duration * 0.95,
                    "elevationGain": km * r.uniform(2, 12),
                    "averageHR": 145 if kind in ("running", "cycling") else 110,
                    "maxHR": 172,
                    "calories": duration / 60 * 11,
                    "averageSpeed": speed,
                    "maxSpeed": speed * 1.6,
                    "activityTrainingLoad": duration / 60 * 1.6,
                    "aerobicTrainingEffect": round(r.uniform(2, 4.5), 1),
                    "anaerobicTrainingEffect": round(r.uniform(0, 2.5), 1),
                    "startLatitude": lat if km else None,
                    "startLongitude": lon if km else None,
                    "locationName": "Utrecht",
                    "hasPolyline": bool(km),
                    "differenceBodyBattery": -int(duration / 300),
                    **{
                        f"hrTimeInZone_{z}": duration * share
                        for z, share in enumerate((0.1, 0.3, 0.35, 0.2, 0.05), start=1)
                    },
                }
            )
        return result

    def get_activity_polyline(self, activity_id):
        r = random.Random(activity_id)
        lat, lon = HOME[0] + r.uniform(-0.05, 0.05), HOME[1] + r.uniform(-0.08, 0.08)
        points = []
        heading = r.uniform(0, 2 * math.pi)
        for _ in range(300):
            heading += r.gauss(0, 0.25)
            lat += math.cos(heading) * 0.0004
            lon += math.sin(heading) * 0.0006
            points.append((lat, lon))
        return {"gPolyline": {"encodedSamples": _encode(points)}}

    def get_training_readiness_range(self, start, end):
        return [
            {
                "calendarDate": d.isoformat(),
                "score": max(5, min(100, 85 - (self._day(d)["rhr"] - 50) * 5 - (30 if self._day(d)["sick"] else 0))),
                "acuteLoad": 300,
                "inputContext": "AFTER_WAKEUP_RESET",
                "sleepScoreFactorPercent": 80,
            }
            for d in _days(start, end)
            if CREATED <= d <= date.today()
        ]

    def get_max_metrics_range(self, start, end, aggregation="daily", sport=None):
        return [
            {
                "generic": {
                    "calendarDate": d.isoformat(),
                    "vo2MaxPreciseValue": round(44 + (d - CREATED).days / 300, 1),
                },
                "cycling": None,
            }
            for d in _days(start, end)
            if CREATED <= d <= date.today() and d.weekday() == 2
        ]

    def get_race_predictions(self, startdate=None, enddate=None, _type=None):
        return [
            {
                "calendarDate": d.isoformat(),
                "time5K": 1500 - (d - CREATED).days // 20,
                "time10K": 3150,
                "timeHalfMarathon": 7000,
                "timeMarathon": 14900,
            }
            for d in _days(startdate, enddate)
            if CREATED <= d <= date.today()
        ]

    def get_weekly_training_status(self, start, end):
        rows = [
            {
                "calendarDate": d.isoformat(),
                "trainingStatus": 4,
                "weeklyTrainingLoad": 450,
                "acuteTrainingLoadDTO": {
                    "dailyTrainingLoadAcute": 320,
                    "dailyTrainingLoadChronic": 300,
                    "acwrPercent": 45,
                },
            }
            for d in _days(start, end)
            if CREATED <= d <= date.today()
        ]
        return {"reportData": {"1": rows}}

    def get_endurance_score(self, startdate, enddate=None, aggregation="weekly"):
        return {
            "groupMap": {
                d.isoformat(): {"groupAverage": 6200 + (d - CREATED).days, "groupMax": 6500}
                for d in _days(startdate, enddate)
                if CREATED <= d <= date.today() and d.weekday() == 0
            }
        }

    def get_hill_score(self, startdate, enddate=None):
        return {
            "hillScoreDTOList": [
                {"calendarDate": d.isoformat(), "overallScore": 55, "strengthScore": 50, "enduranceScore": 60}
                for d in _days(startdate, enddate)
                if CREATED <= d <= date.today() and d.day == 1
            ]
        }

    def get_fitness_age_daily(self, start, end):
        return [
            {
                "calendarDate": d.isoformat(),
                "values": {"fitnessAge": 38.5 - (d - CREATED).days / 500, "achievableFitnessAge": 35.0},
            }
            for d in _days(start, end)
            if CREATED <= d <= date.today()
        ]

    def get_heat_altitude_acclimation_range(self, start, end, aggregation="daily"):
        return [
            {
                "calendarDate": d.isoformat(),
                "heatAcclimationPercentage": 20 if d.month in (6, 7, 8) else 0,
                "altitudeAcclimation": 0,
            }
            for d in _days(start, end)
            if CREATED <= d <= date.today()
        ]

    def get_weigh_ins(self, startdate, enddate):
        summaries = []
        for d in _days(startdate, enddate):
            if d < CREATED or d > date.today() or d.weekday() not in (0, 3):
                continue
            weight = 82000 - (d - CREATED).days * 2 + _rng(d, "w").gauss(0, 400)
            metric = {
                "samplePk": int(d.strftime("%Y%m%d")),
                "timestampGMT": _ms(datetime.combine(d, datetime.min.time()) + timedelta(hours=6)),
                "calendarDate": d.isoformat(),
                "weight": weight,
                "bmi": weight / 1000 / 3.24,
                "bodyFat": 21.0,
                "bodyWater": 57.0,
                "muscleMass": 34000,
                "boneMass": 4600,
            }
            summaries.append({"summaryDate": d.isoformat(), "latestWeight": metric, "allWeightMetrics": [metric]})
        return {"dailyWeightSummaries": summaries}

    def get_blood_pressure(self, startdate, enddate):
        return {
            "measurementSummaries": [
                {
                    "measurements": [
                        {
                            "version": int(d.strftime("%Y%m%d")),
                            "systolic": 122,
                            "diastolic": 79,
                            "pulse": 58,
                            "measurementTimestampLocal": f"{d.isoformat()}T08:00:00.0",
                            "measurementTimestampGMT": f"{d.isoformat()}T07:00:00.0",
                        }
                    ]
                }
                for d in _days(startdate, enddate)
                if CREATED <= d <= date.today() and d.day == 15
            ]
        }

    def get_heart_rates(self, cdate):
        day = date.fromisoformat(cdate)
        p = self._day(day)
        start = datetime.combine(day, datetime.min.time()) - timedelta(hours=1)
        values = []
        for i in range(720):
            hour = i / 30
            awake = 7 <= hour <= 23
            hr = p["rhr"] + (18 if awake else 4) + 10 * math.sin(hour / 3) * awake + p["r"].gauss(0, 3)
            values.append([_ms(start + timedelta(minutes=2 * i)), round(hr)])
        return {"startTimestampGMT": _gmt(start), "heartRateValues": values}

    def get_stress_data(self, cdate):
        day = date.fromisoformat(cdate)
        p = self._day(day)
        start = datetime.combine(day, datetime.min.time()) - timedelta(hours=1)
        stress, battery = [], []
        level = 40
        for i in range(480):
            hour = i / 20
            awake = 7 <= hour <= 23
            s = max(0, min(99, p["stress"] + (15 * math.sin(hour / 2) if awake else -15) + p["r"].gauss(0, 8)))
            level = max(5, min(100, level + (-0.6 if awake else 0.9)))
            stamp = _ms(start + timedelta(minutes=3 * i))
            stress.append([stamp, round(s)])
            battery.append([stamp, "MEASURED", round(level), 3.0])
        return {"startTimestampGMT": _gmt(start), "stressValuesArray": stress, "bodyBatteryValuesArray": battery}

    def get_personal_record(self):
        return [
            {
                "id": 1,
                "typeId": 3,
                "activityType": "running",
                "value": 1450.0,
                "activityId": 20240301 * 10,
                "activityName": "Morning Running",
                "actStartDateTimeInGMTFormatted": "2024-03-01T07:30:00.0",
            }
        ]

    def get_earned_badges(self):
        return [
            {
                "badgeId": i,
                "badgeName": f"Badge {i}",
                "badgeEarnedDate": f"{(CREATED + timedelta(days=i * 23)).isoformat()}T10:00:00.0",
                "badgeEarnedNumber": 1,
                "badgePoints": 2,
            }
            for i in range(1, 30)
        ]

    def get_gear_list(self):
        return [
            {
                "uuid": "a",
                "gearType": "SHOES",
                "status": "ACTIVE",
                "name": "Daily trainers",
                "brand": "Brand",
                "model": "Model",
                "maxUsageDistanceMeters": 800000.0,
                "distanceUsedMeters": 523000.0,
                "numActivitiesLinked": 74,
                "firstUseDate": "2024-01-02",
            }
        ]

    def get_devices(self):
        return [
            {"productDisplayName": "Forerunner 965", "deviceStatus": "active", "imageUrl": None},
            {"productDisplayName": "Index S2 Smart Scale", "deviceStatus": "active"},
        ]

    def get_lifetime_totals(self):
        return {
            "totalDistance": 7_450_000,
            "totalSteps": 9_500_000,
            "totalCalories": 4_100_000,
            "totalActiveDays": 900,
            "totalGoalsMetInDays": 600,
        }

    def get_user_statistics(self):
        return {"userMetrics": [{"totalActivities": 400}]}

    def get_goals(self, status="active", start=1, limit=30):
        return []

    def get_lactate_threshold(self):
        return {"speed_and_heart_rate": {"heartRate": 168, "speed": 3.6}}

    # Activity details ---------------------------------------------------
    def _activity(self, activity_id):
        day = datetime.strptime(str(activity_id // 10), "%Y%m%d").date()
        found = self.get_activities_by_date(day.isoformat(), day.isoformat())
        return found[0] if found else None

    def get_activity_splits(self, activity_id):
        a = self._activity(activity_id)
        if not a or not a["distance"]:
            return {"lapDTOs": []}
        laps, left = [], a["distance"]
        r = random.Random(activity_id)
        while left > 0:
            d = min(1000.0 if a["activityType"]["typeKey"] != "cycling" else 5000.0, left)
            laps.append(
                {
                    "distance": d,
                    "duration": d / a["averageSpeed"] * r.uniform(0.92, 1.08),
                    "averageHR": a["averageHR"] + r.randint(-6, 8),
                    "maxHR": a["maxHR"] - r.randint(0, 10),
                    "averageSpeed": a["averageSpeed"] * r.uniform(0.92, 1.08),
                    "elevationGain": r.uniform(0, 20),
                }
            )
            left -= d
        return {"lapDTOs": laps}

    def get_activity_details(self, activity_id, maxchart=2000, maxpoly=4000):
        a = self._activity(activity_id)
        if not a:
            return {}
        r = random.Random(activity_id)
        n = 400
        rows = []
        for i in range(n):
            t = a["duration"] * i / (n - 1)
            hr = a["averageHR"] - 25 * math.exp(-i / 40) + 6 * math.sin(i / 30) + r.gauss(0, 2)
            speed = (a["averageSpeed"] or 0) * (1 + 0.08 * math.sin(i / 23)) + r.gauss(0, 0.05)
            rows.append(
                {
                    "metrics": [
                        t,
                        (a["distance"] or 0) * i / (n - 1),
                        round(hr),
                        max(0, speed),
                        5 + 12 * math.sin(i / 60),
                    ]
                }
            )
        keys = ["sumDuration", "sumDistance", "directHeartRate", "directSpeed", "directElevation"]
        return {
            "metricDescriptors": [{"metricsIndex": i, "key": k} for i, k in enumerate(keys)],
            "activityDetailMetrics": rows,
        }

    def get_activity_weather(self, activity_id):
        r = random.Random(activity_id)
        return {
            "temp": r.randint(35, 80),
            "apparentTemp": r.randint(33, 80),
            "relativeHumidity": r.randint(40, 95),
            "windSpeed": r.randint(2, 30),
            "windDirectionCompassPoint": r.choice(["n", "sw", "w"]),
            "weatherTypeDTO": {"desc": r.choice(["Cloudy", "Sunny", "Light rain"])},
        }

    def get_activity_exercise_sets(self, activity_id):
        r = random.Random(activity_id)
        sets = []
        for name in ("BENCH_PRESS", "SQUAT", "PULL_UP"):
            for _ in range(3):
                sets.append(
                    {
                        "setType": "ACTIVE",
                        "repetitionCount": r.randint(6, 12),
                        "weight": r.choice([0, 40000, 60000]),
                        "duration": 40,
                        "exercises": [{"category": name, "name": name}],
                    }
                )
                sets.append({"setType": "REST", "duration": 90})
        return {"exerciseSets": sets}

    # Sleep in detail, steps and breathing through the day ---------------
    def get_sleep_data(self, cdate):
        day = date.fromisoformat(cdate)
        sleep = self.get_sleep_daily(cdate, cdate)
        if not sleep:
            return {}
        values = sleep[0]["values"]
        start_local = values["localSleepStartTimeInMillis"]
        start = start_local - 3600 * 1000
        end = start + (values["totalSleepTimeInSeconds"] + 1800) * 1000
        r = _rng(day, "sleep")
        levels, at = [], start
        while at < end:
            length = r.randint(10, 50) * 60000
            levels.append(
                {
                    "startGMT": _gmt(datetime.fromtimestamp(at / 1000, UTC).replace(tzinfo=None)),
                    "endGMT": _gmt(datetime.fromtimestamp(min(end, at + length) / 1000, UTC).replace(tzinfo=None)),
                    "activityLevel": r.choice([0, 1, 1, 1, 2, 2, 3]),
                }
            )
            at += length
        minutes = range(0, int((end - start) / 60000), 5)
        return {
            "dailySleepDTO": {
                "calendarDate": cdate,
                "sleepStartTimestampGMT": start,
                "sleepEndTimestampGMT": end,
                "sleepStartTimestampLocal": start_local,
            },
            "sleepLevels": levels,
            "sleepHeartRate": [
                {"value": values["avgHeartRate"] + round(4 * math.sin(m / 50)), "startGMT": start + m * 60000}
                for m in minutes
            ],
            "wellnessEpochSPO2DataDTOList": [
                {
                    "epochTimestamp": _gmt(
                        datetime.fromtimestamp((start + m * 60000) / 1000, UTC).replace(tzinfo=None)
                    ),
                    "spo2Reading": 95 + r.randint(-4, 3),
                }
                for m in minutes
            ],
            "hrvData": [
                {"value": values["avgOvernightHrv"] + r.randint(-8, 8), "startGMT": start + m * 60000} for m in minutes
            ],
            "restlessMomentsCount": r.randint(10, 60),
        }

    def get_steps_data(self, cdate):
        day = date.fromisoformat(cdate)
        p = self._day(day)
        start = datetime.combine(day, datetime.min.time()) - timedelta(hours=1)
        weights = [0 if h < 7 or h > 22 else (3 if h in (8, 12, 17, 18) else 1) for h in range(24)]
        total = sum(weights)
        blocks = []
        for i in range(96):
            share = weights[i // 4] / total / 4
            blocks.append(
                {
                    "startGMT": _gmt(start + timedelta(minutes=15 * i)),
                    "endGMT": _gmt(start + timedelta(minutes=15 * (i + 1))),
                    "steps": int(p["steps"] * share * p["r"].uniform(0.5, 1.5)),
                }
            )
        return blocks

    def get_respiration_data(self, cdate):
        day = date.fromisoformat(cdate)
        start = datetime.combine(day, datetime.min.time()) - timedelta(hours=1)
        r = _rng(day, "resp")
        return {
            "startTimestampGMT": _gmt(start),
            "respirationValuesArray": [
                [_ms(start + timedelta(minutes=2 * i)), 13 + r.randint(0, 5)] for i in range(720)
            ],
        }

    # Planning --------------------------------------------------------------
    def get_workouts(self, start=0, limit=100):
        return [{"workoutId": 1, "workoutName": "Intervals 6x800", "sportType": {"sportTypeKey": "running"}}]

    def get_training_plans(self, limit=None, start=None, status=None):
        return {
            "trainingPlanList": [
                {
                    "trainingPlanId": 7,
                    "name": "Half marathon, intermediate",
                    "trainingStatus": {"statusKey": "Scheduled"},
                    "endDate": (date.today() + timedelta(days=60)).isoformat(),
                }
            ]
        }

    def get_upcoming_events(self, days_forward=90, limit=10):
        return [
            {
                "id": 99,
                "eventName": "City half marathon",
                "date": (date.today() + timedelta(days=60)).isoformat(),
                "location": "Utrecht",
            }
        ]

    def get_scheduled_workouts_by_period(self, start, end):
        return [{"title": "Easy run", "date": (date.today() + timedelta(days=1)).isoformat()}]

    def get_calendar_events(self, start, end):
        return [
            {"id": 50, "eventName": "Spring 10K", "date": d.isoformat()}
            for d in _days(start, end)
            if d.month == 4 and d.day == 14 and d >= CREATED
        ]

    # The optional sets: little or nothing, as for most accounts
    def get_ecg_daily(self, start, end):
        return {"content": []}

    def get_health_snapshots_range(self, start, end):
        return []

    def get_glucose_data_range(self, start, end):
        return {"glucoseList": []}

    def get_jetlag_trips(self):
        return []

    def get_lifestyle_logging_data(self, cdate):
        return {"calendarDate": cdate, "dailyLogsReport": []}

    def get_running_economy_range(self, start, end, aggregation="daily"):
        return []

    def get_cycling_ability_range(self, start, end, aggregation="DAILY"):
        return {}

    def get_critical_swim_speed_range(self, start, end, aggregation=None):
        return []

    def get_cycling_ftp(self):
        return None

    def get_power_zones(self):
        return []

    def get_heart_rate_zones(self):
        return []

    def get_calorie_summary(self, start, end, current_date=None):
        return {"dailyNutritionContents": []}

    def get_menstrual_calendar_data(self, startdate, enddate):
        return {"cycleSummaries": []}

    def get_golf_summary(self, start=0, limit=100):
        return {"pageNumber": 1, "rowsPerPage": 100, "totalRows": 0}

    def get_golf_club_stats(self, limit=100):
        return []


def _encode(points) -> str:
    """Google's polyline encoding (the reverse of datasets.decode_polyline)."""
    out, last_lat, last_lon = [], 0, 0
    for lat, lon in points:
        for value, last in ((round(lat * 1e5), last_lat), (round(lon * 1e5), last_lon)):
            delta = value - last
            delta = ~(delta << 1) if delta < 0 else delta << 1
            while delta >= 0x20:
                out.append(chr((0x20 | (delta & 0x1F)) + 63))
                delta >>= 5
            out.append(chr(delta + 63))
        last_lat, last_lon = round(lat * 1e5), round(lon * 1e5)
    return "".join(out)
