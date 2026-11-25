# Copied unchanged from openreview-mcp (https://github.com/WCL114/openreview-mcp),
# src/openreview_mcp/query.py at commit 5fb30440e36d9a2815a28a4886b68f28a57c900d.
# Keep both copies identical below this header; compare them with diff after edits.
# Copyright (c) 2026 OpenCódice Research. MIT License, see LICENSE.

"""Boolean query language for exhaustive, local submission search.

Syntax (operators are case-insensitive):

- ``AND``, ``OR``, ``NOT`` and parentheses. Precedence is ``NOT`` > ``AND`` > ``OR``,
  so mix ``OR`` with ``AND``/``NOT`` inside parentheses. ``A NOT B`` means
  ``A AND NOT B``; a leading ``NOT B`` is also allowed.
- Terms written next to each other without an operator are combined with ``AND``.
- ``"double quotes"`` match an exact phrase (consecutive words).
- A trailing ``*`` matches any word ending: ``agent*`` matches agent, agents and
  agentic. It is only allowed at the end of a word, also inside a phrase
  (``"prompt inject*"``).

Matching is on whole words after normalization: case-folding, accent stripping,
and every run of non-alphanumeric characters treated as a word separator. Hence
``agent`` does not match "agents", and ``self-attention`` matches the phrase
"self attention". There is no stemming.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

Field = Literal["title", "title_abstract", "title_abstract_keywords"]
FIELDS: tuple[Field, ...] = ("title", "title_abstract", "title_abstract_keywords")

MAX_QUERY_LENGTH = 2000
MAX_NESTING = 50

_SEPARATORS_RE = re.compile(r"[\W_]+")
# Straight and typographic double quotes all delimit phrases.
_QUOTES = frozenset('"“”„')
_OPERATORS = frozenset({"AND", "OR", "NOT"})
# Token kinds that continue an AND chain: an explicit AND, a binary NOT, or the start
# of another operand (implicit AND).
_AND_CONTINUES = frozenset({"AND", "NOT", "TERM", "PHRASE", "LPAREN"})


class QuerySyntaxError(ValueError):
    """Raised for a malformed query; ``position`` is the 0-based character offset."""

    def __init__(self, message: str, position: int) -> None:
        super().__init__(f"Invalid query at position {position}: {message}")
        self.position = position


def normalize(text: str) -> str:
    """Case-fold, strip accents, and collapse non-alphanumeric runs into single spaces."""
    if not text:
        return ""
    if not text.isascii():
        decomposed = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _SEPARATORS_RE.sub(" ", text.casefold()).strip()


# ---- AST ----


@dataclass(frozen=True, slots=True)
class Word:
    text: str
    prefix: bool = False


@dataclass(frozen=True, slots=True)
class Seq:
    """A single word or a phrase: consecutive words."""

    words: tuple[Word, ...]


@dataclass(frozen=True, slots=True)
class And:
    children: tuple[Node, ...]


@dataclass(frozen=True, slots=True)
class Or:
    children: tuple[Node, ...]


@dataclass(frozen=True, slots=True)
class Not:
    child: Node


Node: TypeAlias = Seq | And | Or | Not


def to_string(node: Node) -> str:
    """Render the canonical form: every nested group parenthesized, the outermost not."""
    return _render(node, top=True)


def _render(node: Node, top: bool) -> str:
    if isinstance(node, Seq):
        text = " ".join(w.text + ("*" if w.prefix else "") for w in node.words)
        return f'"{text}"' if len(node.words) > 1 else text
    if isinstance(node, Not):
        return "NOT " + _render(node.child, top=False)
    op = " AND " if isinstance(node, And) else " OR "
    inner = op.join(_render(c, top=False) for c in node.children)
    return inner if top else f"({inner})"


# ---- Tokenizer ----


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str  # LPAREN, RPAREN, AND, OR, NOT, TERM, PHRASE
    text: str
    pos: int


def _tokenize(query: str) -> list[_Token]:
    tokens: list[_Token] = []
    i, n = 0, len(query)
    while i < n:
        ch = query[i]
        if ch.isspace():
            i += 1
        elif ch in "()":
            tokens.append(_Token("LPAREN" if ch == "(" else "RPAREN", ch, i))
            i += 1
        elif ch in _QUOTES:
            j = i + 1
            while j < n and query[j] not in _QUOTES:
                j += 1
            if j >= n:
                raise QuerySyntaxError("unterminated quote", i)
            tokens.append(_Token("PHRASE", query[i + 1 : j], i))
            i = j + 1
        else:
            j = i
            while (
                j < n
                and not query[j].isspace()
                and query[j] not in "()"
                and query[j] not in _QUOTES
            ):
                j += 1
            word = query[i:j]
            upper = word.upper()
            tokens.append(_Token(upper if upper in _OPERATORS else "TERM", word, i))
            i = j
    return tokens


def _make_seq(token: _Token) -> Seq:
    raw = token.text
    if token.kind == "TERM" and raw[0] in "-+":
        raise QuerySyntaxError(
            f"{raw!r}: a leading {raw[0]!r} is not an operator; use NOT to exclude a word",
            token.pos,
        )
    pieces = raw.split() if token.kind == "PHRASE" else [raw]
    words: list[Word] = []
    for piece in pieces:
        prefix = piece.endswith("*")
        body = piece[:-1] if prefix else piece
        if "*" in body:
            raise QuerySyntaxError(
                f"{piece!r}: the wildcard '*' is only allowed at the end of a word", token.pos
            )
        parts = normalize(body).split()
        if not parts:
            if prefix:
                raise QuerySyntaxError("'*' must follow at least one letter or digit", token.pos)
            continue
        words.extend(Word(p) for p in parts[:-1])
        words.append(Word(parts[-1], prefix))
    if not words:
        raise QuerySyntaxError(f"{raw!r} contains no letters or digits", token.pos)
    return Seq(tuple(words))


# ---- Parser (recursive descent) ----


class _Parser:
    def __init__(self, query: str) -> None:
        self._query = query
        self._tokens = _tokenize(query)
        self._i = 0
        self._depth = 0

    def parse(self) -> Node:
        if not self._tokens:
            raise QuerySyntaxError("the query is empty", 0)
        node = self._or_expr()
        extra = self._peek()
        if extra is not None:
            raise QuerySyntaxError(f"unexpected {extra.text!r}", extra.pos)
        return node

    def _peek(self) -> _Token | None:
        return self._tokens[self._i] if self._i < len(self._tokens) else None

    def _advance(self) -> _Token:
        token = self._tokens[self._i]
        self._i += 1
        return token

    def _or_expr(self) -> Node:
        children = [self._and_expr()]
        while (t := self._peek()) is not None and t.kind == "OR":
            self._advance()
            children.append(self._and_expr())
        return _combine(Or, children)

    def _and_expr(self) -> Node:
        children = [self._not_expr()]
        while (t := self._peek()) is not None and t.kind in _AND_CONTINUES:
            if t.kind == "AND":
                self._advance()
            # A binary NOT ("A NOT B") is left for _not_expr: A AND (NOT B).
            # Anything else that starts an operand is an implicit AND.
            children.append(self._not_expr())
        return _combine(And, children)

    def _not_expr(self) -> Node:
        t = self._peek()
        if t is not None and t.kind == "NOT":
            self._advance()
            return Not(self._not_expr())
        return self._atom()

    def _atom(self) -> Node:
        t = self._peek()
        if t is None:
            prev = self._tokens[-1]
            raise QuerySyntaxError(f"the query ends right after {prev.text!r}", len(self._query))
        if t.kind == "LPAREN":
            self._advance()
            self._depth += 1
            if self._depth > MAX_NESTING:
                raise QuerySyntaxError("too many nested parentheses", t.pos)
            node = self._or_expr()
            close = self._peek()
            if close is None or close.kind != "RPAREN":
                raise QuerySyntaxError("missing ')' for this '('", t.pos)
            self._advance()
            self._depth -= 1
            return node
        if t.kind in ("TERM", "PHRASE"):
            self._advance()
            return _make_seq(t)
        raise QuerySyntaxError(f"expected a word or phrase, found {t.text!r}", t.pos)


def _combine(kind: type[And] | type[Or], children: list[Node]) -> Node:
    if len(children) == 1:
        return children[0]
    flat: list[Node] = []
    for child in children:
        # (a AND b) AND c == a AND b AND c; flattening keeps the canonical form short.
        flat.extend(child.children if isinstance(child, kind) else [child])
    return kind(tuple(flat))


def parse_query(query: str) -> Node:
    """Parse a query string into an AST; raises QuerySyntaxError when malformed."""
    if len(query) > MAX_QUERY_LENGTH:
        raise QuerySyntaxError(
            f"the query is longer than {MAX_QUERY_LENGTH} characters", MAX_QUERY_LENGTH
        )
    return _Parser(query).parse()


# ---- Matching ----


def _seq_matcher(seq: Seq) -> Callable[[str, int], bool]:
    """Return ``match(text, end)`` testing ``seq`` against ``text[:end]``.

    Index texts are space-delimited normalized tokens, so whole words are found with
    plain substring search; only a wildcard before the last word needs a regex.
    """
    words = seq.words
    if not any(w.prefix for w in words[:-1]):
        needle = " " + " ".join(w.text for w in words) + ("" if words[-1].prefix else " ")
        return lambda text, end: text.find(needle, 0, end) != -1
    pattern = re.compile(
        " "
        + " ".join(re.escape(w.text) + ("[^ ]*" if w.prefix else "") for w in words)
        + ("" if words[-1].prefix else " ")
    )
    return lambda text, end: pattern.search(text, 0, end) is not None


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value if v]


class SearchIndex:
    """Normalized search text for a list of records, one string per record.

    Each string is `` <title> | <abstract> | <kw1> | <kw2> `` with tokens separated by
    single spaces. Every field scope is therefore a prefix of the string, and the ``|``
    separators stop phrases from running across fields or keywords.
    """

    def __init__(self, texts: list[str], title_end: list[int], abstract_end: list[int]) -> None:
        self.texts = texts
        self.title_end = title_end
        self.abstract_end = abstract_end

    def __len__(self) -> int:
        return len(self.texts)

    @classmethod
    def build(cls, records: Iterable[Mapping[str, Any]]) -> SearchIndex:
        texts: list[str] = []
        title_end: list[int] = []
        abstract_end: list[int] = []
        for rec in records:
            title = normalize(rec.get("title") or "")
            abstract = normalize(rec.get("abstract") or "")
            keywords = " | ".join(k for k in map(normalize, _as_list(rec.get("keywords"))) if k)
            head = f" {title} "
            middle = f"{head}| {abstract} "
            texts.append(f"{middle}| {keywords} ")
            title_end.append(len(head))
            abstract_end.append(len(middle))
        return cls(texts, title_end, abstract_end)

    def _ends(self, field: Field) -> Sequence[int] | None:
        if field == "title":
            return self.title_end
        if field == "title_abstract":
            return self.abstract_end
        if field == "title_abstract_keywords":
            return None
        raise ValueError(f"Unknown field {field!r}; expected one of {', '.join(FIELDS)}")

    def search(self, node: Node | None, field: Field = "title_abstract_keywords") -> list[int]:
        """Return the sorted indices of records matching ``node`` (all records if None)."""
        ends = self._ends(field)
        if node is None:
            return list(range(len(self.texts)))
        universe: set[int] | None = None
        leaf_hits: dict[Seq, set[int]] = {}

        def leaf(seq: Seq) -> set[int]:
            if seq not in leaf_hits:
                match = _seq_matcher(seq)
                texts = self.texts
                if ends is None:
                    leaf_hits[seq] = {i for i, t in enumerate(texts) if match(t, len(t))}
                else:
                    leaf_hits[seq] = {i for i, t in enumerate(texts) if match(t, ends[i])}
            return leaf_hits[seq]

        def evaluate(n: Node) -> set[int]:
            nonlocal universe
            if isinstance(n, Seq):
                return leaf(n)
            if isinstance(n, Not):
                if universe is None:
                    universe = set(range(len(self.texts)))
                return universe - evaluate(n.child)
            if isinstance(n, And):
                result = set(evaluate(n.children[0]))
                for child in n.children[1:]:
                    if not result:
                        break
                    result &= evaluate(child)
                return result
            result = set()
            for child in n.children:
                result |= evaluate(child)
            return result

        return sorted(evaluate(node))
