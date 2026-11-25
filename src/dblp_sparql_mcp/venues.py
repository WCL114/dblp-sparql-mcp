"""Resolve a venue name ("CCS", "USENIX Security") to dblp stream keys.

dblp streams have no acronym property (checked November 2026); acronyms usually close
the title in parentheses, e.g. "Annual ACM Conference on Computer and Communications
Security (CCS)". A plain substring match is noisy ("ccs" hits 56 streams), so results
are ranked: key, acronym or exact title, title prefix, any substring.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .client import Row, Selector
from .schemas import Venue
from .sparql import DBLP, STREAM_PREFIX, venue_query

MIN_NAME_LENGTH = 2
MAX_LIMIT = 50
_ACRONYM_RE = re.compile(r"\(([^()]+)\)")
_RANKS = ("key", "acronym", "title_prefix", "substring")


@dataclass(slots=True)
class _Stream:
    key: str
    label: str
    kinds: set[str] = field(default_factory=set)
    names: set[str] = field(default_factory=set)


def _group(rows: Iterable[Row]) -> list[_Stream]:
    streams: dict[str, _Stream] = {}
    for row in rows:
        key = row["s"].removeprefix(STREAM_PREFIX)
        s = streams.setdefault(key, _Stream(key=key, label=row["label"]))
        s.kinds.add(row["kind"].removeprefix(DBLP))
        s.names.add(row["name"])
    return list(streams.values())


def _rank(s: _Stream, needle: str) -> int:
    titles = [t.lower() for t in (s.label, *s.names)]
    if s.key.rsplit("/", 1)[-1].lower() == needle:
        return 0
    acronyms = {a.strip() for t in titles for a in _ACRONYM_RE.findall(t)}
    if needle in acronyms or needle in titles:
        return 1
    if any(t.startswith(needle) for t in titles):
        return 2
    return 3


def find_venues(
    client: Selector, name: str, *, limit: int = 10, refresh: bool = False
) -> dict[str, Any]:
    needle = " ".join(name.split()).lower()
    if len(needle) < MIN_NAME_LENGTH:
        raise ValueError(f"name must have at least {MIN_NAME_LENGTH} characters")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}, got {limit}")
    streams = _group(client.select(venue_query(needle), refresh=refresh))
    ranked = sorted(((_rank(s, needle), s) for s in streams), key=lambda rs: (rs[0], rs[1].key))
    venues = [
        Venue(
            key=s.key,
            title=s.label,
            kind=", ".join(sorted(s.kinds)),
            match=_RANKS[rank],
            other_titles=sorted(s.names - {s.label}),
        ).model_dump()
        for rank, s in ranked[:limit]
    ]
    return {"total": len(streams), "venues": venues}
