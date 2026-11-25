"""SPARQL text for the dblp endpoint: candidate queries, detail queries, venue lookup.

Candidate retrieval translates the boolean query into QLever text-index patterns that
return a superset of the matches; ``query.SearchIndex`` then decides exactly. Measured
on the live endpoint (November 2026): the index splits titles at every non-alphanumeric
character and ignores case, like ``query.normalize``. An exact word is
accent-sensitive ("jager" misses "Jäger") while a prefix is not ("jager*" finds it),
so every word is sent as a prefix. Several words in one ``ql:contains-word`` string must
all occur in the title, in any order.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterator, Sequence

from .query import And, Node, Not, Or, Seq

DBLP = "https://dblp.org/rdf/schema#"
STREAM_PREFIX = "https://dblp.org/streams/"
RECORD_PREFIX = "https://dblp.org/rec/"

PREFIXES = """PREFIX dblp: <https://dblp.org/rdf/schema#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
PREFIX ql: <http://qlever.cs.uni-freiburg.de/builtin-functions/>
"""

# Publication types: the tool's name for each and the dblp class.
TYPE_CLASSES: dict[str, str] = {
    "conference": "Inproceedings",
    "journal": "Article",
    "preprint": "Informal",
    "book": "Book",
    "incollection": "Incollection",
    "editorship": "Editorship",
    "data": "Data",
    "reference": "Reference",
    "withdrawn": "Withdrawn",
}
TYPE_NAMES = {cls: name for name, cls in TYPE_CLASSES.items()}

_STREAM_KEY_RE = re.compile(r"^[a-z]+/[A-Za-z0-9][A-Za-z0-9_.+-]*$")
# Characters an IRIREF may not contain (SPARQL 1.1 grammar).
_IRI_FORBIDDEN_RE = re.compile(r'[<>"{}|^`\\\x00-\x20]')


class UnboundedQueryError(ValueError):
    """The query cannot narrow the candidates: it relies on NOT alone somewhere."""


def literal(text: str) -> str:
    """Quote ``text`` as a SPARQL string literal."""
    escaped = (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def iri(value: str) -> str:
    if _IRI_FORBIDDEN_RE.search(value):
        raise ValueError(f"{value!r} cannot be used in an IRI")
    return f"<{value}>"


def stream_iri(venue: str) -> str:
    """Accept a stream key ("conf/uss") or its full IRI; return the IRI."""
    key = venue.strip()
    if key.startswith(STREAM_PREFIX):
        key = key[len(STREAM_PREFIX) :]
    if not _STREAM_KEY_RE.match(key):
        raise ValueError(
            f"{venue!r} is not a dblp venue key such as 'conf/uss' or 'journals/tifs'; "
            "use dblp_find_venue to look one up"
        )
    return STREAM_PREFIX + key


def record_key(pub_iri: str) -> str:
    return pub_iri[len(RECORD_PREFIX) :] if pub_iri.startswith(RECORD_PREFIX) else pub_iri


# ---- Boolean query -> text-index pattern ----


def narrows(node: Node) -> bool:
    """True if ``node`` restricts the records to those containing some words."""
    if isinstance(node, Seq):
        return True
    if isinstance(node, Not):
        return False
    if isinstance(node, And):
        return any(narrows(c) for c in node.children)
    return all(narrows(c) for c in node.children)


def text_pattern(node: Node) -> str:
    """Graph pattern binding ``?title`` to a superset of the titles matching ``node``."""
    if not narrows(node):
        raise UnboundedQueryError(
            "The query cannot be searched across dblp: NOT only excludes, so the whole "
            "query and every OR branch need at least one word or phrase outside NOT. "
            "agent* NOT survey works; NOT survey alone, or (agent* OR NOT survey), does not."
        )
    return _pattern(node, itertools.count(1))


def _prefix_words(seq: Seq) -> list[str]:
    return [f"{w.text}*" for w in seq.words]


def _text_block(words: Sequence[str], counter: Iterator[int]) -> str:
    var = f"?t{next(counter)}"
    unique = list(dict.fromkeys(words))
    return f"{var} ql:contains-entity ?title . {var} ql:contains-word {literal(' '.join(unique))} ."


def _pattern(node: Node, counter: Iterator[int]) -> str:
    if isinstance(node, Seq):
        return _text_block(_prefix_words(node), counter)
    if isinstance(node, Or):
        return " UNION ".join("{ " + _pattern(c, counter) + " }" for c in node.children)
    if isinstance(node, And):
        words: list[str] = []
        groups: list[str] = []
        for child in node.children:
            if isinstance(child, Seq):
                # Words of several ANDed terms all go into one string: same title.
                words.extend(_prefix_words(child))
            elif narrows(child):
                groups.append("{ " + _pattern(child, counter) + " }")
            # Children that cannot narrow (NOT, OR with a NOT branch) are left to the
            # exact local check; skipping them only widens the candidates.
        if words:
            groups.insert(0, _text_block(words, counter))
        return " ".join(groups)
    raise AssertionError("NOT nodes never narrow")  # pragma: no cover


# ---- Queries ----


def _values(var: str, terms: Sequence[str]) -> str:
    return f"  VALUES {var} {{ {' '.join(terms)} }}"


def candidate_query(
    node: Node | None,
    *,
    streams: Sequence[str],
    years: Sequence[int],
    types: Sequence[str],
    limit: int,
) -> str:
    """Select every record that may match: key, title, type and year, at most ``limit``.

    Years are joined as typed literals: ``FILTER(STR(?y) IN (...))`` timed out on large
    candidate sets while the join took seconds.
    """
    lines = [PREFIXES + "SELECT DISTINCT ?pub ?title ?type ?y WHERE {"]
    if years:
        lines.append(_values("?y", [f'"{y}"^^xsd:gYear' for y in years]))
        lines.append("  ?pub dblp:yearOfPublication ?y .")
    if streams:
        lines.append(_values("?stream", [iri(s) for s in streams]))
        lines.append("  ?pub dblp:publishedInStream ?stream .")
    if node is not None:
        lines.append("  " + text_pattern(node))
    lines.append("  ?pub dblp:title ?title .")
    lines.append(_values("?type", [f"dblp:{TYPE_CLASSES[t]}" for t in types]))
    lines.append("  ?pub rdf:type ?type .")
    if not years:
        lines.append("  OPTIONAL { ?pub dblp:yearOfPublication ?y }")
    lines.append("}")
    lines.append(f"LIMIT {limit}")
    return "\n".join(lines) + "\n"


def details_query(pub_iris: Sequence[str]) -> str:
    """One row per author (or one row without author) for each record."""
    values = " ".join(iri(p) for p in pub_iris)
    return (
        PREFIXES
        + "SELECT ?pub ?title ?type ?y ?venue ?doi ?page ?ord ?name WHERE {\n"
        + f"  VALUES ?pub {{ {values} }}\n"
        + "  ?pub dblp:title ?title ; rdf:type ?type .\n"
        + "  FILTER(?type != dblp:Publication)\n"
        + "  OPTIONAL { ?pub dblp:yearOfPublication ?y }\n"
        + "  OPTIONAL { ?pub dblp:publishedIn ?venue }\n"
        + "  OPTIONAL { ?pub dblp:doi ?doi }\n"
        + "  OPTIONAL { ?pub dblp:primaryDocumentPage ?page }\n"
        + "  OPTIONAL { ?pub dblp:hasSignature ?sig . ?sig rdf:type dblp:AuthorSignature ; "
        + "dblp:signatureOrdinal ?ord ; dblp:signatureDblpName ?name }\n"
        + "}\n"
    )


def venue_query(name: str) -> str:
    """Streams whose title, former title or key contains ``name`` (case-insensitive)."""
    needle = literal(name.strip().lower())
    return (
        PREFIXES
        + "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
        + "SELECT ?s ?kind ?label ?name WHERE {\n"
        + "  ?s rdf:type dblp:Stream ; rdf:type ?kind ; rdfs:label ?label .\n"
        + "  FILTER(?kind != dblp:Stream)\n"
        + "  { ?s dblp:streamTitle ?name } UNION { ?s dblp:formerStreamTitle ?name }\n"
        + f"  FILTER(CONTAINS(LCASE(STR(?name)), {needle}) || "
        + f'CONTAINS(LCASE(STRAFTER(STR(?s), "{STREAM_PREFIX}")), {needle}))\n'
        + "}\n"
    )
