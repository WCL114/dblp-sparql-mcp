"""Write a full result set to a JSON or RIS file instead of returning it through the chat.

RIS files import into Zotero, EndNote, and most other reference managers. Existing
files are never overwritten.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .schemas import Paper

FORMATS = ("json", "ris")

_RIS_TYPES = {
    "conference": "CPAPER",
    "journal": "JOUR",
    "preprint": "UNPB",
    "book": "BOOK",
    "incollection": "CHAP",
    "editorship": "EDBOOK",
    "data": "DATA",
}


def _exists_message(target: Path) -> str:
    return f"{target} already exists; choose a new file name (existing files are never overwritten)"


def prepare_target(path: str) -> tuple[Path, str]:
    """Validate an export path up front, before any data is fetched; return (path, format)."""
    target = Path(path).expanduser()
    if not target.is_absolute():
        raise ValueError(f"export_path must be an absolute path, got {path!r}")
    fmt = target.suffix.lower().lstrip(".")
    if fmt not in FORMATS:
        raise ValueError(f"export_path must end with .json or .ris, got {target.name!r}")
    if not target.parent.is_dir():
        raise ValueError(f"The directory {target.parent} does not exist")
    if target.exists():
        raise FileExistsError(_exists_message(target))
    return target, fmt


def write_export(
    target: Path,
    fmt: str,
    papers: Sequence[Paper],
    meta: Mapping[str, Any],
) -> dict[str, Any]:
    """Write ``papers`` to a path checked by ``prepare_target``."""
    if fmt == "json":
        payload = {"meta": dict(meta), "results": [p.model_dump() for p in papers]}
        text, newline = json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "\n"
    else:
        # CRLF line ends, as in the original RIS specification; LF-only readers cope too.
        text, newline = render_ris(papers), "\r\n"
    try:
        # Exclusive creation: a file that appeared since prepare_target is not clobbered.
        with target.open("x", encoding="utf-8", newline=newline) as fh:
            fh.write(text)
    except FileExistsError:
        raise FileExistsError(_exists_message(target)) from None
    return {"path": str(target), "format": fmt, "count": len(papers)}


def render_ris(papers: Sequence[Paper]) -> str:
    return "".join(_ris_record(p) + "\n" for p in papers)


def _ris_record(p: Paper) -> str:
    venue_tag = "JO" if p.type == "journal" else "T2"
    fields: list[tuple[str, str | None]] = [
        ("TY", _RIS_TYPES.get(p.type, "GEN")),
        ("TI", p.title),
    ]
    fields += [("AU", author) for author in p.authors]
    fields += [
        ("PY", str(p.year) if p.year else None),
        (venue_tag, p.venue),
        ("DO", p.doi),
        ("UR", p.url or p.dblp_url),
        ("N1", f"dblp record: {p.dblp_url} (type: {p.type})"),
        ("DB", "dblp"),
        ("ID", p.key),
    ]
    lines = [f"{tag}  - {_one_line(value)}" for tag, value in fields if value]
    return "\n".join([*lines, "ER  - "]) + "\n"


def _one_line(value: str) -> str:
    return " ".join(value.split())
