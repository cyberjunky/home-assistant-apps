"""The app's database: one SQLite file in the app's storage, with a full-text index over everything
that is saved and a history of what happened to each item."""

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

STATUSES = ["inbox", "later", "done", "archived"]
# The first of an item's two dates (due, happens on), or '' without one
NEXT_DATE = (
    "(CASE WHEN i.due = '' THEN i.happens_on WHEN i.happens_on = '' THEN i.due ELSE min(i.due, i.happens_on) END)"
)
SORTS = {
    "new": "i.created_at DESC",
    "date": f"{NEXT_DATE} = '', {NEXT_DATE}, i.created_at DESC",
    "old": "i.created_at ASC",
    "priority": "i.priority DESC, i.created_at DESC",
    "touched": "i.updated_at DESC",
}
# Columns the page may change
EDITABLE = {"title", "note", "category", "tags", "purpose", "priority", "status", "summary", "due", "happens_on"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'link',
    url TEXT,
    url_key TEXT,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'web',
    site TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    page_kind TEXT NOT NULL DEFAULT '',
    image TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'Other',
    tags TEXT NOT NULL DEFAULT '[]',
    purpose TEXT NOT NULL DEFAULT 'read',
    priority INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'inbox',
    due TEXT NOT NULL DEFAULT '',
    happens_on TEXT NOT NULL DEFAULT '',
    sorted_by TEXT NOT NULL DEFAULT '',
    fetch_state TEXT NOT NULL DEFAULT 'waiting',
    fetch_error TEXT NOT NULL DEFAULT '',
    via TEXT NOT NULL DEFAULT 'share',
    caller TEXT NOT NULL DEFAULT '',
    shares INTEGER NOT NULL DEFAULT 1,
    extra TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    fetched_at REAL,
    opened_at REAL,
    done_at REAL
);
CREATE INDEX IF NOT EXISTS items_url_key ON items(url_key);
CREATE INDEX IF NOT EXISTS items_status ON items(status, created_at);
CREATE INDEX IF NOT EXISTS items_created ON items(created_at);
CREATE INDEX IF NOT EXISTS items_fetch ON items(fetch_state);

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    title, description, summary, note, text, tags, category, site, detail, author,
    content='items', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts(rowid, title, description, summary, note, text, tags, category, site, detail, author)
    VALUES (new.id, new.title, new.description, new.summary, new.note, new.text, new.tags, new.category, new.site,
            new.detail, new.author);
END;
CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, description, summary, note, text, tags, category, site, detail,
                          author)
    VALUES ('delete', old.id, old.title, old.description, old.summary, old.note, old.text, old.tags, old.category,
            old.site, old.detail, old.author);
END;
CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE OF title, description, summary, note, text, tags, category, site,
                                                      detail, author ON items BEGIN
    INSERT INTO items_fts(items_fts, rowid, title, description, summary, note, text, tags, category, site, detail,
                          author)
    VALUES ('delete', old.id, old.title, old.description, old.summary, old.note, old.text, old.tags, old.category,
            old.site, old.detail, old.author);
    INSERT INTO items_fts(rowid, title, description, summary, note, text, tags, category, site, detail, author)
    VALUES (new.id, new.title, new.description, new.summary, new.note, new.text, new.tags, new.category, new.site,
            new.detail, new.author);
END;

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    item_id INTEGER,
    at REAL NOT NULL,
    what TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_at ON events(at);
CREATE INDEX IF NOT EXISTS events_item ON events(item_id);
"""

# What the list and the cards need; the page text stays in the database until an item is opened
CARD_COLUMNS = (
    "i.id, i.kind, i.url, i.title, i.description, i.summary, i.note, i.source, i.site, i.detail, i.author, "
    "i.page_kind, i.image, i.category, i.tags, i.purpose, i.priority, i.status, i.sorted_by, i.fetch_state, "
    "i.fetch_error, i.via, i.shares, i.due, i.happens_on, i.created_at, i.updated_at, i.opened_at, i.done_at"
)


def fts_query(text: str) -> str:
    """A search box's words as an FTS5 query: every word must be there, each as the start of a word.

    "quoted words" stay together. Anything FTS5 would read as syntax is dropped, so no search
    can fail on what was typed.
    """
    parts = []
    for phrase, word in re.findall(r'"([^"]+)"|(\S+)', text or ""):
        if phrase:
            tokens = re.findall(r"\w+", phrase)
            if tokens:
                parts.append('"' + " ".join(tokens) + '"')
        else:
            for token in re.findall(r"\w+", word):
                parts.append(f'"{token}"*')
    return " AND ".join(parts)


class Store:
    """All reads and writes of the database. Thread-safe: callers use asyncio.to_thread."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------------
    def _row(self, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        item = dict(row)
        for key, empty in (("tags", []), ("extra", {})):
            if key in item:
                try:
                    item[key] = json.loads(item[key] or "null") or empty
                except ValueError:
                    item[key] = empty
        return item

    def log(self, item_id: int | None, what: str, detail: str = "", at: float | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO events(item_id, at, what, detail) VALUES (?, ?, ?, ?)",
                (item_id, at or time.time(), what, detail[:300]),
            )
            self._db.commit()

    def add(self, fields: dict) -> int:
        now = time.time()
        values = {"created_at": now, "updated_at": now, **fields}
        for key in ("tags", "extra"):
            if key in values and not isinstance(values[key], str):
                values[key] = json.dumps(values[key])
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        with self._lock:
            cursor = self._db.execute(f"INSERT INTO items({columns}) VALUES ({marks})", list(values.values()))
            item_id = cursor.lastrowid
            self._db.execute(
                "INSERT INTO events(item_id, at, what, detail) VALUES (?, ?, 'saved', ?)",
                (item_id, values["created_at"], values.get("via", "")),
            )
            self._db.commit()
        return int(item_id)

    def update(self, item_id: int, fields: dict, touch: bool = True) -> None:
        if not fields:
            return
        values = dict(fields)
        for key in ("tags", "extra"):
            if key in values and not isinstance(values[key], str):
                values[key] = json.dumps(values[key])
        if touch:
            values["updated_at"] = time.time()
        sets = ", ".join(f"{k} = ?" for k in values)
        with self._lock:
            self._db.execute(f"UPDATE items SET {sets} WHERE id = ?", [*values.values(), item_id])
            self._db.commit()

    def get(self, item_id: int, history: bool = False) -> dict | None:
        with self._lock:
            item = self._row(self._db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())
            if item and history:
                item["history"] = [
                    dict(r)
                    for r in self._db.execute(
                        "SELECT at, what, detail FROM events WHERE item_id = ? ORDER BY at DESC, id DESC LIMIT 50",
                        (item_id,),
                    )
                ]
        return item

    def by_url_key(self, key: str, other_than: int | None = None) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM items WHERE url_key = ? AND id != ? ORDER BY id LIMIT 1", (key, other_than or -1)
            ).fetchone()
        return self._row(row)

    def delete(self, item_id: int) -> dict | None:
        with self._lock:
            item = self.get(item_id)
            if item:
                self._db.execute("DELETE FROM items WHERE id = ?", (item_id,))
                self._db.execute(
                    "INSERT INTO events(item_id, at, what, detail) VALUES (NULL, ?, 'deleted', ?)",
                    (time.time(), (item.get("title") or item.get("url") or "")[:300]),
                )
                self._db.commit()
        return item

    def waiting(self) -> list[int]:
        """Items whose page is still to be read or that are still to be sorted (after a restart)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id FROM items WHERE fetch_state = 'waiting' OR sorted_by = '' ORDER BY id"
            ).fetchall()
        return [r["id"] for r in rows]

    def known_tags(self, limit: int = 80) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT j.value AS tag, COUNT(*) AS n FROM items, json_each(items.tags) AS j "
                "GROUP BY j.value ORDER BY n DESC, tag LIMIT ?",
                (limit,),
            ).fetchall()
        return [r["tag"] for r in rows]

    # ------------------------------------------------------------------
    def search(
        self,
        q: str = "",
        status: str = "",
        category: str = "",
        source: str = "",
        tag: str = "",
        purpose: str = "",
        priority: int | None = None,
        dated: bool = False,
        since: float | None = None,
        until: float | None = None,
        sort: str = "new",
        limit: int = 60,
        offset: int = 0,
    ) -> dict:
        """Items that match, newest first by default, with the total and per-facet counts."""
        where, args = [], []
        match = fts_query(q)
        if match:
            where.append("i.id IN (SELECT rowid FROM items_fts WHERE items_fts MATCH ?)")
            args.append(match)
        base_where, base_args = list(where), list(args)
        if status == "open":
            where.append("i.status IN ('inbox', 'later')")
        elif status in STATUSES:
            where.append("i.status = ?")
            args.append(status)
        for column, value in (("category", category), ("source", source), ("purpose", purpose)):
            if value:
                where.append(f"i.{column} = ?")
                args.append(value)
        if tag:
            where.append("EXISTS (SELECT 1 FROM json_each(i.tags) WHERE value = ?)")
            args.append(tag)
        if priority is not None:
            where.append("i.priority = ?")
            args.append(priority)
        if dated:
            where.append("(i.due != '' OR i.happens_on != '')")
        if since:
            where.append("i.created_at >= ?")
            args.append(since)
        if until:
            where.append("i.created_at < ?")
            args.append(until)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        order = SORTS.get(sort, SORTS["new"])
        if match and sort == "relevance":
            order = "i.created_at DESC"
        with self._lock:
            total = self._db.execute(f"SELECT COUNT(*) FROM items i {clause}", args).fetchone()[0]
            rows = self._db.execute(
                f"SELECT {CARD_COLUMNS} FROM items i {clause} ORDER BY {order} LIMIT ? OFFSET ?",
                [*args, max(1, min(limit, 500)), max(0, offset)],
            ).fetchall()
            facets = {}
            base = f"WHERE {' AND '.join(base_where)}" if base_where else ""
            for column in ("status", "category", "source", "purpose"):
                facets[column] = {
                    r[0]: r[1]
                    for r in self._db.execute(
                        f"SELECT i.{column}, COUNT(*) FROM items i {base} GROUP BY i.{column}", base_args
                    )
                }
        return {"total": total, "items": [self._row(r) for r in rows], "facets": facets}

    def counts(self) -> dict:
        with self._lock:
            by_status = dict(self._db.execute("SELECT status, COUNT(*) FROM items GROUP BY status").fetchall())
            waiting = self._db.execute(
                "SELECT COUNT(*) FROM items WHERE fetch_state = 'waiting' OR sorted_by = ''"
            ).fetchone()[0]
        return {
            "total": sum(by_status.values()),
            "by_status": {s: by_status.get(s, 0) for s in STATUSES},
            "waiting": waiting,
        }

    def stats(self, days: int = 90) -> dict:
        """Everything the overview shows."""
        now = time.time()
        start = now - days * 86400
        with self._lock:
            db = self._db

            def grouped(sql: str, *args: Any) -> list[dict]:
                return [dict(r) for r in db.execute(sql, args)]

            per_day_saved = grouped(
                "SELECT date(created_at, 'unixepoch', 'localtime') AS day, COUNT(*) AS n FROM items "
                "WHERE created_at >= ? GROUP BY day",
                start,
            )
            per_day_done = grouped(
                "SELECT date(at, 'unixepoch', 'localtime') AS day, COUNT(*) AS n FROM events "
                "WHERE at >= ? AND what IN ('done', 'archived') GROUP BY day",
                start,
            )
            open_ = "status IN ('inbox', 'later')"
            result = {
                "days": days,
                "counts": self.counts(),
                "per_day": {"saved": per_day_saved, "done": per_day_done},
                "categories": grouped(
                    f"SELECT category AS name, COUNT(*) AS n, SUM({open_}) AS open FROM items "
                    "GROUP BY category ORDER BY n DESC"
                ),
                "sources": grouped("SELECT source AS name, COUNT(*) AS n FROM items GROUP BY source ORDER BY n DESC"),
                "purposes": grouped(
                    f"SELECT purpose AS name, COUNT(*) AS n FROM items WHERE {open_} GROUP BY purpose ORDER BY n DESC"
                ),
                "tags": grouped(
                    "SELECT j.value AS name, COUNT(*) AS n FROM items, json_each(items.tags) AS j "
                    "GROUP BY j.value ORDER BY n DESC, name LIMIT 40"
                ),
                "sites": grouped(
                    "SELECT site AS name, COUNT(*) AS n FROM items WHERE site != '' "
                    "GROUP BY site ORDER BY n DESC LIMIT 12"
                ),
                "week": dict(
                    db.execute(
                        "SELECT (SELECT COUNT(*) FROM items WHERE created_at >= ?) AS saved, "
                        "(SELECT COUNT(*) FROM events WHERE at >= ? AND what IN ('done', 'archived')) AS handled, "
                        "(SELECT COUNT(*) FROM events WHERE at >= ? AND what = 'shared again') AS again",
                        (now - 7 * 86400, now - 7 * 86400, now - 7 * 86400),
                    ).fetchone()
                ),
                "inbox_age": dict(
                    db.execute(
                        "SELECT SUM(created_at >= ?) AS week, SUM(created_at < ? AND created_at >= ?) AS month, "
                        "SUM(created_at < ? AND created_at >= ?) AS quarter, SUM(created_at < ?) AS older "
                        "FROM items WHERE status = 'inbox'",
                        (
                            now - 7 * 86400,
                            now - 7 * 86400,
                            now - 30 * 86400,
                            now - 30 * 86400,
                            now - 90 * 86400,
                            now - 90 * 86400,
                        ),
                    ).fetchone()
                ),
                "high": [
                    self._row(r)
                    for r in db.execute(
                        f"SELECT {CARD_COLUMNS} FROM items i WHERE i.{open_} AND i.priority = 2 "
                        "ORDER BY i.created_at DESC LIMIT 6"
                    )
                ],
                "recent": [
                    self._row(r)
                    for r in db.execute(f"SELECT {CARD_COLUMNS} FROM items i ORDER BY i.created_at DESC LIMIT 8")
                ],
                # Saved a while ago, never opened, still open: what is being forgotten
                "forgotten": [
                    self._row(r)
                    for r in db.execute(
                        f"SELECT {CARD_COLUMNS} FROM items i WHERE i.{open_} AND i.opened_at IS NULL "
                        "AND i.created_at < ? ORDER BY random() LIMIT 6",
                        (now - 14 * 86400,),
                    )
                ],
                "activity": grouped(
                    "SELECT e.at, e.what, e.detail, e.item_id, i.title, i.url, i.source FROM events e "
                    "LEFT JOIN items i ON i.id = e.item_id ORDER BY e.at DESC, e.id DESC LIMIT 25"
                ),
                "upcoming": self.dated_open(12),
                "first": db.execute("SELECT MIN(created_at) FROM items").fetchone()[0],
                "sorted": dict(db.execute("SELECT sorted_by, COUNT(*) FROM items GROUP BY sorted_by").fetchall()),
                "failed": db.execute("SELECT COUNT(*) FROM items WHERE fetch_state IN ('failed', 'dead')").fetchone()[
                    0
                ],
            }
        return result

    def dated_open(self, limit: int = 500) -> list[dict]:
        """Open items with a date, the first date first (overdue ones at the top)."""
        with self._lock:
            rows = self._db.execute(
                f"SELECT {CARD_COLUMNS}, {NEXT_DATE} AS next_date FROM items i "
                "WHERE i.status IN ('inbox', 'later') AND (i.due != '' OR i.happens_on != '') "
                f"ORDER BY {NEXT_DATE}, i.id LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row(r) for r in rows]

    def history(self, limit: int = 200, before: float | None = None) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT e.id, e.at, e.what, e.detail, e.item_id, i.title, i.url, i.source, i.image FROM events e "
                "LEFT JOIN items i ON i.id = e.item_id WHERE e.at < ? ORDER BY e.at DESC, e.id DESC LIMIT ?",
                (before or time.time() + 1, max(1, min(limit, 1000))),
            ).fetchall()
        return [dict(r) for r in rows]

    def size(self) -> int:
        try:
            return sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*"))
        except OSError:
            return 0
