"""The ingest port's gate and the per-sender rate cap."""

from src.changes import count_score, items
from src.ingest import RateLimiter, is_private


def test_private_addresses():
    for address in ("10.1.2.3", "172.20.0.5", "192.168.1.1", "127.0.0.1", "::1", "fd00::1", "::ffff:192.168.1.5"):
        assert is_private(address), address
    for address in ("8.8.8.8", "172.32.0.1", "2001:db8::1", "100.64.0.1", "", None, "nonsense"):
        assert not is_private(address), address


def test_rate_limiter():
    limiter = RateLimiter(limit=10)
    assert limiter.allow("a", 6, now=1) == 6
    assert limiter.allow("a", 6, now=1) == 4
    assert limiter.allow("a", 1, now=1) == 0
    assert limiter.allow("b", 3, now=1) == 3
    assert limiter.allow("a", 6, now=2) == 6


def test_change_scores_rank_silence_and_new_errors():
    assert count_score(10, 10) == 0
    assert count_score(400, 20) > count_score(30, 20)
    data = {
        "hosts": [{"host": "nas", "cur": 0, "prev": 500}, {"host": "steady", "cur": 100, "prev": 98}],
        "severities": [{"severity": 3, "cur": 200, "prev": 4}],
        "patterns": [{"id": 9, "cur": 50, "prev": 0, "template": "disk <*> full", "first_seen": 1100.0}],
        "metrics": [
            {
                "name": "temp",
                "labels": {"room": "attic"},
                "cur": 40.0,
                "prev_mean": 20.0,
                "prev_std": 1.0,
                "prev_n": 60,
            },
            {"name": "calm", "labels": {}, "cur": 20.5, "prev_mean": 20.0, "prev_std": 1.0, "prev_n": 60},
        ],
    }
    result = items(data, 1000.0, 2000.0)
    kinds = [i["kind"] for i in result]
    assert sorted(kinds) == ["host", "metric", "pattern", "severity"]
    assert all(set(i) == {"kind", "title", "detail", "score", "link"} for i in result)
    host = next(i for i in result if i["kind"] == "host")
    assert host["title"] == "nas went silent" and host["link"] == "#logs/host%3Anas/1000/2000"
    assert result == sorted(result, key=lambda i: -i["score"])
    metric = next(i for i in result if i["kind"] == "metric")
    assert (
        metric["link"]
        == "#metrics/%5B%7B%22filter%22%3A%7B%22room%22%3A%22attic%22%7D%2C%22name%22%3A%22temp%22%7D%5D/1000/2000"
    )


class _Writer:
    def __init__(self):
        self.logs, self.samples, self.dropped = [], [], 0

    def put_log(self, entry):
        self.logs.append(entry)
        return True

    def put_samples(self, samples):
        self.samples.extend(samples)
        return len(samples)


async def _client(token: str):
    from aiohttp.test_utils import TestClient, TestServer

    from src.database import Sources
    from src.ingest import IngestServer, Intake

    writer = _Writer()
    server = IngestServer(0, token, Intake(writer, Sources(), limit=3))
    client = TestClient(TestServer(server.app, host="127.0.0.1"))
    await client.start_server()
    return client, writer


async def test_ingest_token():
    client, writer = await _client("s3cret")
    try:
        line = '{"host": "a", "message": "hello"}'
        assert (await client.post("/ingest/logs", data=line)).status == 401
        response = await client.post("/ingest/logs", data=line, headers={"Authorization": "Bearer wrong"})
        assert response.status == 401 and "s3cret" not in await response.text()
        response = await client.post("/ingest/logs", data=line, headers={"Authorization": "Bearer s3cret"})
        assert response.status == 200 and await response.json() == {"accepted": 1, "rejected": 0}
        assert writer.logs[0].message == "hello"
        assert (await client.get("/health")).status == 200
    finally:
        await client.close()


async def test_ingest_without_token_from_a_private_address_and_limits():
    client, _writer = await _client("")
    try:
        lines = "\n".join(f'{{"message": "m{i}"}}' for i in range(5))
        response = await client.post("/ingest/logs", data=lines)
        # 127.0.0.1 is allowed; the cap of 3 per second drops the rest
        assert await response.json() == {"accepted": 3, "rejected": 2}
        response = await client.post("/v1/logs", data=b"x", headers={"Content-Type": "application/x-protobuf"})
        assert response.status == 415
        response = await client.post("/v1/metrics", data=b"{not json", headers={"Content-Type": "application/json"})
        assert response.status == 400
        response = await client.post("/ingest/logs", data=b"x" * (5 * 1024 * 1024 + 10))
        assert response.status == 413
    finally:
        await client.close()
