"""With the default data, parsing gives exactly what it gave with 1.4.14's hard-coded lists."""

import json
from pathlib import Path

from tests.parse_snapshot import snapshot

EXPECTED = Path(__file__).parent / "data" / "parse_snapshot.json"


def test_parsing_is_unchanged(tmp_path):
    got = json.loads(json.dumps(snapshot(str(tmp_path / "m.sqlite3")), ensure_ascii=False, sort_keys=True))
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    for i, (g, e) in enumerate(zip(got["messages"], expected["messages"], strict=True)):
        assert g == e, f"message {i}: {e.get('body')}"
    assert got == expected
