"""Tests for the boolean query language: parsing, canonical form, and matching."""

from __future__ import annotations

import pytest

from dblp_sparql_mcp.query import (
    QuerySyntaxError,
    SearchIndex,
    normalize,
    parse_query,
    to_string,
)


@pytest.mark.parametrize(
    ("query", "canonical"),
    [
        ("prompt injection", "prompt AND injection"),
        ('"prompt injection"', '"prompt injection"'),
        ("a OR b AND c", "a OR (b AND c)"),
        ("(a OR b) AND c", "(a OR b) AND c"),
        ("(a OR b) (c OR d)", "(a OR b) AND (c OR d)"),
        ("a NOT b", "a AND NOT b"),
        ("NOT a", "NOT a"),
        ("NOT NOT a", "NOT NOT a"),
        ("a and b or not c", "(a AND b) OR NOT c"),
        ("(a AND b) AND c", "a AND b AND c"),
        ("a OR (b OR c)", "a OR b OR c"),
        ("NOT (a OR b)", "NOT (a OR b)"),
        ("inject*", "inject*"),
        ('"prompt inject*"', '"prompt inject*"'),
        ("self-attention", '"self attention"'),
        ("Self-Attent*", '"self attent*"'),
        ("“prompt injection” agent*", '"prompt injection" AND agent*'),
        ("Naïve", "naive"),
        ('"trial and error"', '"trial and error"'),
        ("GPT-4o", '"gpt 4o"'),
    ],
)
def test_canonical_form(query: str, canonical: str) -> None:
    assert to_string(parse_query(query)) == canonical


@pytest.mark.parametrize(
    "query",
    [
        "",
        "   ",
        "(a",
        "a)",
        "a AND",
        "OR a",
        "a OR OR b",
        "()",
        "a AND ()",
        '"unterminated',
        '""',
        "*",
        "wom*n",
        "*agent",
        "-agent",
        "&&",
        "(" * 60 + "a" + ")" * 60,
        "a " * 1001,
    ],
)
def test_syntax_errors(query: str) -> None:
    with pytest.raises(QuerySyntaxError):
        parse_query(query)


def test_syntax_error_reports_position() -> None:
    with pytest.raises(QuerySyntaxError) as exc:
        parse_query("a AND (b OR c")
    assert exc.value.position == 6
    assert "position 6" in str(exc.value)


def test_syntax_error_is_value_error() -> None:
    with pytest.raises(ValueError, match="NOT to exclude"):
        parse_query("agent -benchmark")


def test_normalize() -> None:
    assert normalize("Prompt-Injection: LLM's (GPT-4o)") == "prompt injection llm s gpt 4o"
    assert normalize("Naïve Café ﬁne") == "naive cafe fine"
    assert normalize("snake_case") == "snake case"
    assert normalize("") == ""


RECORDS = [
    {
        "title": "Prompt Injection Attacks on LLM Agents",
        "abstract": "We study indirect prompt injections.",
        "keywords": ["agent security", "LLM"],
    },
    {
        "title": "Agentic workflows",
        "abstract": "Self-attention is all you need.",
        "keywords": ["prompt", "injection"],
    },
    {"title": "Naïve Bayes revisited", "abstract": "A classic.", "keywords": []},
    {"title": "On the prompt", "abstract": "Injection is studied here.", "keywords": None},
]


@pytest.fixture(scope="module")
def index() -> SearchIndex:
    return SearchIndex.build(RECORDS)


def _hits(index: SearchIndex, query: str, field: str = "title_abstract_keywords") -> list[int]:
    return index.search(parse_query(query), field)  # type: ignore[arg-type]


def test_whole_words_and_wildcards(index: SearchIndex) -> None:
    assert _hits(index, "agent", "title") == []
    assert _hits(index, "agent") == [0]  # keyword "agent security"
    assert _hits(index, "agent*", "title") == [0, 1]  # agents, agentic
    assert _hits(index, "inject*", "title_abstract") == [0, 3]


def test_phrases(index: SearchIndex) -> None:
    # Record 1 has "prompt" and "injection" only as separate keywords; record 3 has them
    # in different fields. Neither may match the phrase.
    assert _hits(index, '"prompt injection"') == [0]
    assert _hits(index, '"prompt injection*"', "title_abstract") == [0]
    assert _hits(index, '"indirect prompt"', "title") == []
    assert _hits(index, '"indirect prompt"', "title_abstract") == [0]
    assert _hits(index, '"inject* attacks"') == [0]  # wildcard before the last word


def test_implicit_and_spans_fields(index: SearchIndex) -> None:
    assert _hits(index, "prompt injection") == [0, 1, 3]
    assert _hits(index, "prompt injection", "title") == [0]


def test_boolean_operators(index: SearchIndex) -> None:
    assert _hits(index, 'agent* NOT "prompt injection"') == [1]
    assert _hits(index, "naive OR agentic") == [1, 2]
    assert _hits(index, "NOT prompt*") == [2]
    assert _hits(index, "(naive OR agentic) AND workflows") == [1]


def test_normalization_applies_to_text_and_query(index: SearchIndex) -> None:
    assert _hits(index, "self-attention") == [1]
    assert _hits(index, '"self attention"') == [1]
    assert _hits(index, "NAÏVE") == [2]
    assert _hits(index, "naive") == [2]


def test_no_query_matches_everything(index: SearchIndex) -> None:
    assert index.search(None) == [0, 1, 2, 3]


def test_unknown_field_rejected(index: SearchIndex) -> None:
    with pytest.raises(ValueError, match="Unknown field"):
        index.search(None, "abstract")  # type: ignore[arg-type]
