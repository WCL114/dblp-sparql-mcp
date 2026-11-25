"""Diskcache-backed TTL cache for SPARQL results, keyed by the query text.

The key is derived from the normalized query alone (no client objects), so it is
the same in every process. The cache lives in ``DBLP_SPARQL_MCP_CACHE_DIR``
(default ``~/.cache/dblp-sparql-mcp``) and is bypassed when
``DBLP_SPARQL_MCP_NO_CACHE=1``.
"""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from typing import Any

import diskcache

# Bump to orphan every existing entry after a change in what is stored.
KEY_VERSION = 1
_SIZE_LIMIT = 1 << 30  # 1 GiB

_cache: diskcache.Cache | None = None
_cache_lock = threading.Lock()


def enabled() -> bool:
    return os.environ.get("DBLP_SPARQL_MCP_NO_CACHE") != "1"


def cache_dir() -> Path:
    configured = os.environ.get("DBLP_SPARQL_MCP_CACHE_DIR")
    return Path(configured) if configured else Path.home() / ".cache" / "dblp-sparql-mcp"


def _store() -> diskcache.Cache:
    """Open the cache on first use, so importing this module touches no files."""
    global _cache
    with _cache_lock:
        if _cache is None:
            directory = cache_dir()
            directory.mkdir(parents=True, exist_ok=True)
            _cache = diskcache.Cache(str(directory), size_limit=_SIZE_LIMIT)
        return _cache


def make_key(query: str) -> str:
    """Key for a query: whitespace runs collapse, so layout changes do not matter.

    Runs inside string literals collapse too; the generated literals never contain
    them (words are joined by single spaces, venue names are whitespace-normalized).
    """
    normalized = " ".join(query.split())
    return hashlib.sha256(f"v{KEY_VERSION}\n{normalized}".encode()).hexdigest()


def get_value(key: str) -> Any:
    """Return the cached value, or None on a miss or when caching is disabled."""
    if not enabled():
        return None
    return _store().get(key, default=None)


def set_value(key: str, value: Any, ttl_seconds: float) -> None:
    if enabled():
        _store().set(key, value, expire=ttl_seconds)
