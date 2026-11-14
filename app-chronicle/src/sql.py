"""The SQL page: scripts run like in pgAdmin, in a session that keeps its connection.

Each open page has a session with its own connection to the database it picked, so a
transaction stays open between runs (BEGIN in a script, or auto-commit switched off) until
Commit or Rollback. The connection is the web UI's role, chronicle_ui: it can read and change
the data and the tables, but it is no superuser (no server files, no programs, no other roles).

A script is split into statements. Statements that change something (anything but SELECT,
SHOW, EXPLAIN, transaction control and SET) are listed first, and the page asks before it
runs them: a guard against typos, not a permission (a SELECT can call a function that writes).
"""

import asyncio
import contextlib
import logging
import re
import secrets
import time
from dataclasses import dataclass, field

import asyncpg

from .admin import UI_SESSION
from .config import Config
from .database import _cell

logger = logging.getLogger(__name__)

RESULT_ROWS = 10_000  # rows returned per result at most
MAX_SESSIONS = 8
IDLE_SECONDS = 15 * 60  # a session nobody used for this long is closed (an open transaction rolled back)
TIMEOUTS = (30, 60, 300, 1800, 3600)  # what the page offers, in seconds
MAX_STATEMENTS = 500

# The first word of statements that only read, or only change the session
READ_WORDS = {"SELECT", "SHOW", "TABLE", "VALUES", "EXPLAIN", "WITH", "FETCH"}
SESSION_WORDS = {
    "BEGIN",
    "START",
    "COMMIT",
    "END",
    "ROLLBACK",
    "ABORT",
    "SAVEPOINT",
    "RELEASE",
    "SET",
    "RESET",
    "DISCARD",
    "PREPARE",
    "DEALLOCATE",
    "LISTEN",
    "UNLISTEN",
    "DECLARE",
    "CLOSE",
    "MOVE",
}
# Words that make a WITH, EXPLAIN ANALYZE or SELECT INTO change data
WRITE_WORDS = {"INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE"}

_DOLLAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


class SessionError(Exception):
    """Something the page did wrong or can't do now; the message is for the page."""


@dataclass
class Statement:
    text: str
    start: int  # offset in the script, for error positions


def split_statements(script: str) -> list[Statement]:
    """Splits a script at the semicolons that end statements: not those in strings, quoted
    names, dollar quotes ($$ .. $$, $body$ .. $body$) or comments (-- and nested /* */)."""
    statements: list[Statement] = []
    i, start, n = 0, 0, len(script)
    while i < n:
        c = script[i]
        if c == "-" and script.startswith("--", i):
            end = script.find("\n", i)
            i = n if end == -1 else end + 1
        elif c == "/" and script.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if script.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif script.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
        elif c == "'":
            # E'..' strings may escape a quote with a backslash
            escapes = (
                i > 0 and script[i - 1] in "eE" and (i < 2 or not (script[i - 2].isalnum() or script[i - 2] == "_"))
            )
            i += 1
            while i < n:
                if escapes and script[i] == "\\":
                    i += 2
                elif script[i] == "'":
                    if script.startswith("''", i):
                        i += 2
                    else:
                        i += 1
                        break
                else:
                    i += 1
        elif c == '"':
            end = script.find('"', i + 1)
            while end != -1 and script.startswith('""', end):
                end = script.find('"', end + 2)
            i = n if end == -1 else end + 1
        elif c == "$" and (i == 0 or not (script[i - 1].isalnum() or script[i - 1] == "_")):
            match = _DOLLAR.match(script, i)
            if match:
                end = script.find(match.group(0), match.end())
                i = n if end == -1 else end + len(match.group(0))
            else:
                i += 1
        elif c == ";":
            _add(statements, script, start, i)
            i += 1
            start = i
        else:
            i += 1
    _add(statements, script, start, n)
    return statements


def _add(statements: list[Statement], script: str, start: int, end: int) -> None:
    text = script[start:end]
    if _words(text):
        lead = len(text) - len(text.lstrip())
        statements.append(Statement(text.strip(), start + lead))


def _words(text: str) -> list[str]:
    """The keywords and names of a statement, without strings, quoted names and comments."""
    plain = []
    for part in split_parts(text):
        if part[0] == "code":
            plain.append(part[1])
    return [w.upper() for w in _WORD.findall(" ".join(plain))]


def split_parts(text: str) -> list[tuple[str, str]]:
    """The statement as ("code", ..) and ("quoted", ..) pieces."""
    parts: list[tuple[str, str]] = []
    i, start, n = 0, 0, len(text)

    def flush(kind: str, a: int, b: int) -> None:
        if b > a:
            parts.append((kind, text[a:b]))

    while i < n:
        c = text[i]
        if c == "-" and text.startswith("--", i):
            flush("code", start, i)
            end = text.find("\n", i)
            i = n if end == -1 else end + 1
            start = i
        elif c == "/" and text.startswith("/*", i):
            flush("code", start, i)
            depth, i = 1, i + 2
            while i < n and depth:
                if text.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif text.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            start = i
        elif c in "'\"":
            flush("code", start, i)
            quote, j = c, i + 1
            while j < n:
                if text[j] == quote:
                    if text.startswith(quote * 2, j):
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            flush("quoted", i, j)
            i = start = j
        elif c == "$" and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")):
            match = _DOLLAR.match(text, i)
            if match:
                flush("code", start, i)
                end = text.find(match.group(0), match.end())
                j = n if end == -1 else end + len(match.group(0))
                flush("quoted", i, j)
                i = start = j
            else:
                i += 1
        else:
            i += 1
    flush("code", start, n)
    return parts


def changes(statement: str) -> str | None:
    """What a statement changes, by its leading words (DROP TABLE, DELETE, ..); None when it
    only reads or changes the session."""
    words = _words(statement)
    if not words:
        return None
    first = words[0]
    if first in SESSION_WORDS:
        return None
    if first in READ_WORDS:
        if first == "EXPLAIN" and "ANALYZE" not in words[:4] and "ANALYSE" not in words[:4]:
            return None
        writes = [w for w in words[1:] if w in WRITE_WORDS]
        if writes:
            return writes[0]
        if first == "SELECT" and "INTO" in words:
            return "SELECT INTO"
        return None
    if first in ("CREATE", "DROP", "ALTER", "COMMENT", "SECURITY", "IMPORT") and len(words) > 1:
        second = words[1]
        if second in ("OR", "UNIQUE", "TEMP", "TEMPORARY", "UNLOGGED", "MATERIALIZED", "FOREIGN") and len(words) > 2:
            return f"{first} {second} {words[2]}"
        return f"{first} {second}"
    return first


@dataclass
class Session:
    id: str
    database: str
    conn: asyncpg.Connection
    pid: int
    last_used: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    notices: list[str] = field(default_factory=list)


class SqlSessions:
    """The open sessions of the SQL page."""

    def __init__(self, config: Config):
        self.config = config
        self.sessions: dict[str, Session] = {}
        self._sweeper: asyncio.Task | None = None

    async def _connect(self, database: str) -> asyncpg.Connection:
        return await asyncpg.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.username,
            password=self.config.password or None,
            database=database,
            timeout=10,
            # The page's SQL is not cached as prepared statements
            statement_cache_size=0,
            server_settings={**UI_SESSION, "application_name": "chronicle-sql"},
        )

    async def open(self, database: str) -> Session:
        if len(self.sessions) >= MAX_SESSIONS:
            oldest = min(self.sessions.values(), key=lambda s: s.last_used)
            await self.close(oldest.id)
        conn = await self._connect(database)
        session = Session(secrets.token_urlsafe(16), database, conn, conn.get_server_pid())

        def notice(_conn, message):
            session.notices.append(f"{message.severity}: {message.message}")
            del session.notices[:-200]

        conn.add_log_listener(notice)
        self.sessions[session.id] = session
        if self._sweeper is None or self._sweeper.done():
            self._sweeper = asyncio.create_task(self._sweep(), name="sql-sweeper")
        return session

    def get(self, session_id: object) -> Session | None:
        if not isinstance(session_id, str):
            return None
        return self.sessions.get(session_id)

    async def close(self, session_id: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(session.conn.close(), 5)

    async def close_all(self) -> None:
        for session_id in list(self.sessions):
            await self.close(session_id)
        if self._sweeper:
            self._sweeper.cancel()

    async def _sweep(self) -> None:
        while self.sessions:
            await asyncio.sleep(60)
            now = time.monotonic()
            for session in list(self.sessions.values()):
                if now - session.last_used > IDLE_SECONDS and not session.lock.locked():
                    logger.info(f"Closing the idle SQL session on {session.database}")
                    await self.close(session.id)

    def state(self, session: Session) -> dict:
        conn = session.conn
        return {
            "session": session.id,
            "database": session.database,
            "pid": session.pid,
            "in_transaction": conn.is_in_transaction(),
        }

    async def run(
        self,
        session: Session | None,
        database: str,
        script: str,
        *,
        autocommit: bool,
        timeout: int,
        confirmed: bool,
    ) -> dict:
        statements = split_statements(script)
        if not statements:
            raise SessionError("Enter a statement")
        if len(statements) > MAX_STATEMENTS:
            raise SessionError(f"At most {MAX_STATEMENTS} statements at once")
        changing = [kind for kind in (changes(s.text) for s in statements) if kind]
        if changing and not confirmed:
            counts: dict[str, int] = {}
            for kind in changing:
                counts[kind] = counts.get(kind, 0) + 1
            return {"confirm": [{"kind": k, "count": c} for k, c in counts.items()]}
        if timeout not in TIMEOUTS:
            timeout = TIMEOUTS[0]

        if session is not None and session.database != database:
            if session.conn.is_in_transaction():
                raise SessionError(f"A transaction is open on {session.database}: commit or roll it back first")
            await self.close(session.id)
            session = None
        if session is None or session.conn.is_closed():
            session = await self.open(database)

        async with session.lock:
            session.last_used = time.monotonic()
            session.notices.clear()
            conn = session.conn
            results: list[dict] = []
            error = None
            started = time.monotonic()
            try:
                # A failed transaction takes only ROLLBACK (which the script may hold)
                with contextlib.suppress(asyncpg.InFailedSQLTransactionError):
                    await conn.execute(f"SET statement_timeout = '{int(timeout)}s'")
                if not autocommit and not conn.is_in_transaction():
                    await conn.execute("BEGIN")
                for index, statement in enumerate(statements):
                    try:
                        results.append(await self._run_one(conn, statement))
                    except asyncpg.PostgresError as e:
                        error = _postgres_error(e, statement, index)
                        break
            except (OSError, asyncpg.InterfaceError) as e:
                await self.close(session.id)
                raise SessionError(f"The connection was lost: {e}") from e
            finally:
                session.last_used = time.monotonic()
            return {
                **self.state(session),
                "results": results,
                "error": error,
                "notices": list(session.notices),
                "statements": len(statements),
                "duration_ms": round((time.monotonic() - started) * 1000, 1),
            }

    async def _run_one(self, conn: asyncpg.Connection, statement: Statement) -> dict:
        started = time.monotonic()
        sql = statement.text
        base = {"statement": sql[:300], "start": statement.start}
        words = _words(sql)
        if words and words[0] in SESSION_WORDS:
            # Transaction control and settings run as they are (also in a failed transaction)
            return {**base, "status": await conn.execute(sql), "duration_ms": _ms(started)}
        prepared = await conn.prepare(sql)
        attributes = prepared.get_attributes()
        if not attributes:
            status = await conn.execute(sql)
            return {**base, "status": status, "duration_ms": _ms(started)}
        columns = [{"name": a.name, "type": a.type.name} for a in attributes]
        if conn.is_in_transaction():
            rows = await _fetch(prepared)
        else:
            # A cursor needs a transaction; this one ends right after (auto-commit)
            async with conn.transaction():
                rows = await _fetch(prepared)
        truncated = len(rows) > RESULT_ROWS
        return {
            **base,
            "columns": columns,
            "rows": [[_cell(v) for v in row] for row in rows[:RESULT_ROWS]],
            "truncated": truncated,
            "limit": RESULT_ROWS,
            "status": f"{min(len(rows), RESULT_ROWS)}{'+' if truncated else ''} rows",
            "duration_ms": _ms(started),
        }

    async def end(self, session: Session, commit: bool) -> dict:
        async with session.lock:
            session.last_used = time.monotonic()
            if session.conn.is_in_transaction():
                await session.conn.execute("COMMIT" if commit else "ROLLBACK")
            return self.state(session)

    async def cancel(self, session: Session, pool: asyncpg.Pool) -> bool:
        """Stops the session's running statement (from another connection)."""
        return bool(await pool.fetchval("SELECT pg_cancel_backend($1)", session.pid))


async def _fetch(prepared: asyncpg.prepared_stmt.PreparedStatement) -> list:
    cursor = await prepared.cursor()
    return await cursor.fetch(RESULT_ROWS + 1)


def _ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 1)


def _postgres_error(e: asyncpg.PostgresError, statement: Statement, index: int) -> dict:
    """PostgreSQL's message, with the hint and the position in the whole script."""
    detail = {"message": str(e), "sqlstate": e.sqlstate, "statement": index, "start": statement.start}
    for key in ("detail", "hint"):
        if getattr(e, key, None):
            detail[key] = getattr(e, key)
    position = getattr(e, "position", None)
    if position:
        with contextlib.suppress(TypeError, ValueError):
            detail["position"] = statement.start + int(position)
    return detail
