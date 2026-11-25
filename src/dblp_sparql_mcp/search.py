"""Exhaustive title search and venue listings: candidates from SPARQL, exact matching here.

1. One SPARQL query fetches every record that may match (key, title, type, year).
2. ``query.SearchIndex`` decides the matches with the same semantics as openreview-mcp.
3. Only the records on the requested page (or in an export) are fetched in full.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from . import export
from .client import Row, Selector
from .query import Node, SearchIndex, parse_query, to_string
from .schemas import Paper
from .sparql import (
    DBLP,
    RECORD_PREFIX,
    STREAM_PREFIX,
    TYPE_CLASSES,
    TYPE_NAMES,
    candidate_query,
    details_query,
    record_key,
    stream_iri,
    text_pattern,
)

DEFAULT_TYPES: tuple[str, ...] = ("conference", "journal")
CANDIDATE_LIMIT = 25_000
MAX_LIMIT = 100
MAX_VENUES = 20
DETAIL_CHUNK = 100
EXPORT_LIMIT = 5000
MIN_YEAR = 1936  # older than any dblp record
MAX_YEAR_SPAN = 100

_DISAMBIGUATION_RE = re.compile(r" \d{4}$")  # "Yuqi Jia 0001" -> "Yuqi Jia"
_DOI_PREFIX_RE = re.compile(r"^https?://(?:dx\.)?doi\.org/", re.IGNORECASE)


class CandidateLimitError(ValueError):
    """Too many candidate records to fetch; the caller has to narrow the search."""


@dataclass(frozen=True, slots=True)
class _Candidate:
    iri: str
    key: str
    title: str
    type: str
    year: int | None


@dataclass(slots=True)
class _Details:
    title: str
    types: set[str] = field(default_factory=set)
    years: set[int] = field(default_factory=set)
    venues: set[str] = field(default_factory=set)
    dois: set[str] = field(default_factory=set)
    pages: set[str] = field(default_factory=set)
    authors: dict[int, str] = field(default_factory=dict)


# ---- Arguments ----


def year_range(year_from: int | None, year_to: int | None) -> list[int]:
    """The years to search, both ends included; empty for no restriction."""
    if year_from is None and year_to is None:
        return []
    start = MIN_YEAR if year_from is None else year_from
    end = datetime.now(UTC).year + 1 if year_to is None else year_to
    if start > end:
        raise ValueError(f"year_from ({start}) is after year_to ({end})")
    if end - start + 1 > MAX_YEAR_SPAN:
        raise ValueError(
            f"The years {start}-{end} span more than {MAX_YEAR_SPAN}; give both year_from "
            "and year_to, or neither"
        )
    return list(range(start, end + 1))


def resolve_types(types: Sequence[str] | None) -> list[str]:
    if types is None:
        return list(DEFAULT_TYPES)
    if not types:
        raise ValueError("types must not be empty; omit it for conference and journal papers")
    unknown = [t for t in types if t not in TYPE_CLASSES]
    if unknown:
        raise ValueError(f"Unknown types {unknown}; expected some of {', '.join(TYPE_CLASSES)}")
    return list(dict.fromkeys(types))


def resolve_venues(venues: Sequence[str] | None) -> list[str]:
    streams = list(dict.fromkeys(stream_iri(v) for v in venues or []))
    if len(streams) > MAX_VENUES:
        raise ValueError(f"At most {MAX_VENUES} venues per search, got {len(streams)}")
    return streams


def _parse(query: str | None) -> Node | None:
    if query is None or not query.strip():
        return None
    node = parse_query(query)
    text_pattern(node)  # rejects queries that cannot narrow, before any request
    return node


# ---- Records ----


def _year(value: str | None) -> int | None:
    if value and value[:4].isdigit():
        return int(value[:4])
    return None


def _type_name(type_iri: str) -> str:
    local = type_iri.removeprefix(DBLP)
    return TYPE_NAMES.get(local, local.lower())


def _clean_title(title: str) -> str:
    # dblp ends titles with a period ("... Defenses."); keep "?" and "!" and ellipses.
    if title.endswith(".") and not title.endswith(".."):
        return title[:-1]
    return title


def _candidates(rows: Iterable[Row]) -> list[_Candidate]:
    seen: dict[str, _Candidate] = {}
    for row in rows:
        pub = row["pub"]
        if pub not in seen:
            seen[pub] = _Candidate(
                iri=pub,
                key=record_key(pub),
                title=row["title"],
                type=_type_name(row["type"]),
                year=_year(row.get("y")),
            )
    return list(seen.values())


def assemble(rows: Iterable[Row]) -> dict[str, Paper]:
    """Build papers from detail rows (one row per author), keyed by record IRI."""
    acc: dict[str, _Details] = {}
    for row in rows:
        d = acc.setdefault(row["pub"], _Details(title=row["title"]))
        d.types.add(_type_name(row["type"]))
        if (year := _year(row.get("y"))) is not None:
            d.years.add(year)
        for value, target in (
            (row.get("venue"), d.venues),
            (row.get("doi"), d.dois),
            (row.get("page"), d.pages),
        ):
            if value:
                target.add(value)
        if row.get("ord", "").isdigit() and row.get("name"):
            d.authors[int(row["ord"])] = _DISAMBIGUATION_RE.sub("", row["name"])
    papers: dict[str, Paper] = {}
    for pub, d in acc.items():
        key = record_key(pub)
        papers[pub] = Paper(
            key=key,
            title=_clean_title(d.title),
            authors=[d.authors[i] for i in sorted(d.authors)],
            venue=min(d.venues) if d.venues else None,
            year=min(d.years) if d.years else None,
            type=min(d.types) if d.types else "unknown",
            doi=_DOI_PREFIX_RE.sub("", min(d.dois)) if d.dois else None,
            url=min(d.pages) if d.pages else None,
            dblp_url=RECORD_PREFIX + key,
        )
    return papers


def _fallback(c: _Candidate) -> Paper:
    """A record that vanished between the two queries: what the candidate row said."""
    return Paper(
        key=c.key,
        title=_clean_title(c.title),
        year=c.year,
        type=c.type,
        dblp_url=RECORD_PREFIX + c.key,
    )


def fetch_papers(
    client: Selector, candidates: Sequence[_Candidate], *, refresh: bool
) -> list[Paper]:
    """Full records for ``candidates``, in their order, ``DETAIL_CHUNK`` per request."""
    found: dict[str, Paper] = {}
    for start in range(0, len(candidates), DETAIL_CHUNK):
        chunk = candidates[start : start + DETAIL_CHUNK]
        found.update(
            assemble(client.select(details_query([c.iri for c in chunk]), refresh=refresh))
        )
    return [found.get(c.iri) or _fallback(c) for c in candidates]


# ---- Search ----


def search(
    client: Selector,
    *,
    query: str | None = None,
    venues: Sequence[str] | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    types: Sequence[str] | None = None,
    limit: int = 20,
    offset: int = 0,
    count_only: bool = False,
    export_path: str | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}, got {limit}")
    if offset < 0:
        raise ValueError(f"offset must not be negative, got {offset}")
    node = _parse(query)
    streams = resolve_venues(venues)
    if node is None and not streams:
        raise ValueError(
            "Give a query, or at least one venue (e.g. venues=['conf/uss']) to list all of "
            "its papers"
        )
    years = year_range(year_from, year_to)
    type_names = resolve_types(types)
    target = export.prepare_target(export_path) if export_path else None

    sparql = candidate_query(
        node, streams=streams, years=years, types=type_names, limit=CANDIDATE_LIMIT + 1
    )
    rows = client.select(sparql, refresh=refresh)
    if len(rows) > CANDIDATE_LIMIT:
        raise CandidateLimitError(
            f"More than {CANDIDATE_LIMIT:,} dblp records contain the words of this query "
            "(before the exact match), too many to fetch. Add words, restrict the years, "
            "or name venues; results are never truncated."
        )
    candidates = _candidates(rows)
    index = SearchIndex.build({"title": c.title} for c in candidates)
    hits = sorted(
        (candidates[i] for i in index.search(node, "title")),
        key=lambda c: (-(c.year or 0), c.key),
    )

    filters = {
        "venues": [s.removeprefix(STREAM_PREFIX) for s in streams],
        "year_from": years[0] if years else None,
        "year_to": years[-1] if years else None,
        "types": type_names,
    }
    result: dict[str, Any] = {
        "total": len(hits),
        "offset": offset,
        "count_only": count_only,
        "parsed": to_string(node) if node is not None else None,
        "candidates": len(candidates),
        "filters": filters,
    }

    exported: dict[str, Paper] = {}
    if target is not None:
        path, fmt = target
        if len(hits) > EXPORT_LIMIT:
            raise ValueError(
                f"{len(hits):,} matches exceed the export limit of {EXPORT_LIMIT:,}; "
                "split the search by years or venues"
            )
        papers = fetch_papers(client, hits, refresh=refresh)
        exported = {p.key: p for p in papers}
        meta = {**{k: v for k, v in result.items() if k != "count_only"}, "source": "dblp SPARQL"}
        meta["exported_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        result["export"] = export.write_export(path, fmt, papers, meta)

    if not count_only:
        page = hits[offset : offset + limit]
        if all(c.key in exported for c in page):
            papers = [exported[c.key] for c in page]
        else:
            papers = fetch_papers(client, page, refresh=refresh)
        result["results"] = [p.model_dump() for p in papers]
    return result
