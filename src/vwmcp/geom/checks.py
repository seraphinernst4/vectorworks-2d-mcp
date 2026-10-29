"""Drawing quality checks for exact architectural 2D drawings.

Input: records from vwlib2d.read_objects. Output: a list of issues with
location, measured value and a suggested fix. Distances are measured on the
exact geometry (true arcs), areas on finely sampled geometry.
"""

from __future__ import annotations

import math
from collections import defaultdict

from shapely import STRtree
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from vwmcp.geom import curves as C
from vwmcp.geom.shapes import Shape, from_records, polygonal, shapely_parts, units_tolerances

ALL_CHECKS = (
    "overlap", "duplicate", "near", "gap", "self_intersection", "open",
    "double_line", "vertices", "tiny", "classes", "attributes", "approx",
)
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
DIM_CLASS_HINTS = ("bemaß", "bemass", "dimension", "maß", "mass")


def _fmt(v: float) -> str:
    """German number formatting without losing precision for small values."""
    if v == 0:
        return "0"
    if abs(v) >= 100:
        s = f"{v:.1f}"
    elif abs(v) >= 1:
        s = f"{v:.3f}".rstrip("0").rstrip(".")
    else:
        s = f"{v:.4g}"
    return s.replace(".", ",")


class Report:
    def __init__(self, tol: dict, units: dict):
        self.t = tol
        self.units = units
        self.items: list[dict] = []

    def add(self, kind, severity, uuids, at, message, **extra):
        it = {"kind": kind, "severity": severity, "uuids": list(dict.fromkeys(uuids)),
              "at": [at[0], at[1]] if at is not None else None, "message": message}
        it.update(extra)
        self.items.append(it)
        return it

    def mm(self, d: float) -> float:
        return d * self.t["mpu"]


def _thickness(g) -> float:
    """Width of the rectangle with the same area and perimeter (exact for
    rectangular slivers, a good estimate otherwise)."""
    a, half = g.area, g.length / 2.0
    if half <= 0:
        return 0.0
    disc = half * half - 4.0 * a
    if disc <= 0:
        return half / 2.0  # square-ish
    return (half - math.sqrt(disc)) / 2.0


def _rep_point(g):
    try:
        p = g.representative_point()
        return (p.x, p.y)
    except Exception:  # noqa: BLE001
        c = g.centroid
        return (c.x, c.y)


def _ring_coords(g, limit=400):
    out = []
    for part in shapely_parts(g):
        if isinstance(part, Polygon):
            cs = list(part.exterior.coords)
            step = max(1, len(cs) // limit)
            out.append([[x, y] for x, y in cs[::step]])
    return out


def _class_matcher(patterns):
    pats = [p.lower() for p in (patterns or [])]

    def ignored(cls: str) -> bool:
        c = (cls or "").lower()
        for p in pats:
            if p.endswith("*") and c.startswith(p[:-1]):
                return True
            if c == p:
                return True
        return False
    return ignored


def run_checks(records: list[dict], units: dict, tolerance_mm: float = 1.0,
               checks=None, none_class: str = "", max_issues: int = 200,
               compare: str = "layer", ignore_classes=None) -> dict:
    """compare: which objects are checked against each other for overlaps,
    gaps and near misses: 'layer' (same layer, default), 'class' (same
    class and layer) or 'all'. ignore_classes: class names (or 'Prefix*')
    left out of geometric checks, e.g. furniture or hatch classes that
    overlap on purpose."""
    ignored = _class_matcher(ignore_classes)
    records_all = records
    records = [r for r in records if not ignored(r.get("class", ""))]

    def comparable(a: Shape, b: Shape) -> bool:
        if compare == "all":
            return True
        if a.layer != b.layer:
            return False
        return compare != "class" or a.cls == b.cls

    t = units_tolerances(units, tolerance_mm)
    checks = set(checks or ALL_CHECKS)
    rep = Report(t, units)
    shapes = from_records(records, t["chord"])
    by_uuid = {s.uuid: s for s in shapes}
    areas = [s for s in shapes if s.is_area]
    lines = [s for s in shapes if s.is_line]
    geo = [s for s in shapes if s.rings]
    chord, eps, tol, thin = t["chord"], t["eps"], t["tol"], t["min_thick"]

    # ---- validity -----------------------------------------------------------
    for s in areas:
        if not s.is_valid(chord) and "self_intersection" in checks:
            from shapely.validation import explain_validity
            raw = Polygon(C.discretize(s.rings[0], chord))
            why = explain_validity(raw)
            at = None
            if "[" in why:
                try:
                    xy = why[why.index("[") + 1: why.index("]")].split()
                    at = (float(xy[0]), float(xy[1]))
                except (ValueError, IndexError):
                    at = None
            rep.add("self_intersection", "error", [s.uuid], at or _rep_point(s.geometry(chord)),
                    "Umriss überschneidet sich selbst", fix={"action": "manual"})

    # ---- area pairs -----------------------------------------------------------
    area_geoms = [s.geometry(chord) for s in areas]
    tree = STRtree(area_geoms) if area_geoms else None
    pair_done = set()
    if tree is not None and ({"overlap", "duplicate"} & checks):
        for i, s in enumerate(areas):
            gi = area_geoms[i]
            if gi.is_empty:
                continue
            for j in tree.query(gi):
                j = int(j)
                if j <= i:
                    continue
                o = areas[j]
                if not comparable(s, o):
                    continue
                gj = area_geoms[j]
                if gj.is_empty or not gi.intersects(gj):
                    continue
                inter = polygonal(gi.intersection(gj))
                if inter.is_empty or inter.area <= 0:
                    continue
                th = _thickness(inter)
                if th <= thin:
                    continue
                pair_done.add((s.uuid, o.uuid))
                sym = gi.symmetric_difference(gj)
                if sym.is_empty or _thickness(polygonal(sym)) <= thin and sym.area <= thin * max(gi.length, gj.length):
                    if "duplicate" in checks:
                        rep.add("duplicate", "error", [s.uuid, o.uuid], _rep_point(inter),
                                "Doppelte Fläche (deckungsgleich)",
                                fix={"action": "duplicates"})
                    continue
                if "overlap" not in checks:
                    continue
                inside = None
                if gj.buffer(thin).contains(gi):
                    inside = (s, o)
                elif gi.buffer(thin).contains(gj):
                    inside = (o, s)
                if inside:
                    msg = "Fläche liegt komplett in einer anderen (überlagerte Füllflächen)"
                else:
                    msg = "Flächen überlappen sich"
                small = th <= tol
                if small:
                    msg += " (nur %s mm, Ungenauigkeit)" % _fmt(rep.mm(th))
                rep.add("overlap", "error", [s.uuid, o.uuid], _rep_point(inter), msg,
                        thickness_mm=rep.mm(th), area=inter.area,
                        region=_ring_coords(inter),
                        fix={"action": "snap" if small else "overlaps"})

    # ---- exact near misses (vertex vs. outline) ----------------------------------
    if ({"near", "gap"} & checks) and geo:
        g_all = [s.geometry(chord) for s in geo]
        gtree = STRtree(g_all)
        seen = set()
        near_pairs = defaultdict(list)
        for s in geo:
            for p in s.anchors():
                hits = gtree.query(Point(p).buffer(tol))
                best = None
                for j in hits:
                    o = geo[int(j)]
                    if o.uuid == s.uuid or not comparable(s, o):
                        continue
                    d, cp = o.distance_to(p)
                    if d > tol:
                        continue
                    # nearest exact vertex of the other object
                    vd, vp = math.inf, None
                    for q in o.anchors():
                        dq = math.hypot(q[0] - p[0], q[1] - p[1])
                        if dq < vd:
                            vd, vp = dq, q
                    if d <= eps:
                        if eps < vd <= tol:
                            cand = ("node", vd, o, vp)
                        else:
                            continue
                    else:
                        cand = ("node", vd, o, vp) if vd <= tol else ("edge", d, o, cp)
                    if best is None or cand[1] < best[1]:
                        best = cand
                if best is None:
                    continue
                kind, d, o, target = best
                key = (s.uuid, o.uuid, round(p[0] / tol), round(p[1] / tol))
                rkey = (o.uuid, s.uuid, round(target[0] / tol), round(target[1] / tol))
                if key in seen or rkey in seen:
                    continue
                seen.add(key)
                pair = tuple(sorted((s.uuid, o.uuid)))
                near_pairs[pair].append({"uuid": s.uuid, "other": o.uuid, "point": [p[0], p[1]],
                                         "target": [target[0], target[1]], "target_kind": kind,
                                         "distance": d})
        for pair, moves in near_pairs.items():
            a, b = by_uuid[pair[0]], by_uuid[pair[1]]
            touching = a.geometry(chord).distance(b.geometry(chord)) <= eps
            dmax = max(m["distance"] for m in moves)
            where = " (%d Stellen)" % len(moves) if len(moves) > 1 else ""
            if not touching:
                if "gap" not in checks:
                    continue
                kind_out, msg = "gap", "Spalt %s mm zwischen Objekten%s" % (_fmt(rep.mm(dmax)), where)
            else:
                if "near" not in checks:
                    continue
                kind_out = "near"
                if all(m["target_kind"] == "node" for m in moves):
                    msg = "Punkte liegen bis %s mm nebeneinander statt aufeinander%s" % (_fmt(rep.mm(dmax)), where)
                else:
                    msg = "Punkt liegt bis %s mm neben der Kante statt darauf%s" % (_fmt(rep.mm(dmax)), where)
            for m in moves:
                m["distance_mm"] = rep.mm(m.pop("distance"))
            rep.add(kind_out, "error", list(pair), moves[0]["point"], msg, distance_mm=rep.mm(dmax),
                    moves=moves, fix={"action": "snap"})

    # ---- thin holes inside unions (gaps between neighbours) ---------------------------
    groups = defaultdict(list)
    for i, s in enumerate(areas):
        key = "*" if compare == "all" else (s.layer if compare == "layer" else s.layer + "\t" + s.cls)
        if not area_geoms[i].is_empty:
            groups[key].append(area_geoms[i])
    unions = []
    if "gap" in checks:
        for gs in groups.values():
            if len(gs) > 1:
                try:
                    unions.append(unary_union(gs))
                except Exception:  # noqa: BLE001
                    pass
    for u in unions:
        for part in shapely_parts(u):
            if not isinstance(part, Polygon):
                continue
            for ring in part.interiors:
                hole = Polygon(ring)
                th = _thickness(hole)
                if thin < th <= 2 * tol:
                    near = [areas[int(j)].uuid for j in tree.query(hole.buffer(thin))
                            if area_geoms[int(j)].distance(hole) <= thin] if tree is not None else []
                    rep.add("gap", "error", near, _rep_point(hole),
                            "Lücke zwischen Flächen (%s mm breit)" % _fmt(rep.mm(th)),
                            thickness_mm=rep.mm(th), area=hole.area, region=_ring_coords(hole),
                            fix={"action": "snap"})

    # ---- double lines -----------------------------------------------------------------
    if "double_line" in checks and lines:
        outl = [(s, s.geometry(chord) if s.is_line else s.geometry(chord).boundary) for s in geo]
        otree = STRtree([g for _, g in outl])
        for s in lines:
            gl = s.geometry(chord)
            if gl.is_empty:
                continue
            for j in otree.query(gl.buffer(thin)):
                o, go = outl[int(j)]
                if o.uuid == s.uuid or not comparable(s, o) or (o.is_line and o.order < s.order and (o.uuid, s.uuid) in pair_done):
                    continue
                shared = gl.intersection(go.buffer(thin))
                L = shared.length
                if L > max(tol, thin * 10):
                    pair_done.add((s.uuid, o.uuid))
                    same = L >= gl.length - thin * 10 and (not o.is_line or L >= go.length - thin * 10)
                    rep.add("duplicate" if same and o.is_line else "double_line",
                            "error" if same else "warning", [s.uuid, o.uuid], _rep_point(shared),
                            ("Doppelte Linie (deckungsgleich)" if same and o.is_line else
                             "Linie liegt auf %s (Doppellinie, %s lang)" %
                             ("einer anderen Linie" if o.is_line else "einer Flächenkante",
                              _fmt(rep.mm(L) / 1000.0) + " m")),
                            length=L, fix={"action": "duplicates" if same and o.is_line else "manual"})

    # ---- open outlines ------------------------------------------------------------------
    if "open" in checks:
        for s in shapes:
            if s.kind not in ("polygon", "polyline") or s.closed or not s.rings:
                continue
            v = s.rec["vertices"]
            d = math.hypot(v[0][0] - v[-1][0], v[0][1] - v[-1][1])
            if d <= tol:
                rep.add("open", "error", [s.uuid], v[0][:2],
                        "Umriss ist nicht geschlossen (Lücke %s mm)" % _fmt(rep.mm(d)),
                        distance_mm=rep.mm(d), fix={"action": "close"})
            elif s.filled:
                rep.add("open", "warning", [s.uuid], v[0][:2],
                        "Offener Umriss mit Füllung (Fläche nicht geschlossen)",
                        fix={"action": "close"})

    # ---- vertex hygiene -------------------------------------------------------------------
    if "vertices" in checks:
        for s in shapes:
            if s.kind not in ("polygon", "polyline") or s.approx:
                continue
            for ring_i, verts in enumerate([s.rec["vertices"]] + list(s.rec.get("holes") or [])):
                n = len(verts)
                closed = s.closed or ring_i > 0
                for i in range(n):
                    if not closed and i == n - 1:
                        break
                    a, b = verts[i], verts[(i + 1) % n]
                    d = math.hypot(a[0] - b[0], a[1] - b[1])
                    if d <= eps:
                        rep.add("vertices", "warning", [s.uuid], a[:2], "Doppelter Punkt",
                                vertex=i + 1, ring=ring_i, fix={"action": "clean"})
                    elif d <= tol and a[2] == 0 and b[2] == 0:
                        rep.add("vertices", "warning", [s.uuid], a[:2],
                                "Mini-Segment %s mm" % _fmt(rep.mm(d)), vertex=i + 1, ring=ring_i,
                                distance_mm=rep.mm(d), fix={"action": "clean"})
                for i in range(n):
                    if not closed and (i == 0 or i == n - 1):
                        continue
                    p, a, b = verts[i], verts[i - 1], verts[(i + 1) % n]
                    if p[2] != 0 or a[2] != 0 or b[2] != 0:
                        continue
                    if C.point_segment_distance(p[:2], ("L", tuple(a[:2]), tuple(b[:2]))) <= eps and \
                            math.hypot(p[0] - a[0], p[1] - a[1]) > eps and math.hypot(p[0] - b[0], p[1] - b[1]) > eps:
                        rep.add("vertices", "info", [s.uuid], p[:2],
                                "Überflüssiger Punkt (liegt auf gerader Kante)", vertex=i + 1, ring=ring_i,
                                fix={"action": "clean"})

    # ---- tiny objects -------------------------------------------------------------------------
    if "tiny" in checks:
        for s in areas:
            g = s.geometry(chord)
            if g.is_empty:
                continue
            th = _thickness(g)
            if th <= tol:
                rep.add("tiny", "warning", [s.uuid], _rep_point(g),
                        "Splitterfläche (nur %s mm dick)" % _fmt(rep.mm(th)), thickness_mm=rep.mm(th),
                        fix={"action": "manual"})
        for s in lines:
            g = s.geometry(chord)
            if 0 < g.length <= tol:
                rep.add("tiny", "warning", [s.uuid], (g.coords[0][0], g.coords[0][1]),
                        "Mini-Linie (%s mm lang)" % _fmt(rep.mm(g.length)), fix={"action": "manual"})

    # ---- organisation: classes and attributes ------------------------------------------------
    if "classes" in checks:
        nc = [r for r in records_all if r.get("class") == none_class and r["kind"] not in ("group",)]
        if nc:
            by_layer = defaultdict(list)
            for r in nc:
                by_layer[r.get("layer", "")].append(r)
            for layer, rs in by_layer.items():
                kinds = defaultdict(int)
                for r in rs:
                    kinds[r["kind"]] += 1
                rep.add("classes", "warning", [r["uuid"] for r in rs[:100]],
                        _center(rs), "%d Objekte ohne Klasse (Klasse '%s') auf Ebene '%s': %s" % (
                            len(rs), none_class, layer,
                            ", ".join("%d %s" % (n, k) for k, n in sorted(kinds.items()))),
                        count=len(rs), fix={"action": "set_class"})
        dims = [r for r in records_all if r["kind"] == "dimension"
                and not any(h in r.get("class", "").lower() for h in DIM_CLASS_HINTS)]
        if dims:
            rep.add("classes", "info", [r["uuid"] for r in dims[:100]], _center(dims),
                    "%d Bemaßungen nicht in einer Bemaßungsklasse" % len(dims), count=len(dims),
                    fix={"action": "set_class"})
    if "attributes" in checks:
        by_cls = defaultdict(list)
        for r in records_all:
            bc = (r.get("attrs") or {}).get("by_class")
            if bc and r["kind"] in ("rect", "oval", "polygon", "polyline", "arc", "line") and not all(bc.values()):
                by_cls[r.get("class", "")].append(r)
        for cls, rs in sorted(by_cls.items()):
            rep.add("attributes", "info", [r["uuid"] for r in rs[:100]], _center(rs),
                    "%d Objekte in Klasse '%s' haben eigene Attribute statt 'nach Klasse'" % (len(rs), cls),
                    count=len(rs), fix={"action": "attributes_by_class"})
    if "approx" in checks:
        ap = [s for s in shapes if s.approx]
        if ap:
            rep.add("approx", "info", [s.uuid for s in ap[:100]], None,
                    "%d Objekte mit Spline-/Bezier-Kurven: nur angenähert geprüft" % len(ap),
                    count=len(ap))

    issues = sorted(rep.items, key=lambda it: (SEVERITY_ORDER[it["severity"]], it["kind"],
                                               -(it.get("thickness_mm") or it.get("distance_mm") or 0)))
    for n, it in enumerate(issues, 1):
        it["id"] = n
    counts = defaultdict(int)
    for it in issues:
        counts[it["severity"]] += 1
    kinds = defaultdict(int)
    for it in issues:
        kinds[it["kind"]] += 1
    return {
        "summary": {
            "objects": len(records_all), "checked": len(records), "areas": len(areas), "lines": len(lines),
            "compare": compare, "ignored_classes": list(ignore_classes or []),
            "errors": counts["error"], "warnings": counts["warning"], "infos": counts["info"],
            "by_kind": dict(kinds), "tolerance_mm": tolerance_mm,
            "precision_mm": t["min_thick"] * t["mpu"],
            "clean": counts["error"] == 0,
        },
        "issues": issues[:max_issues],
        "truncated": len(issues) > max_issues,
        "_shapes": shapes,
        "_all_issues": issues,
    }


def _center(recs):
    xs, ys = [], []
    for r in recs:
        b = r.get("bbox")
        if b:
            xs += [b[0], b[2]]
            ys += [b[1], b[3]]
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2) if xs else None


def public(result: dict) -> dict:
    """Strip internal keys and bulky regions for the MCP response."""
    out = {k: v for k, v in result.items() if not k.startswith("_")}
    out["issues"] = [{k: v for k, v in it.items() if k != "region"} for it in out["issues"]]
    return out
