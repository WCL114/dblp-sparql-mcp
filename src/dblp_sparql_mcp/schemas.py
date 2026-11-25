"""Pydantic schemas for the records returned by the tools."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Paper(BaseModel):
    """One dblp record."""

    key: str  # dblp key, e.g. "conf/uss/LiuJGJG24"
    title: str  # without dblp's trailing period
    authors: list[str] = Field(default_factory=list)  # in byline order
    venue: str | None = None  # dblp "publishedIn", e.g. "SOUPS @ USENIX Security Symposium"
    year: int | None = None
    type: str  # conference, journal, preprint, ... (see sparql.TYPE_CLASSES)
    doi: str | None = None
    url: str | None = None  # landing page of the publisher or proceedings
    dblp_url: str  # the record on dblp.org (a link only; this server never fetches it)


class Venue(BaseModel):
    """A dblp stream: a conference series, journal, or book series."""

    key: str  # e.g. "conf/uss"; pass it in `venues`
    title: str
    kind: str  # Conference, Journal, Series, ...
    match: str  # why it matched: key, acronym, title_prefix, substring
    other_titles: list[str] = Field(default_factory=list)
