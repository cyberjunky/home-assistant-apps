"""Schedules, mobile access and the chargers nearby: extras that must never cost the car's data or its sleep."""

import asyncio
import time

import pytest

from src.config import Config
from src.monitor import nearby_sites, normalize
from src.tesla_api import DATA_ENDPOINTS, TeslaApi, TeslaApiError, VehicleUnavailable

from .conftest import FakeApi, make_monitor, vehicle_data

NEARBY = {
    "destination_charging": [
        {"location": {"lat": 52.2, "long": 5.2}, "name": "Hotel", "type": "destination", "distance_miles": 10.0}
    ],
    "superchargers": [
        {
            "location": {"lat": 52.3, "long": 5.3},
            "name": "Far Supercharger",
            "distance_miles": 20.0,
            "available_stalls": 0,
            "total_stalls": 8,
            "site_closed": True,
        },
        {
            "location": {"lat": 52.11, "long": 5.11},
            "name": "Near Supercharger",
            "distance_miles": 1.0,
            "available_stalls": 5,
            "total_stalls": 12,
            "site_closed": False,
        },
    ],
}


def test_schedules_are_read_from_the_cars_data():
    now = time.time()
    data = vehicle_data(now) | dict(
        charge_schedule_data={
            "charge_schedules": [
                {
                    "days_of_week": 62,
                    "start_enabled": True,
                    "start_time": 60,
                    "end_enabled": False,
                    "end_time": 360,
                    "enabled": True,
                },
                {
                    "days_of_week": 65,
                    "start_enabled": False,
                    "start_time": 0,
                    "end_enabled": True,
                    "end_time": 540,
                    "one_time": True,
                },
            ]
        },
        preconditioning_schedule_data={
            "precondition_schedules": [{"days_of_week": 127, "precondition_time": 450, "enabled": True}]
        },
    )
    snapshot = normalize(data, now)
    first, second = snapshot["charge_schedules"]
    # A time that isn't switched on is left out
    assert (first["days"], first["start"], first["end"], first["enabled"]) == (62, 60, None, True)
    assert (second["start"], second["end"], second["one_time"], second["enabled"]) == (None, 540, True, False)
    assert snapshot["precondition_schedules"] == [
        {"name": "", "days": 127, "one_time": False, "enabled": True, "lat": None, "lon": None, "time": 450}
    ]


def test_a_car_without_schedules_reports_none():
    snapshot = normalize(vehicle_data(time.time()), time.time())
    assert snapshot["charge_schedules"] is None
    assert snapshot["precondition_schedules"] is None


def test_nearby_sites_are_metric_and_nearest_first():
    result = nearby_sites(NEARBY, 1000.4)
    assert result["ts"] == 1000
    assert [s["name"] for s in result["sites"]] == ["Near Supercharger", "Hotel", "Far Supercharger"]
    near, hotel, far = result["sites"]
    assert near == {
        "kind": "supercharger",
        "name": "Near Supercharger",
        "lat": 52.11,
        "lon": 5.11,
        "distance_km": 1.609,
        "available": 5,
        "total": 12,
        "closed": False,
    }
    assert (hotel["kind"], hotel["available"], hotel["total"]) == ("destination", None, None)
    assert far["closed"] is True
    assert nearby_sites({}, 0)["sites"] == []


def test_extras_are_read_once_along_with_the_cars_data(database):
    api = FakeApi(lambda now: vehicle_data(now))
    api.nearby = NEARBY
    monitor = make_monitor(database, api, Config(streaming=False))

    async def scenario():
        await monitor._poll()
        await monitor._poll()
        await monitor._poll()

    asyncio.run(scenario())
    kinds = [kind for kind, _ in api.calls]
    assert kinds.count("vehicle_data") == 3
    # Not again with every read
    assert kinds.count("mobile_enabled") == 1
    assert kinds.count("nearby_charging_sites") == 1
    view = monitor.view()
    assert view["mobile_access"] is True
    assert len(view["nearby"]["sites"]) == 3


def test_a_sleeping_car_is_not_asked_for_extras(database):
    api = FakeApi(lambda now: vehicle_data(now))

    async def asleep(tesla_id):
        api.calls.append(("vehicle", time.time()))
        return {"state": "asleep"}

    api.vehicle = asleep
    monitor = make_monitor(database, api, Config(streaming=False))
    asyncio.run(monitor._poll())
    assert [kind for kind, _ in api.calls] == ["vehicle"]


@pytest.mark.parametrize("error", [VehicleUnavailable("vehicle unavailable", 408), TeslaApiError("forbidden", 403)])
def test_failing_extras_leave_the_cars_data_alone_and_wait(database, error):
    api = FakeApi(lambda now: vehicle_data(now, soc=61))
    api.extras_fail = error
    monitor = make_monitor(database, api, Config(streaming=False))

    async def scenario():
        await monitor._poll()
        await monitor._poll()

    asyncio.run(scenario())
    kinds = [kind for kind, _ in api.calls]
    assert kinds.count("mobile_enabled") == 1 and kinds.count("nearby_charging_sites") == 1
    view = monitor.view()
    assert view["soc"] == 61
    assert view["mobile_access"] is None and view["nearby"] is None


def _api_refusing_schedules(error: Exception) -> tuple[TeslaApi, list[str]]:
    api = TeslaApi(auth=None)
    asked: list[str] = []

    async def request(method, path, params=None, **more):
        asked.append(params["endpoints"])
        if "schedule" in params["endpoints"]:
            raise error
        return {"state": "online"}

    api._request = request
    return api, asked


def test_refused_schedules_are_left_out_from_then_on():
    api, asked = _api_refusing_schedules(TeslaApiError("invalid endpoints", 400))

    async def scenario():
        assert await api.vehicle_data(1) == {"state": "online"}
        assert await api.vehicle_data(1) == {"state": "online"}

    asyncio.run(scenario())
    assert [e == DATA_ENDPOINTS for e in asked] == [False, True, True]


def test_a_sleeping_car_does_not_switch_the_schedules_off():
    api, asked = _api_refusing_schedules(VehicleUnavailable("vehicle unavailable", 408))

    async def scenario():
        for _ in range(2):
            with pytest.raises(VehicleUnavailable):
                await api.vehicle_data(1)

    asyncio.run(scenario())
    assert all("schedule" in e for e in asked) and len(asked) == 2
