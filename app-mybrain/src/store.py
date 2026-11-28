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
EDITABLE = {
    "title",
    "note",
    "category",
    "tags",
    "purpose",
    "priority",
    "status",
    "summary",
    "due",
    "happens_on",
    "private",
}

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
    owner TEXT NOT NULL DEFAULT '',
    private INTEGER NOT NULL DEFAULT 0,
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
CREATE INDEX IF NOT EXISTS items_owner ON items(owner, status);

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
    detail TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_at ON events(at);
CREATE INDEX IF NOT EXISTS events_item ON events(item_id);
CREATE INDEX IF NOT EXISTS events_owner ON events(owner, at);
"""

# What the list and the cards need; the page text stays in the database until an item is opened
CARD_COLUMNS = (
    "i.id, i.kind, i.url, i.title, i.description, i.summary, i.note, i.source, i.site, i.detail, i.author, "
    "i.page_kind, i.image, i.category, i.tags, i.purpose, i.priority, i.status, i.sorted_by, i.fetch_state, "
    "i.fetch_error, i.via, i.shares, i.due, i.happens_on, i.owner, i.private, i.created_at, i.updated_at, i.opened_at, i.done_at"
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

    # ------------------------------------------------------------------
    # Who sees what. An item belongs to the Home Assistant user who saved it (owner); '' is nobody's,
    # for an app that doesn't know its users, and is everybody's. Public items can be browsed by all,
    # private ones only by their owner. Only the owner changes an item.
    # ------------------------------------------------------------------
    @staticmethod
    def scope(viewer: str, whose: str = "mine") -> tuple[str, list]:
        """SQL (on columns of items, no alias) for the items a viewer looks at: mine, everyone or user:<id>."""
        if whose == "everyone":
            return "(owner = ? OR owner = '' OR private = 0)", [viewer]
        if whose.startswith("user:"):
            other = whose[5:]
            if other == viewer:
                return "(owner = ?)", [viewer]
            return "(owner = ? AND private = 0)", [other]
        return "(owner = ? OR owner = '')", [viewer]

    @staticmethod
    def visible(item: dict, viewer: str) -> bool:
        return not item.get("private") or item.get("owner") in ("", viewer)

    @staticmethod
    def owns(item: dict, viewer: str) -> bool:
        return item.get("owner") in ("", viewer)

    def log(self, item_id: int | None, what: str, detail: str = "", at: float | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO events(item_id, at, what, detail, owner) "
                "VALUES (?, ?, ?, ?, COALESCE((SELECT owner FROM items WHERE id = ?), ''))",
                (item_id, at or time.time(), what, detail[:300], item_id),
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
                "INSERT INTO events(item_id, at, what, detail, owner) VALUES (?, ?, 'saved', ?, ?)",
                (item_id, values["created_at"], values.get("via", ""), values.get("owner", "")),
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

    def by_url_key(self, key: str, owner: str = "", other_than: int | None = None) -> dict | None:
        """The owner's item with this link, if they saved it before (someone else's doesn't count)."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM items WHERE url_key = ? AND owner = ? AND id != ? ORDER BY id LIMIT 1",
                (key, owner, other_than or -1),
            ).fetchone()
        return self._row(row)

    def delete(self, item_id: int) -> dict | None:
        with self._lock:
            item = self.get(item_id)
            if item:
                self._db.execute("DELETE FROM items WHERE id = ?", (item_id,))
                self._db.execute(
                    "INSERT INTO events(item_id, at, what, detail, owner) VALUES (NULL, ?, 'deleted', ?, ?)",
                    (time.time(), (item.get("title") or item.get("url") or "")[:300], item.get("owner") or ""),
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

    def known_tags(self, owner: str = "", limit: int = 80) -> list[str]:
        """The owner's tags, most used first (what the AI is asked to reuse)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT j.value AS tag, COUNT(*) AS n FROM items, json_each(items.tags) AS j "
                "WHERE items.owner = ? GROUP BY j.value ORDER BY n DESC, tag LIMIT ?",
                (owner, limit),
            ).fetchall()
        return [r["tag"] for r in rows]

    def owners(self) -> list[str]:
        """Everyone who has items."""
        with self._lock:
            rows = self._db.execute("SELECT DISTINCT owner FROM items").fetchall()
        return sorted({r[0] or "" for r in rows})

    def household(self, viewer: str) -> list[dict]:
        """Per owner: how many items the viewer may browse."""
        with self._lock:
            rows = self._db.execute(
                "SELECT owner, COUNT(*) AS n FROM items WHERE owner != '' AND (private = 0 OR owner = ?) "
                "GROUP BY owner ORDER BY n DESC",
                (viewer,),
            ).fetchall()
        return [{"owner": r[0], "n": r[1]} for r in rows]

    # ------------------------------------------------------------------
    def search(
        self,
        viewer: str = "",
        whose: str = "mine",
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
        sql, scope_args = self.scope(viewer, whose)
        where, args = [sql.replace("owner", "i.owner").replace("private", "i.private")], list(scope_args)
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
        clause = f"WHERE {' AND '.join(where)}"
        order = SORTS.get(sort, SORTS["new"])
        with self._lock:
            total = self._db.execute(f"SELECT COUNT(*) FROM items i {clause}", args).fetchone()[0]
            rows = self._db.execute(
                f"SELECT {CARD_COLUMNS} FROM items i {clause} ORDER BY {order} LIMIT ? OFFSET ?",
                [*args, max(1, min(limit, 500)), max(0, offset)],
            ).fetchall()
            facets = {}
            base = f"WHERE {' AND '.join(base_where)}"
            for column in ("status", "category", "source", "purpose"):
                facets[column] = {
                    r[0]: r[1]
                    for r in self._db.execute(
                        f"SELECT i.{column}, COUNT(*) FROM items i {base} GROUP BY i.{column}", base_args
                    )
                }
        return {"total": total, "items": [self._row(r) for r in rows], "facets": facets}

    def counts(self, owner: str | None = None) -> dict:
        """Items per status: of one owner (and nobody's), or of everyone with None."""
        where, args = ("WHERE owner = ? OR owner = ''", [owner]) if owner is not None else ("", [])
        with self._lock:
            by_status = dict(
                self._db.execute(f"SELECT status, COUNT(*) FROM items {where} GROUP BY status", args).fetchall()
            )
            waiting = self._db.execute(
                f"SELECT COUNT(*) FROM items {where} {'AND' if where else 'WHERE'} "
                "(fetch_state = 'waiting' OR sorted_by = '')",
                args,
            ).fetchone()[0]
        return {
            "total": sum(by_status.values()),
            "by_status": {s: by_status.get(s, 0) for s in STATUSES},
            "waiting": waiting,
        }

    def stats(self, days: int = 90, viewer: str = "") -> dict:
        """Everything the overview shows, about the viewer's own items."""
        now = time.time()
        start = now - days * 86400
        mine = "(SELECT * FROM items WHERE owner = ? OR owner = '')"
        my_events = "(SELECT * FROM events WHERE owner = ? OR owner = '')"
        v = viewer
        with self._lock:
            db = self._db

            def grouped(sql: str, *args: Any) -> list[dict]:
                return [dict(r) for r in db.execute(sql, args)]

            def cards(sql: str, *args: Any) -> list[dict]:
                return [self._row(r) for r in db.execute(sql, args)]

            open_ = "status IN ('inbox', 'later')"
            week = now - 7 * 86400
            result = {
                "days": days,
                "counts": self.counts(viewer),
                "per_day": {
                    "saved": grouped(
                        f"SELECT date(created_at, 'unixepoch', 'localtime') AS day, COUNT(*) AS n FROM {mine} "
                        "WHERE created_at >= ? GROUP BY day",
                        v,
                        start,
                    ),
                    "done": grouped(
                        f"SELECT date(at, 'unixepoch', 'localtime') AS day, COUNT(*) AS n FROM {my_events} "
                        "WHERE at >= ? AND what IN ('done', 'archived') GROUP BY day",
                        v,
                        start,
                    ),
                },
                "categories": grouped(
                    f"SELECT category AS name, COUNT(*) AS n, SUM({open_}) AS open FROM {mine} "
                    "GROUP BY category ORDER BY n DESC",
                    v,
                ),
                "sources": grouped(
                    f"SELECT source AS name, COUNT(*) AS n FROM {mine} GROUP BY source ORDER BY n DESC", v
                ),
                "purposes": grouped(
                    f"SELECT purpose AS name, COUNT(*) AS n FROM {mine} WHERE {open_} GROUP BY purpose ORDER BY n DESC",
                    v,
                ),
                "tags": grouped(
                    f"SELECT j.value AS name, COUNT(*) AS n FROM {mine} AS m, json_each(m.tags) AS j "
                    "GROUP BY j.value ORDER BY n DESC, name LIMIT 40",
                    v,
                ),
                "week": dict(
                    db.execute(
                        f"SELECT (SELECT COUNT(*) FROM {mine} WHERE created_at >= ?) AS saved, "
                        f"(SELECT COUNT(*) FROM {my_events} WHERE at >= ? AND what IN ('done', 'archived')) AS handled, "
                        f"(SELECT COUNT(*) FROM {my_events} WHERE at >= ? AND what = 'shared again') AS again",
                        (v, week, v, week, v, week),
                    ).fetchone()
                ),
                "inbox_age": dict(
                    db.execute(
                        "SELECT SUM(created_at >= ?) AS week, SUM(created_at < ? AND created_at >= ?) AS month, "
                        "SUM(created_at < ? AND created_at >= ?) AS quarter, SUM(created_at < ?) AS older "
                        f"FROM {mine} WHERE status = 'inbox'",
                        (week, week, now - 30 * 86400, now - 30 * 86400, now - 90 * 86400, now - 90 * 86400, v),
                    ).fetchone()
                ),
                "high": cards(
                    f"SELECT {CARD_COLUMNS} FROM {mine} i WHERE i.{open_} AND i.priority = 2 "
                    "ORDER BY i.created_at DESC LIMIT 6",
                    v,
                ),
                "recent": cards(f"SELECT {CARD_COLUMNS} FROM {mine} i ORDER BY i.created_at DESC LIMIT 8", v),
                # Saved a while ago, never opened, still open: what is being forgotten
                "forgotten": cards(
                    f"SELECT {CARD_COLUMNS} FROM {mine} i WHERE i.{open_} AND i.opened_at IS NULL "
                    "AND i.created_at < ? ORDER BY random() LIMIT 6",
                    v,
                    now - 14 * 86400,
                ),
                "activity": grouped(
                    f"SELECT e.at, e.what, e.detail, e.item_id, i.title, i.url, i.source FROM {my_events} e "
                    "LEFT JOIN items i ON i.id = e.item_id ORDER BY e.at DESC, e.id DESC LIMIT 25",
                    v,
                ),
                "upcoming": self.dated_open(owner=viewer, limit=12),
                "first": db.execute(f"SELECT MIN(created_at) FROM {mine}", (v,)).fetchone()[0],
                "sorted": dict(
                    db.execute(f"SELECT sorted_by, COUNT(*) FROM {mine} GROUP BY sorted_by", (v,)).fetchall()
                ),
                "household": self.household(viewer),
            }
        return result

    def dated_open(self, owner: str | None = None, public_only: bool = False, limit: int = 500) -> list[dict]:
        """Open items with a date, the first date first (overdue ones at the top).

        owner: only that owner's (and nobody's); public_only: only items that aren't private.
        """
        where, args = ["i.status IN ('inbox', 'later')", "(i.due != '' OR i.happens_on != '')"], []
        if owner is not None:
            where.append("(i.owner = ? OR i.owner = '')")
            args.append(owner)
        if public_only:
            where.append("i.private = 0")
        with self._lock:
            rows = self._db.execute(
                f"SELECT {CARD_COLUMNS}, {NEXT_DATE} AS next_date FROM items i WHERE {' AND '.join(where)} "
                f"ORDER BY {NEXT_DATE}, i.id LIMIT ?",
                (*args, limit),
            ).fetchall()
        return [self._row(r) for r in rows]

    def history(self, viewer: str = "", limit: int = 200, before: float | None = None) -> list[dict]:
        """What happened to the viewer's own items, newest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT e.id, e.at, e.what, e.detail, e.item_id, i.title, i.url, i.source, i.image FROM events e "
                "LEFT JOIN items i ON i.id = e.item_id WHERE (e.owner = ? OR e.owner = '') AND e.at < ? "
                "ORDER BY e.at DESC, e.id DESC LIMIT ?",
                (viewer, before or time.time() + 1, max(1, min(limit, 1000))),
            ).fetchall()
        return [dict(r) for r in rows]

    def size(self) -> int:
        try:
            return sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*"))
        except OSError:
            return 0
