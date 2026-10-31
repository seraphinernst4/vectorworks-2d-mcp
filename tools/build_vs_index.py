#!/usr/bin/env python3
"""Build Keystone's vs.* signature index (src/vwmcp/data/vs_index.json).

Based on tools/build_vs_index.py from vwx-mcp (github.com/vicquick/vwx-mcp,
MIT, (c) 2026 vicquick), extended with parameter *types*:

    python tools/build_vs_index.py <vs.py> [--vwx <vwx vs_index.json>]
        [--versions "<Functions By Version.md>"] [--out <file>]

Sources:
- <vs.py>: the Python stub from github.com/Vectorworks/developer-scripting
  (Python/pages/files/vs.py). Every function there lists its parameters with
  a type comment (``p1, # POINT - ...``) and its VectorScript signature
  (``PROCEDURE Rect(p1X, p1Y:REAL; p2X, p2Y:REAL);``).
- --vwx: vwx-mcp's vs_index.json (MIT, (c) vicquick) for category and the
  one-line description, used where the stub has none.
- --versions: "Function Reference/Functions By Version.md" from the same
  repository, to note functions that only exist from a given version on.

Output, per function name:
    a   parameters "name:TYPE, ..." (Python order)
    t   type codes for the argument check in the bridge, one per Python
        parameter, or null when the function can't be checked safely
        (variadic, ANY/ARRAY parameters, stub and VectorScript disagree)
    r   return type ("" for procedures)
    c   category, d one-line description, v first version ("2027"), if known

Type codes: H handle, R real, I integer/longint, B boolean, S string/char/
criteria, P point (tuple or 2 numbers), Q 3D point (tuple or 3 numbers),
V vector (like Q), C colour (one (r, g, b) tuple; three loose numbers are a
silent no-op in Vectorworks), F callback.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from datetime import date
from pathlib import Path

CODES = {
    "HANDLE": "H",
    "REAL": "R",
    "INTEGER": "I",
    "LONGINT": "I",
    "BOOLEAN": "B",
    "STRING": "S",
    "CHAR": "S",
    "DYNARRAY[] OF CHAR": "S",
    "CRITERIA": "S",
    "POINT": "P",
    "POINT3D": "Q",
    "VECTOR": "V",
    "COLOR": "C",
    "PROCEDURE": "F",
}
WIDTH = {"P": 2, "Q": 3, "V": 3, "C": 3}  # values each code takes in VectorScript


def _param_types(lines: list[str], node: ast.FunctionDef) -> list[tuple[str, str]]:
    """(name, TYPE) per parameter, from the comments in the def header."""
    names = [a.arg for a in node.args.args]
    end = node.body[0].lineno - 1 if node.body else node.lineno
    header = lines[node.lineno - 1:end]
    found: dict[str, str] = {}
    for line in header:
        m = re.match(r"\s*(?:def \w+\()?\s*(\w+)\s*,?\s*#\s*([A-Za-z_][\w\[\]()/ ]*?)\s*(?:-|$)", line)
        if m and m.group(1) in names and m.group(1) not in found:
            t = re.sub(r"\(.*?\)", "", m.group(2))  # "REAL (Coordinate)" -> "REAL"
            t = re.sub(r"(?i)^\s*in/out\s+", "IN/OUT ", t)
            found[m.group(1)] = re.sub(r"\s+", " ", t).strip().upper()
    return [(n, found.get(n, "")) for n in names]


def _vs_inputs(sig: str) -> int | None:
    """Number of input values in a VectorScript signature, None if variadic."""
    m = re.search(r"\((.*)\)", sig)
    if not m:
        return 0
    body = m.group(1)
    if "..." in body:
        return None
    n = 0
    for group in body.split(";"):
        group = group.strip()
        if not group or group.upper().startswith("VAR "):
            continue
        names, _, typ = group.partition(":")
        typ = re.sub(r"\(.*?\)", "", typ).strip().upper()
        width = {"POINT": 2, "POINT3D": 3, "VECTOR": 3}.get(typ, 1)
        n += width * len([x for x in names.split(",") if x.strip()])
    return n


def _versions(path: Path | None) -> dict[str, str]:
    if not path or not path.is_file():
        return {}
    out: dict[str, str] = {}
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        h = re.match(r"##\s+Vectorworks\s+([\d.]+)", line)
        if h:
            current = h.group(1)
            continue
        if current:
            for name in re.findall(r"\[(\w+)\]\(Functions/", line):
                out.setdefault(name, current)
    return out


def build(stub: Path, vwx: Path | None = None, versions: Path | None = None) -> dict:
    src = stub.read_text(encoding="utf-8", errors="ignore")
    lines = src.splitlines()
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(src)
    extra = json.loads(vwx.read_text(encoding="utf-8")) if vwx and vwx.is_file() else {}
    since = _versions(versions)
    funcs: dict[str, dict] = {}
    stats = {"functions": 0, "checked": 0}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        doc = ast.get_docstring(node) or ""
        params = _param_types(lines, node)
        vsig = re.search(r"VectorScript:\s*(.+)", doc)
        cat = re.search(r"Category:\s*(.+)", doc)
        ret = ""
        rm = re.search(r"return\s*\(?\s*'([^']+)'", ast.get_source_segment(src, node) or "")
        if rm:
            ret = rm.group(1)
        summary = ""
        for line in doc.splitlines():
            s = line.strip()
            if s and not s.startswith(("Python:", "VectorScript:", "Category:")):
                summary = s
                break
        # "in/out" parameters are passed in Python and are VAR in VectorScript
        inout = [t[len("IN/OUT "):] for _, t in params if t.startswith("IN/OUT ")]
        params = [(n, t[len("IN/OUT "):] if t.startswith("IN/OUT ") else t) for n, t in params]
        codes = [CODES.get(t) for _, t in params]
        inputs = _vs_inputs(vsig.group(1)) if vsig else None
        checkable = (
            all(codes)
            and inputs is not None
            and sum(WIDTH.get(c, 1) for c in codes)
            == inputs + sum(WIDTH.get(CODES.get(t, ""), 1) for t in inout)
        )
        x = extra.get(node.name, {})
        entry = {
            "a": ", ".join(f"{n}:{t or '?'}" for n, t in params),
            "t": "".join(codes) if checkable else None,
            "r": ret,
            "c": (cat.group(1).strip() if cat else "") or x.get("cat", ""),
            "d": (summary or x.get("doc", ""))[:200],
        }
        if node.name in since:
            entry["v"] = since[node.name]
        funcs[node.name] = entry
        stats["functions"] += 1
        stats["checked"] += bool(checkable)
    # functions only the vwx index knows (kept for lookup, never checked)
    for name, x in extra.items():
        if name not in funcs:
            funcs[name] = {"a": ", ".join(x.get("args", [])), "t": None, "r": x.get("ret", ""),
                           "c": x.get("cat", ""), "d": x.get("doc", "")[:200]}
    return {
        "_meta": {
            "built": date.today().isoformat(),
            "source": "Vectorworks developer-scripting vs.py stub (types, VectorScript signatures); "
                      "descriptions/categories partly from vwx-mcp vs_index.json (MIT, (c) vicquick)",
            **stats,
        },
        "f": funcs,
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("stub", type=Path)
    ap.add_argument("--vwx", type=Path)
    ap.add_argument("--versions", type=Path)
    ap.add_argument("--out", type=Path,
                    default=Path(__file__).resolve().parents[1] / "src/vwmcp/data/vs_index.json")
    args = ap.parse_args(argv)
    idx = build(args.stub, args.vwx, args.versions)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(idx, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                        encoding="utf-8")
    m = idx["_meta"]
    print(f"{m['functions']} functions, {m['checked']} with argument check -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
