"""Tests for the SPARQL text: translation of the query, filters, and key validation."""

from __future__ import annotations

import pytest

from dblp_sparql_mcp.query import parse_query
from dblp_sparql_mcp.sparql import (
    UnboundedQueryError,
    candidate_query,
    details_query,
    literal,
    narrows,
    stream_iri,
    text_pattern,
    venue_query,
)


def _words(pattern: str) -> list[str]:
    """The contains-word strings of a pattern, in order."""
    return [part.split('"')[1] for part in pattern.split("ql:contains-word ")[1:]]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("agent", True),
        ('"prompt injection"', True),
        ("a NOT b", True),
        ("NOT a", False),
        ("NOT NOT a", False),
        ("a OR NOT b", False),
        ("(a OR b) AND NOT c", True),
        ("a AND (b OR NOT c)", True),  # the OR is skipped, `a` still narrows
        ("(a AND NOT b) OR c", True),
        ("(NOT a AND NOT b) OR c", False),
    ],
)
def test_narrows(query: str, expected: bool) -> None:
    assert narrows(parse_query(query)) is expected


@pytest.mark.parametrize("query", ["NOT survey", "agent* OR NOT survey", "NOT (a OR b)"])
def test_unbounded_queries_are_rejected(query: str) -> None:
    with pytest.raises(UnboundedQueryError, match="outside NOT"):
        text_pattern(parse_query(query))


def test_every_word_becomes_a_prefix() -> None:
    # Exact words are accent-sensitive on the server, prefixes are not (probe A4).
    assert _words(text_pattern(parse_query("Rényi"))) == ["renyi*"]
    assert _words(text_pattern(parse_query('"prompt injection"'))) == ["prompt* injection*"]
    assert _words(text_pattern(parse_query("agent*"))) == ["agent*"]


def test_punctuation_is_split_like_normalize() -> None:
    # The server returns nothing for "privacy-preserving" or "x.509" (probe A2).
    assert _words(text_pattern(parse_query("privacy-preserving X.509"))) == [
        "privacy* preserving* x* 509*"
    ]


def test_and_merges_terms_into_one_block() -> None:
    pattern = text_pattern(parse_query('"prompt injection" agent* NOT survey'))
    assert _words(pattern) == ["prompt* injection* agent*"]
    assert "survey" not in pattern
    assert "UNION" not in pattern


def test_or_becomes_union_and_groups_join_on_title() -> None:
    pattern = text_pattern(parse_query('("prompt injection" OR jailbreak*) AND agent*'))
    assert _words(pattern) == ["agent*", "prompt* injection*", "jailbreak*"]
    assert pattern.count("UNION") == 1
    # Each text block has its own text variable, all bound to the same ?title.
    assert pattern.count("ql:contains-entity ?title") == 3
    assert {"?t1", "?t2", "?t3"} <= set(pattern.replace(".", " ").split())


def test_duplicate_words_are_sent_once() -> None:
    assert _words(text_pattern(parse_query("agent agent*"))) == ["agent*"]


def test_candidate_query_filters() -> None:
    q = candidate_query(
        parse_query("agent*"),
        streams=[stream_iri("conf/uss")],
        years=[2024, 2025],
        types=["conference", "journal"],
        limit=25001,
    )
    assert 'VALUES ?y { "2024"^^xsd:gYear "2025"^^xsd:gYear }' in q
    assert "FILTER(STR(?y)" not in q  # timed out on large candidate sets (probe E1)
    assert "VALUES ?stream { <https://dblp.org/streams/conf/uss> }" in q
    assert "VALUES ?type { dblp:Inproceedings dblp:Article }" in q
    assert "SELECT DISTINCT ?pub ?title ?type ?y" in q
    assert q.rstrip().endswith("LIMIT 25001")
    assert "OPTIONAL { ?pub dblp:yearOfPublication ?y }" not in q


def test_listing_without_years_keeps_the_year_optional() -> None:
    q = candidate_query(
        None, streams=[stream_iri("conf/ccs")], years=[], types=["preprint"], limit=10
    )
    assert "ql:contains-word" not in q
    assert "OPTIONAL { ?pub dblp:yearOfPublication ?y }" in q
    assert "dblp:Informal" in q


def test_details_query_lists_the_records() -> None:
    q = details_query(["https://dblp.org/rec/conf/uss/LiuJGJG24"])
    assert "VALUES ?pub { <https://dblp.org/rec/conf/uss/LiuJGJG24> }" in q
    assert "dblp:AuthorSignature" in q
    with pytest.raises(ValueError, match="IRI"):
        details_query(["https://dblp.org/rec/x> } DROP"])


@pytest.mark.parametrize(
    ("venue", "expected"),
    [
        ("conf/uss", "https://dblp.org/streams/conf/uss"),
        (" journals/tifs ", "https://dblp.org/streams/journals/tifs"),
        ("https://dblp.org/streams/conf/iccS", "https://dblp.org/streams/conf/iccS"),
    ],
)
def test_stream_keys(venue: str, expected: str) -> None:
    assert stream_iri(venue) == expected


@pytest.mark.parametrize("venue", ["USENIX Security", "uss", "conf/uss>", "conf/", "/uss"])
def test_bad_stream_keys(venue: str) -> None:
    with pytest.raises(ValueError, match="dblp_find_venue"):
        stream_iri(venue)


def test_literal_escapes() -> None:
    assert literal('a"b\\c\nd') == '"a\\"b\\\\c\\nd"'


def test_venue_query_escapes_the_name() -> None:
    q = venue_query('S&P" ) }')
    assert '"s&p\\" ) }"' in q
