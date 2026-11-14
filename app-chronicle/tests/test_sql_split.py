"""Splitting a script into statements, and what the page asks before running."""

import pytest

from src.sql import changes, split_statements


def texts(script):
    return [s.text for s in split_statements(script)]


def test_split_plain():
    assert texts("select 1; select 2;") == ["select 1", "select 2"]
    assert texts("select 1") == ["select 1"]
    assert texts("  ;; ; ") == []


def test_semicolons_inside_are_kept():
    script = """select ';' as a, 'it''s; ok', E'back\\'slash;';
select "odd;name" from t;
-- a comment; with a semicolon
select 1 /* block; /* nested; */ still */;
create function f() returns int language plpgsql as $$ begin return 1; end $$;
do $body$ begin perform 1; end $body$;
select $1"""
    parts = texts(script)
    assert len(parts) == 6
    assert parts[0].startswith("select ';'") and parts[0].endswith("E'back\\'slash;'")
    assert parts[2] == "-- a comment; with a semicolon\nselect 1 /* block; /* nested; */ still */"
    assert parts[3].endswith("end $$")
    assert parts[4].startswith("do $body$") and parts[4].endswith("$body$")
    assert parts[5] == "select $1"


def test_positions_point_into_the_script():
    script = "select 1;\n\n  select oops"
    second = split_statements(script)[1]
    assert script[second.start :].startswith("select oops")


@pytest.mark.parametrize(
    ("sql", "kind"),
    [
        ("SELECT * FROM chronicle.states", None),
        ("  select 1", None),
        ("SHOW work_mem", None),
        ("EXPLAIN SELECT 1", None),
        ("EXPLAIN DELETE FROM t", None),
        ("EXPLAIN ANALYZE DELETE FROM t", "DELETE"),
        ("WITH x AS (SELECT 1) SELECT * FROM x", None),
        ("WITH gone AS (DELETE FROM t RETURNING *) SELECT count(*) FROM gone", "DELETE"),
        ("SELECT * INTO copy FROM t", "SELECT INTO"),
        ("SELECT 'DELETE FROM t'", None),
        ("BEGIN", None),
        ("COMMIT", None),
        ("SET work_mem = '64MB'", None),
        ("DELETE FROM t", "DELETE"),
        ("update t set a = 1", "UPDATE"),
        ("DROP TABLE t", "DROP TABLE"),
        ("CREATE OR REPLACE VIEW v AS SELECT 1", "CREATE OR REPLACE"),
        ("CREATE UNIQUE INDEX i ON t (a)", "CREATE UNIQUE INDEX"),
        ("TRUNCATE t", "TRUNCATE"),
        ("VACUUM ANALYZE", "VACUUM"),
        ("CALL refresh_continuous_aggregate('x', NULL, NULL)", "CALL"),
        ("-- just a comment\nDROP SCHEMA s CASCADE", "DROP SCHEMA"),
    ],
)
def test_changes(sql, kind):
    assert changes(sql) == kind
