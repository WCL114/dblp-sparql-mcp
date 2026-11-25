"""Tests for cache keys and the disabled cache."""

from __future__ import annotations

from dblp_sparql_mcp import cache


def test_key_ignores_layout_only() -> None:
    assert cache.make_key("SELECT ?x\n  WHERE { }") == cache.make_key("SELECT ?x WHERE { }")
    assert cache.make_key("SELECT ?x") != cache.make_key("SELECT ?y")


def test_disabled_cache_returns_nothing() -> None:
    # The test suite runs with DBLP_SPARQL_MCP_NO_CACHE=1 (see conftest).
    cache.set_value("k", [1], 60)
    assert cache.get_value("k") is None


def test_round_trip(tmp_cache) -> None:
    cache.set_value("k", [{"a": "1"}], 60)
    assert cache.get_value("k") == [{"a": "1"}]


def test_cache_dir_from_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DBLP_SPARQL_MCP_CACHE_DIR", str(tmp_path))
    assert cache.cache_dir() == tmp_path
    monkeypatch.delenv("DBLP_SPARQL_MCP_CACHE_DIR")
    assert cache.cache_dir().name == "dblp-sparql-mcp"
