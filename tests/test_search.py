"""Tests for search orchestration on records cut from real endpoint responses."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dblp_sparql_mcp import search as search_mod
from dblp_sparql_mcp.search import CandidateLimitError, assemble, search, year_range
from dblp_sparql_mcp.sparql import UnboundedQueryError

from .conftest import FakeClient

LIU = "conf/uss/LiuJGJG24"
CHEN = "conf/uss/ChenPS025"


def test_phrase_matches_exactly_the_two_known_papers(fake: FakeClient) -> None:
    out = search(
        fake, query='"prompt injection"', venues=["conf/uss"], year_from=2024, year_to=2025
    )
    assert out["total"] == 2
    assert [r["key"] for r in out["results"]] == [CHEN, LIU]  # newest first
    assert out["parsed"] == '"prompt injection"'
    assert out["candidates"] == 112
    assert out["filters"] == {
        "venues": ["conf/uss"],
        "year_from": 2024,
        "year_to": 2025,
        "types": ["conference", "journal"],
    }


def test_result_fields(fake: FakeClient) -> None:
    out = search(fake, query='"prompt injection"', venues=["conf/uss"])
    liu = next(r for r in out["results"] if r["key"] == LIU)
    assert liu == {
        "key": LIU,
        "title": "Formalizing and Benchmarking Prompt Injection Attacks and Defenses",
        "authors": ["Yupei Liu", "Yuqi Jia", "Runpeng Geng", "Jinyuan Jia", "Neil Zhenqiang Gong"],
        "venue": "USENIX Security Symposium",
        "year": 2024,
        "type": "conference",
        "doi": None,
        "url": "https://www.usenix.org/conference/usenixsecurity24/presentation/liu-yupei",
        "dblp_url": "https://dblp.org/rec/conf/uss/LiuJGJG24",
    }


def test_local_match_narrows_a_superset(fake: FakeClient) -> None:
    # Expected counts were taken from an independent regex count over the fixture titles.
    # The fake returns all 112 records for every candidate query.
    assert search(fake, query="prompt injection", count_only=True)["total"] == 2
    assert search(fake, query="injection", count_only=True)["total"] == 19
    assert search(fake, query="inject*", count_only=True)["total"] == 21
    assert search(fake, query="injection NOT fault", count_only=True)["total"] == 13
    assert search(fake, query="jailbreak*", count_only=True)["total"] == 10


def test_count_only_sends_one_request_and_agrees_with_full_results(fake: FakeClient) -> None:
    query = '("prompt injection" OR jailbreak*) AND (LLM* OR "language model*")'
    counted = search(fake, query=query, count_only=True)
    assert len(fake.queries) == 1
    assert "results" not in counted
    full = search(fake, query=query, limit=100)
    assert full["total"] == counted["total"] == len(full["results"]) == 9


def test_paging_is_stable(fake: FakeClient) -> None:
    keys = [r["key"] for r in search(fake, query="LLM*", limit=100)["results"]]
    page1 = search(fake, query="LLM*", limit=20)["results"]
    page2 = search(fake, query="LLM*", limit=20, offset=20)["results"]
    assert [r["key"] for r in page1 + page2] == keys[:40]
    beyond = search(fake, query="LLM*", offset=1000)
    assert beyond["results"] == [] and beyond["total"] == len(keys)


def test_details_are_fetched_only_for_the_page(fake: FakeClient) -> None:
    search(fake, query="LLM*", limit=5)
    assert len(fake.queries) == 2
    assert fake.queries[1].count("<https://dblp.org/rec/") == 5


def test_details_are_chunked(monkeypatch, fake: FakeClient) -> None:
    monkeypatch.setattr(search_mod, "DETAIL_CHUNK", 3)
    out = search(fake, query="LLM*", limit=7)
    assert len(out["results"]) == 7
    assert len(fake.queries) == 1 + 3


def test_listing_without_query_needs_a_venue(fake: FakeClient) -> None:
    out = search(fake, venues=["conf/uss"], year_from=2024, year_to=2024, count_only=True)
    assert out["parsed"] is None
    assert out["total"] == 112  # the fake ignores the year; every candidate is a hit
    with pytest.raises(ValueError, match="at least one venue"):
        search(fake)
    assert fake.queries[0].count("ql:contains-word") == 0


def test_record_missing_from_details_falls_back_to_the_candidate() -> None:
    fake = FakeClient(details=[])
    out = search(fake, query='"prompt injection"')
    assert out["results"][0] == {
        "key": CHEN,
        "title": "StruQ: Defending Against Prompt Injection with Structured Queries",
        "authors": [],
        "venue": None,
        "year": 2025,
        "type": "conference",
        "doi": None,
        "url": None,
        "dblp_url": "https://dblp.org/rec/" + CHEN,
    }


def test_candidate_limit(monkeypatch, fake: FakeClient) -> None:
    monkeypatch.setattr(search_mod, "CANDIDATE_LIMIT", 100)
    with pytest.raises(CandidateLimitError, match="More than 100 dblp records"):
        search(fake, query="LLM*")
    assert "LIMIT 101" in fake.queries[0]


@pytest.mark.parametrize(
    ("kwargs", "error", "message"),
    [
        ({"query": "NOT survey"}, UnboundedQueryError, "outside NOT"),
        ({"query": "agent AND"}, ValueError, "Invalid query at position"),
        ({"query": "agent", "limit": 0}, ValueError, "limit must be between"),
        ({"query": "agent", "offset": -1}, ValueError, "offset"),
        ({"query": "agent", "venues": ["USENIX Security"]}, ValueError, "dblp_find_venue"),
        ({"query": "agent", "types": []}, ValueError, "types must not be empty"),
        ({"query": "agent", "types": ["poster"]}, ValueError, "Unknown types"),
        ({"query": "agent", "year_from": 2025, "year_to": 2024}, ValueError, "after"),
        ({"query": "agent", "export_path": "hits.ris"}, ValueError, "absolute path"),
    ],
)
def test_invalid_arguments_send_no_request(fake: FakeClient, kwargs, error, message) -> None:
    with pytest.raises(error, match=message):
        search(fake, **kwargs)
    assert fake.queries == []


def test_year_range() -> None:
    assert year_range(None, None) == []
    assert year_range(2024, 2025) == [2024, 2025]
    assert year_range(None, 1940)[0] == 1936
    with pytest.raises(ValueError, match="span more than 100"):
        year_range(1900, 2025)


def test_assemble_merges_rows_and_cleans_values() -> None:
    rows = [
        {
            "pub": "https://dblp.org/rec/conf/ccs/X24",
            "title": "Why? A Study...",
            "type": "https://dblp.org/rdf/schema#Inproceedings",
            "y": "2024",
            "venue": "CCS",
            "doi": "https://doi.org/10.1145/1234",
            "ord": str(i),
            "name": name,
        }
        for i, name in [(2, "Bo Li 0003"), (1, "Ann Lee"), (10, "Zed Wu")]
    ]
    paper = assemble(rows)["https://dblp.org/rec/conf/ccs/X24"]
    assert paper.title == "Why? A Study..."
    assert paper.authors == ["Ann Lee", "Bo Li", "Zed Wu"]
    assert paper.doi == "10.1145/1234"


def test_export_writes_every_match(tmp_path: Path, fake: FakeClient) -> None:
    target = tmp_path / "hits.json"
    out = search(fake, query="jailbreak*", export_path=str(target), count_only=True)
    assert out["export"] == {"path": str(target), "format": "json", "count": 10}
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["meta"]["total"] == 10 and data["meta"]["parsed"] == "jailbreak*"
    assert len(data["results"]) == 10
    # 10 matches fit in one detail chunk: 1 candidate query + 1 detail query.
    assert len(fake.queries) == 2


def test_export_reuses_details_for_the_page(tmp_path: Path, fake: FakeClient) -> None:
    out = search(fake, query="jailbreak*", export_path=str(tmp_path / "hits.ris"), limit=5)
    assert len(out["results"]) == 5
    assert len(fake.queries) == 2


def test_export_limit(monkeypatch, tmp_path: Path, fake: FakeClient) -> None:
    monkeypatch.setattr(search_mod, "EXPORT_LIMIT", 5)
    with pytest.raises(ValueError, match="export limit"):
        search(fake, query="jailbreak*", export_path=str(tmp_path / "hits.ris"))
    assert not (tmp_path / "hits.ris").exists()
