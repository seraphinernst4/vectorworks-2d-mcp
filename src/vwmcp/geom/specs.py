"""Validate and normalize drawing requests into exact Vectorworks items.

Numbers may be given in another unit than the document's ("mm", "cm", "m")
and polygon points may be relative ("+dx, +dy" chains like a Maßkette).
Unit conversion and relative sums use Decimal arithmetic, so 3650 mm become
exactly 3.65 m and 0.1 + 0.2 is 0.3, not 0.30000000000000004.
"""

from __future__ import annotations

import math
from decimal import Decimal, getcontext

from vwmcp.geom import curves as C

getcontext().prec = 34
UNIT_MM = {"mm": Decimal(1), "cm": Decimal(10), "dm": Decimal(100), "m": Decimal(1000)}
JUST = ("left", "center", "right")
VALIGN = ("top", "top_baseline", "center", "baseline", "bottom")
ORIENT = ("horizontal", "vertical", "aligned")


class SpecError(ValueError):
    pass


class Converter:
    """Numbers in `unit` -> document units, through Decimal."""

    def __init__(self, mm_per_unit: float, unit: str | None):
        doc_mm = Decimal(f"{mm_per_unit:.12g}")
        if unit in (None, "", "document", "doc"):
            self.factor = Decimal(1)
        elif unit in UNIT_MM:
            self.factor = UNIT_MM[unit] / doc_mm
        else:
            raise SpecError("unit must be one of document, mm, cm, dm, m (got %r)" % unit)

    def d(self, v) -> Decimal:
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            raise SpecError("expected a number, got %r" % (v,))
        try:
            x = Decimal(str(v))
        except Exception as e:  # noqa: BLE001
            raise SpecError("not a number: %r" % (v,)) from e
        if not x.is_finite():
            raise SpecError("not a finite number: %r" % (v,))
        return x * self.factor

    def f(self, v) -> float:
        return float(self.d(v))

    def pt(self, p) -> tuple[float, float]:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise SpecError("a point is [x, y], got %r" % (p,))
        return (self.f(p[0]), self.f(p[1]))

    def pts(self, pts, relative=False) -> list[tuple[float, float]]:
        if not isinstance(pts, list) or not pts:
            raise SpecError("points must be a non-empty list of [x, y]")
        if not relative:
            return [self.pt(p) for p in pts]
        x, y = self.d(pts[0][0]), self.d(pts[0][1])
        out = [(float(x), float(y))]
        for p in pts[1:]:
            if not isinstance(p, (list, tuple)) or len(p) != 2:
                raise SpecError("a point is [x, y], got %r" % (p,))
            x, y = x + self.d(p[0]), y + self.d(p[1])
            out.append((float(x), float(y)))
        return out


def _common(item: dict, out: dict, defaults: dict) -> dict:
    for k in ("class", "layer"):
        v = item.get(k, defaults.get(k))
        if v:
            out[k] = str(v)
    attrs = dict(defaults.get("attributes") or {})
    attrs.update(item.get("attributes") or {})
    if item.get("name"):
        attrs["name"] = str(item["name"])
    if not any(k in attrs for k in ("pen", "fill", "hatch", "line_style", "line_weight", "opacity", "by_class")):
        attrs["by_class"] = True  # graphic attributes come from the class
    out["attributes"] = attrs
    return out


def _path(cv: Converter, spec: dict, closed_default=True):
    """Path spec -> (vertices, segments, closed)."""
    if "start" not in spec or not isinstance(spec.get("segments"), list):
        raise SpecError("a path needs 'start' and a 'segments' list")
    cur = cv.pt(spec["start"])
    closed = bool(spec.get("closed", closed_default))
    verts = [[cur[0], cur[1], 0, 0.0]]
    segs = []
    for i, sg in enumerate(spec["segments"]):
        if "line_to" in sg:
            nxt = cv.pt(sg["line_to"])
            segs.append(("L", cur, nxt))
            verts.append([nxt[0], nxt[1], 0, 0.0])
        elif "arc_to" in sg:
            nxt = cv.pt(sg["arc_to"])
            if "center" in sg:
                c = cv.pt(sg["center"])
                ccw = sg.get("direction", "ccw") == "ccw"
            elif "through" in sg:
                m = cv.pt(sg["through"])
                c = C.circle_center_3pt(cur, m, nxt)
                ccw = C.cross(C.sub(m, cur), C.sub(nxt, m)) > 0
            elif "radius" in sg:
                r = cv.f(sg["radius"])
                ccw = sg.get("direction", "ccw") == "ccw"
                c = _center_from_radius(cur, nxt, r, ccw, bool(sg.get("large", False)))
            else:
                raise SpecError("segment %d: arc_to needs center, through or radius" % i)
            r0, r1 = C.dist(cur, c), C.dist(nxt, c)
            if abs(r0 - r1) > 1e-9 * max(r0, r1, 1.0):
                raise SpecError("segment %d: start and end are %.9g / %.9g from the center; an "
                                "arc needs both on the same circle" % (i, r0, r1))
            if cur == nxt:
                raise SpecError("segment %d: a full circle as one arc is ambiguous; use type circle "
                                "or two half arcs" % i)
            segs.append(("A", cur, nxt, c, (r0 + r1) / 2.0, ccw))
            verts += C.arc_to_vertices(cur, nxt, c, ccw)
        else:
            raise SpecError("segment %d needs line_to or arc_to" % i)
        cur = nxt
    if closed and len(verts) > 1 and verts[-1][:2] == verts[0][:2]:
        verts.pop()
    elif closed and segs and segs[-1][2] != segs[0][1]:
        segs.append(("L", segs[-1][2], segs[0][1]))
    return verts, segs, closed


def _center_from_radius(a, b, r, ccw, large):
    chord = C.dist(a, b)
    if chord == 0:
        raise SpecError("arc from a point to the same point: give a center instead of a radius")
    if r <= 0 or chord > 2 * r * (1 + 1e-12):
        raise SpecError("radius %.9g is too small for a chord of %.9g" % (r, chord))
    mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    h = math.sqrt(max(0.0, r * r - (chord / 2.0) ** 2))
    ux, uy = (b[0] - a[0]) / chord, (b[1] - a[1]) / chord
    left = (mid[0] - uy * h, mid[1] + ux * h)
    right = (mid[0] + uy * h, mid[1] - ux * h)
    # ccw small arc: center on the left of a->b
    return left if (ccw != large) else right


def normalize(item: dict, mm_per_unit: float, defaults: dict | None = None):
    """One drawing request -> (vectorworks item, expected segments or None)."""
    defaults = defaults or {}
    cv = Converter(mm_per_unit, item.get("unit", defaults.get("unit")))
    t = item.get("type")
    exp = None
    if t in ("polygon", "polyline"):
        closed = bool(item.get("closed", t == "polygon"))
        pts = cv.pts(item.get("points"), bool(item.get("relative")))
        if closed and len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        for i in range(1, len(pts)):
            if pts[i] == pts[i - 1]:
                raise SpecError("points %d and %d are identical (zero-length edge)" % (i - 1, i))
        if len(pts) < (3 if closed else 2):
            raise SpecError("%s needs at least %d points" % (t, 3 if closed else 2))
        verts = [[x, y, 0, 0.0] for x, y in pts]
        holes = [[[x, y, 0, 0.0] for x, y in cv.pts(h, bool(item.get("relative")))]
                 for h in item.get("holes") or []]
        out = {"kind": "poly", "verts": verts, "closed": closed, "holes": holes}
        exp = C.points_segments(pts, closed)
    elif t == "path":
        verts, segs, closed = _path(cv, item, True)
        holes = []
        for h in item.get("holes") or []:
            if isinstance(h, dict):
                hv, _, _ = _path(cv, dict(h, closed=True), True)
            else:
                hv = [[x, y, 0, 0.0] for x, y in cv.pts(h)]
            holes.append(hv)
        out = {"kind": "poly", "verts": verts, "closed": closed, "holes": holes}
        exp = segs
    elif t == "rect":
        if "p1" in item and "p2" in item:
            p1, p2 = cv.pt(item["p1"]), cv.pt(item["p2"])
            if p1[0] == p2[0] or p1[1] == p2[1]:
                raise SpecError("rect needs width and height > 0")
            out = {"kind": "rect", "p1": p1, "p2": p2}
            xs, ys = sorted((p1[0], p2[0])), sorted((p1[1], p2[1]))
            corners = [(xs[0], ys[1]), (xs[1], ys[1]), (xs[1], ys[0]), (xs[0], ys[0])]
        else:
            x0, y0 = cv.d(item["corner"][0]), cv.d(item["corner"][1])
            w, h = cv.d(item["width"]), cv.d(item["height"])
            if w == 0 or h == 0:
                raise SpecError("rect needs width and height > 0")
            ang = float(item.get("angle", 0.0))
            if ang == 0.0:
                p1, p2 = (float(x0), float(y0 + h)), (float(x0 + w), float(y0))
                out = {"kind": "rect", "p1": p1, "p2": p2}
                xs, ys = sorted((p1[0], p2[0])), sorted((p1[1], p2[1]))
                corners = [(xs[0], ys[1]), (xs[1], ys[1]), (xs[1], ys[0]), (xs[0], ys[0])]
            else:
                ca, sa = math.cos(math.radians(ang)), math.sin(math.radians(ang))
                fx, fy, fw, fh = float(x0), float(y0), float(w), float(h)
                corners = [(fx, fy), (fx + fw * ca, fy + fw * sa),
                           (fx + fw * ca - fh * sa, fy + fw * sa + fh * ca), (fx - fh * sa, fy + fh * ca)]
                out = {"kind": "poly", "verts": [[x, y, 0, 0.0] for x, y in corners], "closed": True,
                       "holes": []}
        exp = None if out["kind"] == "rect" else C.points_segments(corners, True)
        out["_corners"] = corners
    elif t == "circle":
        c, r = cv.pt(item["center"]), cv.f(item["radius"])
        if r <= 0:
            raise SpecError("radius must be > 0")
        out = {"kind": "circle", "center": c, "radius": r}
    elif t == "arc":
        if "center" in item:
            c, r = cv.pt(item["center"]), cv.f(item["radius"])
            out = {"kind": "arc", "center": c, "radius": r, "start": float(item["start_angle"]),
                   "sweep": float(item["sweep"])}
        else:
            a, m, b = cv.pt(item["start"]), cv.pt(item["through"]), cv.pt(item["end"])
            c = C.circle_center_3pt(a, m, b)
            r = C.dist(a, c)
            a0 = math.degrees(math.atan2(a[1] - c[1], a[0] - c[0]))
            ccw = C.cross(C.sub(m, a), C.sub(b, m)) > 0
            sw = math.degrees(C.arc_sweep(("A", a, b, c, r, ccw)))
            out = {"kind": "arc", "center": c, "radius": r, "start": a0, "sweep": sw}
        if out["radius"] <= 0 or out["sweep"] == 0:
            raise SpecError("arc needs radius > 0 and sweep != 0")
    elif t == "oval":
        c = cv.pt(item["center"])
        w, h = cv.f(item["width"]), cv.f(item["height"])
        out = {"kind": "oval", "p1": (c[0] - w / 2, c[1] + h / 2), "p2": (c[0] + w / 2, c[1] - h / 2),
               "angle": float(item.get("angle", 0.0))}
    elif t == "line":
        p1, p2 = cv.pt(item["p1"]), cv.pt(item["p2"])
        if p1 == p2:
            raise SpecError("line needs two different points")
        out = {"kind": "line", "p1": p1, "p2": p2}
        exp = [("L", p1, p2)]
    elif t == "text":
        if not item.get("text"):
            raise SpecError("text needs 'text'")
        out = {"kind": "text", "text": str(item["text"]), "position": cv.pt(item["position"]),
               "size": float(item.get("size", 10)), "angle": float(item.get("angle", 0.0)),
               "align": item.get("align", "left"), "valign": item.get("valign", "baseline")}
        if item.get("font"):
            out["font"] = str(item["font"])
        if out["align"] not in JUST or out["valign"] not in VALIGN:
            raise SpecError("align: %s; valign: %s" % ("/".join(JUST), "/".join(VALIGN)))
    elif t == "dimension":
        out = {"kind": "dim", "p1": cv.pt(item["p1"]), "p2": cv.pt(item["p2"]),
               "offset": cv.f(item.get("offset", 0)), "orientation": item.get("orientation", "aligned")}
        if out["orientation"] not in ORIENT:
            raise SpecError("orientation: " + "/".join(ORIENT))
    elif t == "chain_dimension":
        pts = cv.pts(item.get("points"), bool(item.get("relative")))
        if len(pts) < 3:
            raise SpecError("chain_dimension needs at least 3 points")
        out = {"kind": "chain_dim", "points": pts, "offset": cv.f(item.get("offset", 0)),
               "orientation": item.get("orientation", "aligned")}
        if out["orientation"] not in ORIENT:
            raise SpecError("orientation: " + "/".join(ORIENT))
    elif t == "locus":
        out = {"kind": "locus", "point": cv.pt(item["point"])}
    else:
        raise SpecError("unknown type %r (polygon, polyline, path, rect, circle, arc, oval, line, "
                        "text, dimension, chain_dimension, locus)" % (t,))
    _common(item, out, defaults)
    return out, exp


def check_created(item: dict, exp, rec: dict, eps: float) -> dict:
    """Readback check: did Vectorworks store exactly what was asked for?"""
    from vwmcp.geom.shapes import from_record
    k = item["kind"]
    dev = 0.0
    if k == "rect":
        got = rec.get("corners") or []
        want = item["_corners"]
        dev = max((min(C.dist(w, g) for g in got) for w in want), default=math.inf) if got else math.inf
    elif k == "circle" or k == "arc":
        dev = max(C.dist(item["center"], rec["center"]), abs(item["radius"] - rec["radius"]))
    elif k == "line":
        dev = max(C.dist(item["p1"], rec["p1"]), C.dist(item["p2"], rec["p2"]))
    elif k == "poly" and exp:
        got = from_record(rec).segments
        if len(got) == len(exp) and C.segments_max_deviation(exp, got) != math.inf:
            dev = C.segments_max_deviation(exp, got)
        else:
            pts = [q for s in exp for q in (s[1], s[2])]
            dev = max(min(C.point_segment_distance(p, g) for g in got) for p in pts) if got else math.inf
    elif k == "text":
        dev = 0.0  # origin depends on alignment; position is not a geometric point
    return {"deviation": dev, "exact": dev <= eps}
