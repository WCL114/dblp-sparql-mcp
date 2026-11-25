"""Tests for JSON and RIS export."""

from __future__ import annotations

from pathlib import Path

import pytest

from dblp_sparql_mcp.export import prepare_target, render_ris, write_export
from dblp_sparql_mcp.schemas import Paper

PAPER = Paper(
    key="conf/uss/LiuJGJG24",
    title="Formalizing and Benchmarking Prompt Injection Attacks and Defenses",
    authors=["Yupei Liu", "Yuqi Jia"],
    venue="USENIX Security Symposium",
    year=2024,
    type="conference",
    url="https://www.usenix.org/conference/usenixsecurity24/presentation/liu-yupei",
    dblp_url="https://dblp.org/rec/conf/uss/LiuJGJG24",
)
ARTICLE = Paper(
    key="journals/tifs/X25",
    title="A Journal Paper",
    type="journal",
    venue="IEEE Trans. Inf. Forensics Secur.",
    doi="10.1109/TIFS.2025.1",
    dblp_url="https://dblp.org/rec/journals/tifs/X25",
)


def test_ris_record() -> None:
    lines = render_ris([PAPER, ARTICLE]).splitlines()
    assert lines[:4] == [
        "TY  - CPAPER",
        "TI  - Formalizing and Benchmarking Prompt Injection Attacks and Defenses",
        "AU  - Yupei Liu",
        "AU  - Yuqi Jia",
    ]
    assert "T2  - USENIX Security Symposium" in lines
    assert "ID  - conf/uss/LiuJGJG24" in lines
    assert "TY  - JOUR" in lines and "JO  - IEEE Trans. Inf. Forensics Secur." in lines
    assert "DO  - 10.1109/TIFS.2025.1" in lines
    assert "UR  - https://dblp.org/rec/journals/tifs/X25" in lines  # no landing page
    assert lines.count("ER  - ") == 2


def test_ris_uses_crlf(tmp_path: Path) -> None:
    target, fmt = prepare_target(str(tmp_path / "a.ris"))
    write_export(target, fmt, [PAPER], {})
    assert target.read_bytes().count(b"\r\n") == target.read_bytes().count(b"\n")


def test_existing_files_are_never_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "a.json"
    target.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError, match="already exists"):
        prepare_target(str(target))
    with pytest.raises(FileExistsError):
        write_export(target, "json", [PAPER], {})
    assert target.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize(
    ("name", "message"), [("a.txt", ".json or .ris"), ("missing/a.ris", "does not exist")]
)
def test_bad_targets(tmp_path: Path, name: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        prepare_target(str(tmp_path / name))
