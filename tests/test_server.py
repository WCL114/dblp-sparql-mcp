"""MCP-level tests: the tool surface, results, and error reporting.

Calls go through the SDK's real request handling (argument validation, execution in a
worker thread, result conversion) over an in-process client that performs the
``initialize`` handshake. Only the SPARQL client is replaced.
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp.client import Client

from dblp_sparql_mcp import __version__, server
from dblp_sparql_mcp.client import DblpQueryError, DblpUnavailableError

from .conftest import FakeClient


def _connect() -> Client:
    return Client(server.mcp, mode="legacy")


def _text(result: Any) -> str:
    return " ".join(block.text for block in result.content)


class Failing:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def select(self, query: str, *, refresh: bool = False) -> list[dict[str, str]]:
        raise self.exc


@pytest.fixture
def use_client(monkeypatch):
    def _use(fake: Any) -> Any:
        monkeypatch.setattr(server, "_client", fake)
        return fake

    return _use


async def test_handshake_and_tool_schemas() -> None:
    async with _connect() as client:
        info = client.server_info
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert info is not None
    assert (info.name, info.version) == ("dblp-sparql-mcp", __version__)
    assert set(tools) == {"dblp_search", "dblp_find_venue"}
    assert all(tool.description and tool.output_schema for tool in tools.values())
    schema = tools["dblp_search"].input_schema
    assert "required" not in schema or schema["required"] == []
    type_items = schema["properties"]["types"]["anyOf"][0]["items"]
    assert "preprint" in type_items["enum"]
    assert tools["dblp_find_venue"].input_schema["required"] == ["name"]


async def test_search_returns_structured_results(use_client) -> None:
    use_client(FakeClient())
    async with _connect() as client:
        result = await client.call_tool(
            "dblp_search",
            {
                "query": '"prompt injection"',
                "venues": ["conf/uss"],
                "year_from": 2024,
                "year_to": 2025,
            },
        )
    assert not result.is_error
    out = result.structured_content
    assert out["total"] == 2
    assert [r["key"] for r in out["results"]] == ["conf/uss/ChenPS025", "conf/uss/LiuJGJG24"]


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"query": "NOT survey"}, "outside NOT"),
        ({"query": "agent AND"}, "Invalid query at position"),
        ({}, "at least one venue"),
        ({"query": "agent", "limit": 0}, "limit must be between 1 and 100"),
        ({"query": "agent", "export_path": "hits.ris"}, "export_path must be an absolute path"),
        ({"query": "agent", "types": ["poster"]}, "types"),  # rejected by the input schema
    ],
)
async def test_invalid_arguments_are_explained(use_client, arguments, expected) -> None:
    fake = use_client(FakeClient())
    async with _connect() as client:
        result = await client.call_tool("dblp_search", arguments)
    assert result.is_error
    assert expected in _text(result)
    assert fake.queries == []


@pytest.mark.parametrize(
    "exc",
    [
        DblpUnavailableError("DBLP SPARQL is temporarily unavailable (HTTP 503); use Scopus"),
        DblpQueryError("DBLP SPARQL could not run the query: Operation timed out."),
    ],
)
async def test_dblp_errors_reach_the_client(use_client, exc) -> None:
    use_client(Failing(exc))
    async with _connect() as client:
        result = await client.call_tool("dblp_search", {"query": "agent*"})
    assert result.is_error
    assert str(exc) in _text(result)


async def test_crash_details_stay_on_the_server(use_client) -> None:
    use_client(Failing(KeyError("internal detail")))
    async with _connect() as client:
        result = await client.call_tool("dblp_find_venue", {"name": "ccs"})
    assert result.is_error
    assert "internal detail" not in _text(result)
