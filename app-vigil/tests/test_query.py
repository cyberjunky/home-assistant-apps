"""The log search language, turned into SQL conditions and parameters."""

import pytest

from src.query import QueryError, parse, to_where


def test_empty_query_matches_everything():
    where = to_where("")
    assert (where.sql, where.params) == ("TRUE", [])


def test_words_and_phrases():
    where = to_where('disk full "connection refused"')
    assert where.sql == (
        "search @@ plainto_tsquery('simple', $1) AND search @@ plainto_tsquery('simple', $2)"
        " AND (search @@ phraseto_tsquery('simple', $3) AND message ILIKE $4)"
    )
    assert where.params == ["disk", "full", "connection refused", "%connection refused%"]


def test_not_and_fields():
    where = to_where("-debug host:sw-* app:sshd source:syslog", start=3)
    assert where.sql == (
        "NOT COALESCE(search @@ plainto_tsquery('simple', $3), FALSE) AND host ILIKE $4 AND app = $5 AND source = $6"
    )
    assert where.params == ["debug", "sw-%", "sshd", "syslog"]


def test_severity_compares_syslog_numbers():
    # A name is its number: <=err is err and worse (0..3), the same as <=3
    assert (to_where("severity:<=err").sql, to_where("severity:<=err").params) == ("severity <= $1", [3])
    assert to_where("severity:<=3").params == [3]
    assert (to_where("severity:>=warning").sql, to_where("severity:>=warning").params) == ("severity >= $1", [4])
    assert to_where("level:err").sql == "severity = $1"
    assert to_where("sev:WARN").params == [4]
    assert to_where("severity:>info").sql == "severity > $1"
    with pytest.raises(QueryError):
        to_where("severity:loud")


def test_pattern_and_attributes():
    where = to_where("pattern:42 attrs.user:bob attrs.path:/var/*")
    assert where.sql == "pattern = $1 AND (attrs ->> $2) = $3 AND (attrs ->> $4) ILIKE $5"
    assert where.params == [42, "user", "bob", "path", "/var/%"]
    with pytest.raises(QueryError):
        to_where("pattern:abc")


def test_values_never_reach_the_sql_text():
    nasty = "x'; DROP TABLE vigil.logs; --"
    for query in (nasty, f'"{nasty}"', f"host:{nasty}", f"attrs.a:{nasty}", "attrs.a';--:1"):
        try:
            where = to_where(query)
        except QueryError:
            continue
        assert "DROP" not in where.sql and "'" not in where.sql.replace("'simple'", "")


def test_like_wildcards_are_escaped_and_punctuation_uses_ilike():
    where = to_where("100%_done 10.0.0.1")
    assert where.sql == "message ILIKE $1 AND message ILIKE $2"
    assert where.params == ["%100\\%\\_done%", "%10.0.0.1%"]


def test_prefix_word():
    where = to_where("conn*")
    assert where.sql == "search @@ to_tsquery('simple', $1)" and where.params == ["conn:*"]


def test_quoted_field_value_and_unknown_field():
    terms = parse('app:"my app" foo:bar -"a b"')
    assert (terms[0].field, terms[0].value) == ("app", "my app")
    assert (terms[1].field, terms[1].value) == (None, "foo:bar")
    assert terms[2].negated and terms[2].phrase and terms[2].value == "a b"


def test_unterminated_quote_takes_the_rest():
    terms = parse('"no end')
    assert terms[0].phrase and terms[0].value == "no end"


def test_too_many_terms():
    with pytest.raises(QueryError):
        to_where(" ".join(f"w{i}" for i in range(40)))


def test_metric_search_labels():
    from src.webui import metric_search

    assert metric_search("job=freqle") == ("", {"job": "freqle"})
    assert metric_search("job=freqle  cpu seconds") == ("cpu seconds", {"job": "freqle"})
    assert metric_search("node:cpu:rate5m") == ("node:cpu:rate5m", {})  # Prometheus names may hold colons
    assert metric_search("=x a= ") == ("=x a=", {})
