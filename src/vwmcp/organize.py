"""Rule-based sorting of objects onto classes and layers (server side, pure).

A rule is {"match": {...}, "class": "Target", "layer": "Target",
"by_class": bool}. For each target (class, layer, by_class) the first
matching rule that sets it wins - so a class rule and a layer rule can both
apply to one object. Match keys (all given keys must hold; lists mean "any
of"):

  class, layer, name, symbol   glob patterns, case-insensitive ("A-WALL*");
              an exact name always matches itself (also "A-WALL [EXIST]")
  kind        line, rect, polygon, polyline, arc, oval, text, group, symbol,
              dimension, locus, plugin, ...
  text        regular expression on a text's content (case-insensitive)
  pen, fill_color   "#rrggbb"
  fill        "none" | "solid" | "hatch" | "pattern"
  hatch, line_style   names (glob)
  line_weight_mm      number (compared in Vectorworks' unit, 1/1000 inch: 0.5 matches
                      the stored 0.508) or [min, max]
  area_m2, size_mm    [min, max] (area of closed outlines from their exact geometry;
                      size = larger bounding box side); null = open end
  closed      true/false (polygons/polylines)
  in_group    true: only objects inside groups; false: only top-level ones
  not         a nested match that must NOT hold
"""

from __future__ import annotations

import fnmatch
import re
from collections import Counter, defaultdict
from typing import Any

MATCH_KEYS = {"class", "layer", "name", "symbol", "kind", "text", "pen", "fill_color", "fill", "hatch",
              "line_style", "line_weight_mm", "area_m2", "size_mm", "closed", "in_group", "not"}
GEOMETRY_KEYS = {"text", "symbol", "closed", "area_m2"}  # need records with geometry
MM_PER_MIL = 0.0254
NUMERIC_KEYS = ("line_weight_mm", "area_m2", "size_mm")


class RuleError(ValueError):
    pass


def _list(v) -> list:
    return list(v) if isinstance(v, (list, tuple)) else [v]


def _glob(value: str, patterns) -> bool:
    v = (value or "").lower()
    return any(v == str(p).lower() or fnmatch.fnmatchcase(v, str(p).lower()) for p in _list(patterns))


def _range(v: float | None, r, eps: float = 0.0) -> bool:
    if v is None:
        return False
    if isinstance(r, (int, float)):
        return abs(v - r) <= eps
    lo, hi = (list(r) + [None, None])[:2]
    return (lo is None or v >= lo - eps) and (hi is None or v <= hi + eps)


def _lw_match(v: float | None, r) -> bool:
    """Line weights are whole mils in Vectorworks (0.5 mm is stored as 20 mils = 0.508 mm)."""
    if v is None:
        return False
    if isinstance(r, (int, float)):
        return round(v / MM_PER_MIL) == round(r / MM_PER_MIL)
    return _range(v, r, MM_PER_MIL / 2)


def _area_m2(rec: dict, mpu: float) -> float | None:
    if "_area_m2" not in rec:
        a = None
        try:
            from vwmcp.geom.shapes import from_record

            chord = 0.01 / mpu  # 0.01 mm: ovals and curves to well below any area tolerance
            sh = from_record(rec, chord=chord)
            if sh.is_area:
                a = sh.geometry(chord).area * (mpu / 1000.0) ** 2
        except Exception:  # noqa: BLE001 - objects without usable geometry don't match
            a = None
        rec["_area_m2"] = a
    return rec["_area_m2"]


def validate(rules: list[dict]) -> None:
    if not rules:
        raise RuleError("no rules")
    for i, r in enumerate(rules):
        if not isinstance(r, dict) or not isinstance(r.get("match", {}), dict):
            raise RuleError("rule %d: {'match': {...}, 'class': ..., 'layer': ...}" % i)
        if not (r.get("class") or r.get("layer") or r.get("by_class")):
            raise RuleError("rule %d has no target (class, layer or by_class)" % i)
        _check_match(r.get("match") or {}, "rule %d" % i)


def _check_match(m: dict, where: str) -> None:
    bad = set(m) - MATCH_KEYS
    if bad:
        raise RuleError("%s: unknown match keys %s (known: %s)" % (where, sorted(bad), ", ".join(sorted(MATCH_KEYS))))
    if "text" in m:
        for p in _list(m["text"]):
            try:
                re.compile(p)
            except re.error as e:
                raise RuleError("%s: text pattern %r: %s" % (where, p, e)) from e
    for k in NUMERIC_KEYS:
        if k in m:
            v = m[k]
            vals = [v] if k == "line_weight_mm" and isinstance(v, (int, float)) else (
                list(v) if isinstance(v, (list, tuple)) and len(v) == 2 else None)
            if vals is None or not all(x is None or (isinstance(x, (int, float)) and not isinstance(x, bool))
                                       for x in vals):
                raise RuleError("%s: %s must be %s[min, max] with numbers (or null for an open end)"
                                % (where, k, "a number or " if k == "line_weight_mm" else ""))
    for k in ("closed", "in_group"):
        if k in m and not isinstance(m[k], bool):
            raise RuleError("%s: %s must be true or false" % (where, k))
    if "not" in m:
        if not isinstance(m["not"], dict):
            raise RuleError("%s: 'not' must be a match object" % where)
        _check_match(m["not"], where + " not")


def needs_geometry(rules: list[dict]) -> bool:
    def walk(m):
        return bool(GEOMETRY_KEYS & set(m)) or ("not" in m and walk(m["not"]))
    return any(walk(r.get("match") or {}) for r in rules)


def matches(rec: dict, m: dict, mpu: float) -> bool:
    """Does the object record satisfy every condition of match m?"""
    attrs = rec.get("attrs") or {}
    for k, v in m.items():
        if k == "class" and not _glob(rec.get("class", ""), v):
            return False
        if k == "layer" and not _glob(rec.get("layer", ""), v):
            return False
        if k == "name" and not _glob(rec.get("name", ""), v):
            return False
        if k == "symbol" and not (rec.get("symbol") and _glob(rec["symbol"], v)):
            return False
        if k == "kind" and rec.get("kind") not in _list(v):
            return False
        if k == "text":
            t = rec.get("text")
            if t is None or not any(re.search(p, t, re.I) for p in _list(v)):
                return False
        if k in ("pen", "fill_color"):
            have = (attrs.get(k) or "").lower()
            if not have or have not in [str(x).lower() for x in _list(v)]:
                return False
        if k == "fill" and attrs.get("fill") not in _list(v):
            return False
        if k in ("hatch", "line_style"):
            have = attrs.get(k)
            if not have or not _glob(str(have), v):
                return False
        if k == "line_weight_mm" and not _lw_match(attrs.get("line_weight_mm"), v):
            return False
        if k == "area_m2" and not _range(_area_m2(rec, mpu), v):
            return False
        if k == "size_mm":
            b = rec.get("bbox")
            if not b or not _range(max(b[2] - b[0], b[3] - b[1]) * mpu, v):
                return False
        if k == "closed" and bool(rec.get("closed")) != bool(v):
            return False
        if k == "in_group" and bool(rec.get("parent")) != bool(v):
            return False
        if k == "not" and matches(rec, v, mpu):
            return False
    return True


def _signature(rec: dict) -> tuple:
    a = rec.get("attrs") or {}
    return (rec.get("kind"), rec.get("class"), rec.get("layer"), a.get("pen"), a.get("line_weight_mm"),
            a.get("line_style"))


def plan(records: list[dict], rules: list[dict], mpu: float, include_groups: bool = True,
         sample: int = 5) -> dict[str, Any]:
    """What the rules would change. For each target (class, layer, by_class)
    the first matching rule that sets it wins. Objects already where the
    rules put them count as 'unchanged'. Layer moves only apply to top-level
    objects (a group moves as a whole)."""
    validate(rules)
    per_rule = [{"rule": i, "matched": 0, "changes": 0, "sample": []} for i in range(len(rules))]
    groups: dict[tuple, list[str]] = defaultdict(list)
    unmatched: list[dict] = []
    skipped_layer: list[str] = []
    unchanged = 0
    for rec in records:
        if rec.get("parent") and not include_groups:
            continue
        target: dict[str, Any] = {}
        hit: list[int] = []
        for i, r in enumerate(rules):
            wants = [k for k in ("class", "layer", "by_class") if r.get(k) and k not in target]
            if not wants or not matches(rec, r.get("match") or {}, mpu):
                continue
            for k in wants:
                target[k] = (r[k], i)
            hit.append(i)
            if len(target) == 3:
                break
        if not hit:
            unmatched.append(rec)
            continue
        for i in hit:
            per_rule[i]["matched"] += 1
        cls = target["class"][0] if "class" in target and target["class"][0] != rec.get("class") else None
        lay = target["layer"][0] if "layer" in target and target["layer"][0] != rec.get("layer") else None
        if lay and rec.get("parent"):
            skipped_layer.append(rec["uuid"])  # inside a group: the group moves, not the part
            lay = None
        bc = "by_class" in target and not _all_by_class(rec)
        if cls or lay or bc:
            groups[(cls, lay, bc)].append(rec["uuid"])
            for k, changed in (("class", cls), ("layer", lay), ("by_class", bc)):
                if changed:
                    pr = per_rule[target[k][1]]
                    pr["changes"] += 1
                    if len(pr["sample"]) < sample and rec["uuid"] not in pr["sample"]:
                        pr["sample"].append(rec["uuid"])
        else:
            unchanged += 1
    sig = Counter(_signature(r) for r in unmatched)
    top = []
    for (kind, cls, lay, pen, lw, ls), n in sig.most_common(15):
        top.append({"count": n, "kind": kind, "class": cls, "layer": lay, "pen": pen, "line_weight_mm": lw,
                    "line_style": ls})
    moves = [{"uuids": u, "class": k[0], "layer": k[1], "by_class": k[2]} for k, u in groups.items()]
    return {
        "objects": len(records),
        "to_change": sum(len(m["uuids"]) for m in moves),
        "unchanged": unchanged,
        "unmatched": len(unmatched),
        "rules": per_rule,
        "unmatched_groups": top,
        "layer_moves_skipped_in_groups": len(skipped_layer),
        "_moves": moves,
    }


def _all_by_class(rec: dict) -> bool:
    bc = (rec.get("attrs") or {}).get("by_class")
    if bc is None:  # groups, symbols: no own attributes
        return True
    return all(bc.values())


def class_map_rules(class_map: dict[str, str]) -> list[dict]:
    """{"A-WALL*": "Wand-Tragend", ...} -> rules (in the given order)."""
    return [{"match": {"class": src}, "class": dst} for src, dst in class_map.items()]


def layer_map_rules(layer_map: dict[str, str]) -> list[dict]:
    return [{"match": {"layer": src}, "layer": dst} for src, dst in layer_map.items()]
