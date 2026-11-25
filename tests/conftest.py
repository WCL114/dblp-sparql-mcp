"""Shared fixtures. Tests run offline: no request ever reaches sparql.dblp.org.

``tests/data/uss_sample.json`` is cut from real endpoint responses (2026-11-25): 112
USENIX Security 2024-2025 records and, for the 2024 ones, their detail rows.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import diskcache
import pytest

os.environ["DBLP_SPARQL_MCP_NO_CACHE"] = "1"

DATA = Path(__file__).parent / "data"
_VALUES_PUB_RE = re.compile(r"VALUES \?pub \{([^}]*)\}")


def load_sample() -> dict[str, Any]:
    return json.loads((DATA / "uss_sample.json").read_text(encoding="utf-8"))


class FakeClient:
    """Answers candidate queries with every sample record and detail queries by key.

    The fake ignores the text pattern and filters, i.e. it returns a superset of the
    candidates, which the exact local match must narrow.
    """

    def __init__(
        self,
        candidates: list[dict[str, str]] | None = None,
        details: list[dict[str, str]] | None = None,
    ) -> None:
        sample = load_sample()
        self.candidates = sample["candidates"] if candidates is None else candidates
        self.details = sample["details"] if details is None else details
        self.queries: list[str] = []

    def select(self, query: str, *, refresh: bool = False) -> list[dict[str, str]]:
        self.queries.append(query)
        match = _VALUES_PUB_RE.search(query)
        if match:
            wanted = set(re.findall(r"<([^>]+)>", match.group(1)))
            return [r for r in self.details if r["pub"] in wanted]
        if "dblp:Stream" in query:
            return []
        return list(self.candidates)


@pytest.fixture
def fake() -> FakeClient:
    return FakeClient()


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    """Enable caching for one test, backed by a throwaway directory."""
    from dblp_sparql_mcp import cache

    monkeypatch.setenv("DBLP_SPARQL_MCP_NO_CACHE", "0")
    disk = diskcache.Cache(str(tmp_path / "cache"))
    monkeypatch.setattr(cache, "_cache", disk)
    yield disk
    disk.close()
