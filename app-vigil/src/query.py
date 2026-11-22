"""The log search language, turned into a parameterised SQL condition on vigil.logs.

    disk full                 both words (AND), anywhere in host, app or message
    "connection refused"      the exact phrase in the message
    -debug                    without the word (any term can be negated)
    host:sw-office            a field: host, app, source (exact; sw-* matches the start)
    severity:err              one severity; severity:<=err (or <=3) is err and worse,
                              severity:>=info (or >=6) is info and debug
    pattern:42                lines of one log pattern
    attrs.user:bob            a value in the attributes

A severity name stands for its syslog number (emerg 0 .. debug 7), and comparisons are on that
number, so "worse" is smaller: severity:<=warning is warning, err, crit, alert and emerg.

Values never become part of the SQL text: every one is a $n parameter. Column names come from
the fixed FIELDS table.
"""

import re
from dataclasses import dataclass, field

from .records import severity_number

FIELDS = {"host": "host", "app": "app", "source": "source"}
SEVERITY_FIELDS = ("severity", "sev", "level")
ATTR_KEY = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
# Words the full-text index keeps whole; anything else (dots, slashes, dashes) is matched as text
PLAIN_WORD = re.compile(r"^\w+$")
PREFIX_WORD = re.compile(r"^\w+\*$")
MAX_TERMS = 30


class QueryError(ValueError):
    """The query can't be understood (the message says why)."""


@dataclass
class Term:
    negated: bool
    field: str | None  # None: free text
    value: str
    phrase: bool = False
    op: str | None = None  # severity comparisons


@dataclass
class Where:
    """SQL conditions and their parameters, numbered from the first free $n."""

    params: list = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    start: int = 1

    def param(self, value) -> str:
        self.params.append(value)
        return f"${self.start + len(self.params) - 1}"

    def add(self, condition: str) -> None:
        self.conditions.append(condition)

    @property
    def sql(self) -> str:
        return " AND ".join(self.conditions) if self.conditions else "TRUE"


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def tokenize(text: str) -> list[tuple[bool, str, bool]]:
    """(negated, text, quoted) for each term; a quote may also follow field: (app:"my app")."""
    terms: list[tuple[bool, str, bool]] = []
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        negated = False
        if text[i] == "-" and i + 1 < n and not text[i + 1].isspace():
            negated = True
            i += 1
        if text[i] == '"':
            end = text.find('"', i + 1)
            end = n if end < 0 else end
            terms.append((negated, text[i + 1 : end], True))
            i = end + 1
            continue
        start = i
        while i < n and not text[i].isspace():
            if text[i] == '"' and i > start and text[i - 1] == ":":
                # field:"quoted value"
                end = text.find('"', i + 1)
                end = n if end < 0 else end
                terms.append((negated, text[start:i] + text[i + 1 : end], True))
                i = end + 1
                break
            i += 1
        else:
            terms.append((negated, text[start:i], False))
    return [t for t in terms if t[1]]


def parse(text: str) -> list[Term]:
    terms = []
    for negated, raw, quoted in tokenize(text or ""):
        name, sep, value = raw.partition(":")
        key = name.lower()
        if sep and value and (key in FIELDS or key in SEVERITY_FIELDS or key == "pattern" or key.startswith("attrs.")):
            op = None
            if key in SEVERITY_FIELDS:
                key = "severity"
                for candidate in (">=", "<=", ">", "<", "="):
                    if value.startswith(candidate):
                        op, value = candidate, value[len(candidate) :]
                        break
            terms.append(Term(negated=negated, field=key, value=value, phrase=quoted, op=op))
        else:
            terms.append(Term(negated=negated, field=None, value=raw, phrase=quoted))
    if len(terms) > MAX_TERMS:
        raise QueryError(f"Too many terms (at most {MAX_TERMS})")
    return terms


def _severity_condition(term: Term, where: Where) -> str:
    level = severity_number(term.value)
    if level is None:
        raise QueryError(f"Unknown severity {term.value!r}; use a name (err, warning, info) or 0..7")
    # The op comes from the fixed list in parse(), never from the text itself
    return f"severity {term.op or '='} {where.param(level)}"


def _field_condition(column: str, term: Term, where: Where) -> str:
    value = term.value
    if value.endswith("*") and not term.phrase:
        return f"{column} ILIKE {where.param(_like_escape(value[:-1]) + '%')}"
    return f"{column} = {where.param(value)}"


def _text_condition(term: Term, where: Where) -> str:
    value = term.value
    if term.phrase:
        # The index narrows it down, the ILIKE makes it the exact phrase
        return (
            f"(search @@ phraseto_tsquery('simple', {where.param(value)})"
            f" AND message ILIKE {where.param('%' + _like_escape(value) + '%')})"
        )
    if PLAIN_WORD.match(value):
        return f"search @@ plainto_tsquery('simple', {where.param(value)})"
    if PREFIX_WORD.match(value):
        # Only word characters reach to_tsquery, so its own syntax can't be injected
        return f"search @@ to_tsquery('simple', {where.param(value[:-1].lower() + ':*')})"
    return f"message ILIKE {where.param('%' + _like_escape(value) + '%')}"


def condition(term: Term, where: Where) -> str:
    if term.field is None:
        sql = _text_condition(term, where)
    elif term.field == "severity":
        sql = _severity_condition(term, where)
    elif term.field == "pattern":
        if not term.value.isdigit():
            raise QueryError("pattern: needs a pattern number")
        sql = f"pattern = {where.param(int(term.value))}"
    elif term.field.startswith("attrs."):
        key = term.field[len("attrs.") :]
        if not ATTR_KEY.match(key):
            raise QueryError(f"Invalid attribute name {key!r}")
        sql = _field_condition(f"(attrs ->> {where.param(key)})", term, where)
    else:
        sql = _field_condition(FIELDS[term.field], term, where)
    # NOT on a NULL host is NULL, which would hide the line; a missing field is "not this value"
    return f"NOT COALESCE({sql}, FALSE)" if term.negated else sql


def to_where(text: str, start: int = 1) -> Where:
    """The condition for a query, its parameters numbered from $start."""
    where = Where(start=start)
    for term in parse(text):
        where.add(condition(term, where))
    return where
