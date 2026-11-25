"""MCP server: registers the dblp tools against a shared SPARQL client."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, Literal, ParamSpec, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__, search, venues
from .client import DblpError, SparqlClient

mcp = MCPServer("dblp-sparql-mcp", version=__version__)
_client = SparqlClient()

# Failures whose message is written for the caller: dblp errors (saying what to do about
# them), invalid arguments or queries, and export-file problems. MCPServer passes only a
# ToolError's text on to the client; it treats any other exception as a crash, logged
# with its traceback and reported without text.
_USER_ERRORS: tuple[type[Exception], ...] = (DblpError, ValueError, OSError)

PaperType = Literal[
    "conference",
    "journal",
    "preprint",
    "book",
    "incollection",
    "editorship",
    "data",
    "reference",
    "withdrawn",
]

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _tool(fn: Callable[_P, _R]) -> Callable[_P, _R]:
    """Register ``fn`` as an MCP tool whose user-facing failures reach the client."""

    @functools.wraps(fn)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        try:
            return fn(*args, **kwargs)
        except _USER_ERRORS as exc:
            raise ToolError(str(exc)) from exc

    mcp.tool()(wrapper)
    return wrapper


@_tool
def dblp_search(
    query: str | None = None,
    venues: list[str] | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    types: list[PaperType] | None = None,
    limit: int = 20,
    offset: int = 0,
    count_only: bool = False,
    export_path: str | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Exhaustive boolean TITLE search over all of dblp, or a complete venue listing.

    dblp has titles, authors and venues but no abstracts, so this is a title-only search;
    use Scopus or OpenAlex for topic (abstract) searches.

    Query syntax: AND, OR, NOT (case-insensitive), parentheses, "exact phrases", and a
    trailing * wildcard. Words written side by side are ANDed. Precedence is
    NOT > AND > OR, so parenthesize OR groups. Matching is on whole words without
    stemming, so use * for variants: agent* matches agent, agents, agentic. Example:
    ("prompt injection*" OR jailbreak*) AND (agent* OR "tool use"). NOT alone cannot be
    searched: every OR branch needs a word outside NOT.

    Without a query, lists every paper of the given venues (and years).
    venues: dblp venue keys such as "conf/uss", "conf/ccs", "conf/sp", "conf/ndss",
    "conf/nips", "journals/tifs"; find keys with dblp_find_venue. Proceedings of a venue
    include its co-located workshops; the `venue` field of each result tells them apart.
    year_from / year_to: inclusive. types: conference, journal (the default is these
    two), preprint (arXiv CoRR, IACR ePrint, ...), book, incollection, editorship, data,
    reference, withdrawn. dblp keeps a preprint and its published version as two
    records, so adding preprint can double counts.

    Returns total (every match), the canonical `parsed` query, `candidates`, and one page
    of results, newest first (limit <= 100, offset). Use count_only=true while refining a
    query: it needs 1 request instead of 2. export_path writes every match (at most 5000)
    to a new file, an absolute path ending in .json or .ris (RIS imports into Zotero).
    Requests are spaced 10 s apart and cached for 24 h; refresh=true bypasses the cache.
    """
    return search.search(
        _client,
        query=query,
        venues=venues,
        year_from=year_from,
        year_to=year_to,
        types=list(types) if types is not None else None,
        limit=limit,
        offset=offset,
        count_only=count_only,
        export_path=export_path,
        refresh=refresh,
    )


@_tool
def dblp_find_venue(name: str, limit: int = 10, refresh: bool = False) -> dict[str, Any]:
    """Find dblp venue keys (for dblp_search's `venues`) by name or acronym.

    Matches conference, journal and series titles (current and former) and keys,
    case-insensitively, e.g. "USENIX Security", "CCS", "S&P", "TIFS". Best matches first:
    key equal to the name, then acronym or exact title, then title prefix, then substring.
    Returns total and up to `limit` venues with key, title, kind and match.
    """
    return venues.find_venues(_client, name, limit=limit, refresh=refresh)
