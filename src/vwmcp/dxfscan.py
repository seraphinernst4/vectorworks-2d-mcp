"""Count what a DXF file contains (server side, stdlib only) - to tell after
an import what Vectorworks left out. DWG is a binary format: only its
version is read.

Known losses when Vectorworks 2026 imports DWG/DXF (tested with Rhino 8
exports): rational splines (circles, ellipses, arcs and conic curves that
Rhino writes as NURBS) are dropped; in R12 DXF every spline is dropped.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

DWG_VERSIONS = {"AC1009": "R12", "AC1012": "R13", "AC1014": "R14", "AC1015": "2000", "AC1018": "2004",
                "AC1021": "2007", "AC1024": "2010", "AC1027": "2013", "AC1032": "2018"}
SUB_ENTITIES = {"VERTEX", "SEQEND", "ATTRIB"}


def _pairs(lines: list[str]):
    for i in range(0, len(lines) - 1, 2):
        yield lines[i].strip(), lines[i + 1].rstrip("\r\n")


def scan(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    head = p.read_bytes()[:32]
    if p.suffix.lower() == ".dwg":
        ver = head[:6].decode("ascii", "replace")
        return {"format": "DWG", "version": DWG_VERSIONS.get(ver, ver), "countable": False}
    if head.startswith(b"AutoCAD Binary DXF"):
        return {"format": "DXF (binary)", "countable": False}
    raw = p.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", "replace")
    lines = text.splitlines()
    section = None
    expect_name = False
    header_var = None
    out: dict[str, Any] = {"format": "DXF", "countable": True}
    ents: Counter = Counter()
    blocks = 0
    splines = rational = 0
    cur = None
    in_block_def = False
    for code, val in _pairs(lines):
        if code == "0":
            if cur == "SPLINE":
                splines += 1
            cur = None
            if val == "SECTION":
                expect_name = True
                continue
            if val == "ENDSEC":
                section = None
                continue
            if section == "BLOCKS":
                if val == "BLOCK":
                    in_block_def = True
                    blocks += 1
                elif val == "ENDBLK":
                    in_block_def = False
                continue
            if section == "ENTITIES" and val not in SUB_ENTITIES:
                ents[val] += 1
                cur = val
            continue
        if expect_name and code == "2":
            section = val.strip()
            expect_name = False
            continue
        if section == "HEADER":
            if code == "9":
                header_var = val.strip()
            elif header_var == "$ACADVER" and code == "1":
                out["version"] = DWG_VERSIONS.get(val.strip(), val.strip())
            elif header_var == "$INSUNITS" and code == "70":
                out["insunits"] = int(val.strip() or 0)
            continue
        if cur == "SPLINE" and code == "70":
            try:
                if int(val.strip()) & 4:
                    rational += 1
            except ValueError:
                pass
    if cur == "SPLINE":
        splines += 1
    out["entities"] = dict(ents)
    out["total"] = sum(ents.values())
    out["splines"] = splines
    out["rational_splines"] = rational
    out["block_definitions"] = blocks
    _ = in_block_def
    return out


def expected_losses(src: dict[str, Any]) -> dict[str, Any]:
    """What Vectorworks is known to leave out of this source."""
    if not src.get("countable"):
        return {}
    if src.get("version") == "R12":
        lost = src.get("splines", 0)
        why = "R12-DXF: Vectorworks übernimmt keine Splines"
    else:
        lost = src.get("rational_splines", 0)
        why = "gewichtete Splines (Kreise, Ellipsen, Bögen als NURBS) übernimmt Vectorworks nicht"
    return {"objects": lost, "why": why} if lost else {}
