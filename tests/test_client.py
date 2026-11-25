"""Tests for request spacing, caching, and error classification (fake transport)."""

from __future__ import annotations

import json
import threading
import time

import pytest

from dblp_sparql_mcp.client import (
    DblpQueryError,
    DblpUnavailableError,
    Response,
    SparqlClient,
    min_interval_from_env,
    parse_response,
)

JSON = "application/sparql-results+json"


def _ok(rows: list[dict[str, str]]) -> Response:
    bindings = [{k: {"type": "literal", "value": v} for k, v in r.items()} for r in rows]
    return Response(200, JSON, json.dumps({"results": {"bindings": bindings}}).encode())


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class Transport:
    def __init__(self, clock: FakeClock, response: Response, duration: float = 2.0) -> None:
        self.clock, self.response, self.duration = clock, response, duration
        self.starts: list[float] = []

    def __call__(self, query: str) -> Response:
        self.starts.append(self.clock.now)
        self.clock.now += self.duration
        return self.response


def _client(
    response: Response, interval: float = 10.0
) -> tuple[SparqlClient, Transport, FakeClock]:
    clock = FakeClock()
    transport = Transport(clock, response)
    client = SparqlClient(transport, min_interval=interval, clock=clock, sleep=clock.sleep)
    return client, transport, clock


def test_rows_are_plain_values() -> None:
    client, _, _ = _client(_ok([{"pub": "p1", "title": "T."}]))
    assert client.select("Q1") == [{"pub": "p1", "title": "T."}]


def test_requests_are_spaced_from_the_end_of_the_previous_one() -> None:
    client, transport, clock = _client(_ok([]))
    client.select("Q1")
    client.select("Q2")
    clock.now += 4.0  # time spent elsewhere counts towards the interval
    client.select("Q3")
    gaps = [b - a for a, b in zip(transport.starts, transport.starts[1:], strict=False)]
    # Each request lasts 2 s, so starts are 12 s apart: 10 s after the previous end.
    assert gaps == [12.0, 12.0]
    assert clock.sleeps == [10.0, 6.0]


def test_cache_hits_send_no_request(tmp_cache) -> None:
    client, transport, _ = _client(_ok([{"x": "1"}]))
    assert client.select("SELECT  ?x\nWHERE {}") == [{"x": "1"}]
    assert client.select("SELECT ?x WHERE {}") == [{"x": "1"}]  # same after whitespace
    assert len(transport.starts) == 1
    client.select("SELECT ?x WHERE {}", refresh=True)
    assert len(transport.starts) == 2


def test_cache_survives_a_new_client(tmp_cache) -> None:
    first, _, _ = _client(_ok([{"x": "1"}]))
    first.select("Q")
    second, transport, _ = _client(_ok([{"x": "2"}]))
    assert second.select("Q") == [{"x": "1"}]
    assert transport.starts == []


def test_empty_results_are_cached(tmp_cache) -> None:
    client, transport, _ = _client(_ok([]))
    assert client.select("Q") == []
    assert client.select("Q") == []
    assert len(transport.starts) == 1


def test_concurrent_identical_queries_share_one_request(tmp_cache) -> None:
    calls: list[str] = []

    def slow(query: str) -> Response:
        calls.append(query)
        time.sleep(0.2)
        return _ok([{"x": "1"}])

    client = SparqlClient(slow, min_interval=0)
    results: list[object] = []
    threads = [
        threading.Thread(target=lambda: results.append(client.select("Q"))) for _ in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls == ["Q"]
    assert results == [[{"x": "1"}]] * 5


def test_server_exception_is_passed_through() -> None:
    # What QLever sent when a query hit its time limit (probe D2): HTTP 429 plus JSON.
    body = json.dumps(
        {"exception": "Operation timed out. Last operation: Sort", "status": "ERROR"}
    ).encode()
    with pytest.raises(DblpQueryError, match=r"Operation timed out. Last operation: Sort"):
        parse_response(Response(429, "application/json", body))


@pytest.mark.parametrize(
    ("response", "detail"),
    [
        (Response(200, "text/html", b"<html>Making sure you're not a bot!</html>"), "HTML page"),
        (Response(503, "text/html", b"<html>Service Unavailable</html>"), "HTTP 503"),
        (Response(429, "text/plain", b"Too Many Requests"), "HTTP 429"),
        (Response(200, JSON, b'{"head": {}}'), "no results"),
        (Response(502, JSON, b"not json"), "HTTP 502"),
    ],
)
def test_bad_responses_are_outages(response: Response, detail: str) -> None:
    with pytest.raises(DblpUnavailableError, match="temporarily unavailable") as exc:
        parse_response(response)
    assert detail in str(exc.value)
    assert "Scopus or Semantic Scholar" in str(exc.value)
    assert "not an empty result" in str(exc.value)


def test_network_errors_are_outages_and_still_spaced() -> None:
    clock = FakeClock()

    def broken(query: str) -> Response:
        clock.now += 60.0
        raise TimeoutError("timed out")

    client = SparqlClient(broken, min_interval=10, clock=clock, sleep=clock.sleep)
    with pytest.raises(DblpUnavailableError, match="TimeoutError"):
        client.select("Q")
    with pytest.raises(DblpUnavailableError):
        client.select("Q")
    assert clock.sleeps == [10.0]
    assert client.requests_sent == 2


def test_failures_are_not_cached(tmp_cache) -> None:
    client, transport, _ = _client(Response(503, "text/html", b"<html></html>"))
    for _ in range(2):
        with pytest.raises(DblpUnavailableError):
            client.select("Q")
    assert len(transport.starts) == 2


@pytest.mark.parametrize(
    ("raw", "expected"), [(None, 10.0), ("", 10.0), ("12.5", 12.5), ("-3", 0.0), ("abc", 10.0)]
)
def test_min_interval_from_env(monkeypatch, raw: str | None, expected: float) -> None:
    if raw is None:
        monkeypatch.delenv("DBLP_SPARQL_MCP_MIN_INTERVAL", raising=False)
    else:
        monkeypatch.setenv("DBLP_SPARQL_MCP_MIN_INTERVAL", raw)
    assert min_interval_from_env() == expected
