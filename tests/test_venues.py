"""Tests for venue-name resolution on stream records cut from a real response."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dblp_sparql_mcp.venues import find_venues

ROWS = json.loads((Path(__file__).parent / "data" / "venue_sample.json").read_text("utf-8"))["rows"]


class VenueFake:
    """Mimics the server-side filter: the name occurs in a title or in the key."""

    def __init__(self) -> None:
        self.queries: list[str] = []

    def select(self, query: str, *, refresh: bool = False) -> list[dict[str, str]]:
        self.queries.append(query)
        needle = query.split("CONTAINS(LCASE(STR(?name)), ")[1].split('"')[1]
        return [
            r
            for r in ROWS
            if needle in r["name"].lower() or needle in r["s"].split("/streams/")[1].lower()
        ]


def _keys(name: str, **kwargs: int) -> list[tuple[str, str]]:
    return [(v["key"], v["match"]) for v in find_venues(VenueFake(), name, **kwargs)["venues"]]


def test_key_match_ranks_first() -> None:
    ranked = _keys("CCS")
    assert ranked[:2] == [("conf/ccs", "key"), ("journals/ccs", "key")]
    assert ("conf/ccs2", "acronym") in ranked
    assert ranked[-1][1] == "substring"


def test_acronym_in_parentheses() -> None:
    assert _keys("S&P")[0] == ("conf/sp", "acronym")


def test_title_prefix() -> None:
    assert _keys("usenix security") == [("conf/uss", "title_prefix")]


def test_former_titles_are_reported() -> None:
    icml = find_venues(VenueFake(), "icml")["venues"][0]
    assert icml["key"] == "conf/icml" and icml["match"] == "key"
    assert icml["other_titles"] == ["International Workshop on Machine Learning (ML)"]
    assert icml["kind"] == "Conference"


def test_limit_and_total() -> None:
    out = find_venues(VenueFake(), "ccs", limit=2)
    assert len(out["venues"]) == 2
    assert out["total"] == 6


@pytest.mark.parametrize(("name", "limit"), [("c", 10), ("  ", 10), ("ccs", 0), ("ccs", 51)])
def test_invalid_arguments(name: str, limit: int) -> None:
    fake = VenueFake()
    with pytest.raises(ValueError):
        find_venues(fake, name, limit=limit)
    assert fake.queries == []
