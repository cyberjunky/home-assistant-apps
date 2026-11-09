import time

from src.scheduler import is_due


def obj(**kw):
    base = {"schedule": "daily", "enabled": 1, "authorised": 1, "last_scan_at": None, "id": 1}
    return {**base, **kw}


def test_is_due():
    now = time.time()
    assert is_due(obj(last_scan_at=None), now)
    assert is_due(obj(last_scan_at=now - 2 * 86400), now)
    assert not is_due(obj(last_scan_at=now - 100), now)
    assert not is_due(obj(schedule=""), now)
    assert not is_due(obj(enabled=0), now)
    assert not is_due(obj(authorised=0), now)
    assert is_due(obj(schedule="weekly", last_scan_at=now - 8 * 86400), now)
