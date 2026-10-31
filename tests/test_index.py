"""Sanity of the bundled vs.* signature index and its builder."""

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = json.loads((ROOT / "src" / "vwmcp" / "data" / "vs_index.json").read_text(encoding="utf-8"))["f"]


def _builder():
    spec = importlib.util.spec_from_file_location("build_vs_index", ROOT / "tools" / "build_vs_index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _checked_params():
    for name, e in INDEX.items():
        if e.get("t") is None or not e["a"]:
            continue
        for p in e["a"].split(", "):
            n, _, t = p.partition(":")
            yield name, n, t


def test_codes_match_parameter_count():
    for name, e in INDEX.items():
        if e.get("t") is None:
            continue
        n = len(e["a"].split(", ")) if e["a"] else 0
        assert len(e["t"]) == n, name
        assert set(e["t"]) <= set("HRIBSPQVCF"), name


def test_callbacks_only_where_a_callback_is_meant():
    bad = [(f, n) for f, n, t in _checked_params() if t == "PROCEDURE" and
           not re.search(r"(?i)(callback|actionfunc|func$|^p$)", n)]
    assert not bad
    assert INDEX["ForEachObject"]["t"] == "FS"
    assert INDEX["PDF_GetPageCount"]["t"] is None


def test_text_parameters_are_not_checked_as_numbers():
    bad = [(f, n, t) for f, n, t in _checked_params() if t in ("LONGINT", "INTEGER", "REAL", "BOOLEAN")
           and re.search(r"(?i)(name|cstr)$", n)]
    assert not bad
    assert INDEX["SetSkylight"]["t"] is None


def test_core_2d_functions_are_checked():
    for f, t in {"Rect": "PP", "ArcByCenter": "RRRRR", "SetFillBack": "HC", "HMove": "HRR",
                 "BeginSym": "S", "Symbol": "SPR", "LinearDim": "PPRIIIR"}.items():
        assert INDEX[f]["t"] == t, f


def test_builder_code_rules():
    b = _builder()
    assert b._code("callback", "PROCEDURE") == "F"
    assert b._code("actionFunc", "PROCEDURE") == "F"
    assert b._code("inFilePath", "PROCEDURE") is None
    assert b._code("symName", "LONGINT") is None
    assert b._code("boxText", "BOOLEAN") == "B"
    assert b._code("h", "HANDLE") == "H"
