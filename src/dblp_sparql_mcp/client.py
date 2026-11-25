"""Access to the dblp SPARQL endpoint: rate limiting, caching, and error classification.

Only https://sparql.dblp.org/sparql is contacted. Its robots.txt asks for a crawl delay
of 10 seconds, so one process never starts a request sooner than
``DBLP_SPARQL_MCP_MIN_INTERVAL`` seconds (default 10) after the previous one ended.
Cached results do not count as requests.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from . import __version__, cache

ENDPOINT = "https://sparql.dblp.org/sparql"
USER_AGENT = f"dblp-sparql-mcp/{__version__}"
DEFAULT_MIN_INTERVAL = 10.0
REQUEST_TIMEOUT = 60.0
CACHE_TTL = 24 * 3600

_FALLBACK = "use Scopus or Semantic Scholar instead. This is an outage, not an empty result"

log = logging.getLogger(__name__)

Row = dict[str, str]


class Selector(Protocol):
    """What the tools need from a client: run a SELECT query, get rows of plain values."""

    def select(self, query: str, *, refresh: bool = False) -> list[Row]: ...


class DblpError(Exception):
    """A failure whose message is written for the caller."""


class DblpUnavailableError(DblpError):
    """The endpoint is down, unreachable, or answered with something other than results."""


class DblpQueryError(DblpError):
    """The endpoint reported an exception for the query, such as its time limit."""


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    content_type: str
    body: bytes


Transport = Callable[[str], Response]


def urllib_transport(query: str) -> Response:
    """POST ``query`` to the endpoint; HTTP error statuses are returned, not raised."""
    request = urllib.request.Request(
        ENDPOINT,
        data=query.encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/sparql-query",
            "Accept": "application/sparql-results+json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as resp:
            return Response(resp.status, resp.headers.get("Content-Type", ""), resp.read())
    except urllib.error.HTTPError as exc:
        content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
        return Response(exc.code, content_type, exc.read())


def min_interval_from_env() -> float:
    raw = os.environ.get("DBLP_SPARQL_MCP_MIN_INTERVAL")
    if raw is None or not raw.strip():
        return DEFAULT_MIN_INTERVAL
    try:
        value = float(raw)
    except ValueError:
        log.warning("Ignoring DBLP_SPARQL_MCP_MIN_INTERVAL=%r: not a number", raw)
        return DEFAULT_MIN_INTERVAL
    return max(value, 0.0)


def parse_response(response: Response) -> list[Row]:
    """Turn an endpoint response into rows of plain values, or raise a DblpError."""
    data: Any = None
    if "json" in response.content_type.lower() or response.body.lstrip().startswith(b"{"):
        try:
            data = json.loads(response.body)
        except ValueError:
            data = None
    if isinstance(data, dict) and data.get("exception"):
        # QLever reports its time limit as HTTP 429 with this field; real throttling
        # (no field) is treated as an outage below.
        raise DblpQueryError(
            f"DBLP SPARQL could not run the query: {data['exception']}. If it timed out, "
            "narrow the search: add words, restrict the years, or name the venues."
        )
    results = data.get("results") if isinstance(data, dict) else None
    bindings = results.get("bindings") if isinstance(results, dict) else None
    if response.status != 200 or not isinstance(bindings, list):
        detail = f"HTTP {response.status}"
        if response.body.lstrip()[:1] == b"<":
            detail += ", an HTML page instead of results"
        elif bindings is None:
            detail += ", no results in the response"
        raise DblpUnavailableError(
            f"DBLP SPARQL is temporarily unavailable ({detail}); {_FALLBACK}."
        )
    return [{name: cell["value"] for name, cell in row.items()} for row in bindings]


class SparqlClient:
    """Thread-safe SPARQL access with a process-wide request interval and a disk cache."""

    def __init__(
        self,
        transport: Transport | None = None,
        *,
        min_interval: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._transport = transport or urllib_transport
        self.min_interval = min_interval_from_env() if min_interval is None else min_interval
        self._clock = clock
        self._sleep = sleep
        self._rate_lock = threading.Lock()
        self._last_end: float | None = None
        self._key_locks: dict[str, threading.Lock] = {}
        self._key_locks_guard = threading.Lock()
        self.requests_sent = 0

    def select(self, query: str, *, refresh: bool = False) -> list[Row]:
        """Run a SELECT query; identical concurrent queries share one request."""
        key = cache.make_key(query)
        with self._lock_for(key):
            if not refresh:
                hit = cache.get_value(key)
                if hit is not None:
                    return hit  # type: ignore[no-any-return]
            rows = self._fetch(query)
            cache.set_value(key, rows, CACHE_TTL)
            return rows

    def _lock_for(self, key: str) -> threading.Lock:
        with self._key_locks_guard:
            return self._key_locks.setdefault(key, threading.Lock())

    def _fetch(self, query: str) -> list[Row]:
        with self._rate_lock:
            if self._last_end is not None:
                wait = self._last_end + self.min_interval - self._clock()
                if wait > 0:
                    self._sleep(wait)
            try:
                response = self._transport(query)
            except OSError as exc:  # URLError, socket timeouts, connection resets
                raise DblpUnavailableError(
                    f"DBLP SPARQL is temporarily unavailable ({type(exc).__name__}: {exc}); "
                    f"{_FALLBACK}."
                ) from exc
            finally:
                self._last_end = self._clock()
                self.requests_sent += 1
        return parse_response(response)
