"""Exact 2D building blocks for the asset catalogue (mm, true arcs).

Every function returns vw2d_draw items (dicts), so assets go through the same
exact pipeline as everything else: Decimal unit conversion, true arcs, read-
back check. Coordinates are mm; y points "up" in plan (north) and in
elevation (height).
"""

from __future__ import annotations

import math

Item = dict


def _r(v: float) -> float:
    """Round to 1e-6 mm so float noise never reaches the drawing."""
    return round(float(v), 6) + 0.0


def pt(x: float, y: float) -> list[float]:
    return [_r(x), _r(y)]


def rect(x1: float, y1: float, x2: float, y2: float, role: str = "outline") -> Item:
    """Axis-parallel rectangle from two corners."""
    return {"type": "rect", "p1": pt(min(x1, x2), max(y1, y2)), "p2": pt(max(x1, x2), min(y1, y2)),
            "role": role}


def line(x1: float, y1: float, x2: float, y2: float, role: str = "detail") -> Item:
    return {"type": "line", "p1": pt(x1, y1), "p2": pt(x2, y2), "role": role}


def circle(cx: float, cy: float, r: float, role: str = "detail") -> Item:
    return {"type": "circle", "center": pt(cx, cy), "radius": _r(r), "role": role}


def arc(cx: float, cy: float, r: float, start: float, sweep: float, role: str = "detail") -> Item:
    return {"type": "arc", "center": pt(cx, cy), "radius": _r(r), "start_angle": _r(start),
            "sweep": _r(sweep), "role": role}


def poly(points, closed: bool = True, role: str = "outline") -> Item:
    return {"type": "polygon" if closed else "polyline", "points": [pt(x, y) for x, y in points],
            "role": role}


def path(start, segments, closed: bool = True, role: str = "outline") -> Item:
    """segments: ("L", x, y) line; ("A", x, y, cx, cy, "ccw"|"cw") arc around
    a centre; ("R", x, y, r, "ccw"|"cw"[, large]) arc by radius - the centre
    then follows exactly from the (rounded) end points, so both ends are on
    the circle even for computed, non-grid points."""
    segs = []
    for s in segments:
        if s[0] == "L":
            segs.append({"line_to": pt(s[1], s[2])})
        elif s[0] == "R":
            segs.append({"arc_to": pt(s[1], s[2]), "radius": _r(s[3]), "direction": s[4],
                         "large": bool(s[5]) if len(s) > 5 else False})
        else:
            segs.append({"arc_to": pt(s[1], s[2]), "center": pt(s[3], s[4]), "direction": s[5]})
    return {"type": "path", "start": pt(*start), "segments": segs, "closed": closed, "role": role}


def rounded_rect(x1: float, y1: float, x2: float, y2: float, r: float, role: str = "outline") -> Item:
    """Rectangle with all four corners rounded by r (true arcs)."""
    x1, x2 = min(x1, x2), max(x1, x2)
    y1, y2 = min(y1, y2), max(y1, y2)
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    return path((x1 + r, y1), [
        ("L", x2 - r, y1), ("A", x2, y1 + r, x2 - r, y1 + r, "ccw"),
        ("L", x2, y2 - r), ("A", x2 - r, y2, x2 - r, y2 - r, "ccw"),
        ("L", x1 + r, y2), ("A", x1, y2 - r, x1 + r, y2 - r, "ccw"),
        ("L", x1, y1 + r), ("A", x1 + r, y1, x1 + r, y1 + r, "ccw"),
    ], role=role)


def front_rounded_rect(x1: float, y_back: float, x2: float, y_front: float, r: float,
                       role: str = "outline") -> Item:
    """Rectangle whose two front corners (y_front side) are rounded, back square.
    y_front < y_back (objects stand at a wall at y_back and reach to y_front)."""
    x1, x2 = min(x1, x2), max(x1, x2)
    r = min(r, (x2 - x1) / 2, abs(y_back - y_front))
    return path((x1, y_back), [
        ("L", x1, y_front + r), ("A", x1 + r, y_front, x1 + r, y_front + r, "ccw"),
        ("L", x2 - r, y_front), ("A", x2, y_front + r, x2 - r, y_front + r, "ccw"),
        ("L", x2, y_back),
    ], role=role)


def korb_oval(cx: float, cy: float, w: float, h: float, role: str = "outline") -> Item:
    """Four-centre oval (Korbbogen): an ellipse-like closed outline of width w
    and height h built from four tangent circular arcs (exact arcs, unlike a
    sampled ellipse). Long axis along the larger of w, h."""
    a, b = w / 2.0, h / 2.0
    swap = b > a
    if swap:
        a, b = b, a
    # classic construction: small radius r1 on the long axis, large r2 on the short
    r1 = b * b / a if a > 0 else 0.0
    r1 = max(min(r1 * 1.15, b * 0.95), 1e-6)
    # centre of the big arc on the short axis so the arcs are tangent
    # solve: (a - r1)^2 + y2^2 = (r2 - r1)^2 and r2 = b + y2
    y2 = ((a - r1) ** 2 - (b - r1) ** 2) / (2 * (b - r1))
    r2 = b + y2
    # tangent points between small (centre (a - r1, 0)) and big (centre (0, -y2)) arc
    d = math.hypot(a - r1, y2)
    tx, ty = (a - r1) + r1 * (a - r1) / d, r1 * y2 / d

    def P(x, y):
        return (cx + y, cy + x) if swap else (cx + x, cy + y)

    pts = {"E": (a, 0.0), "NE": (tx, ty), "NW": (-tx, ty), "SW": (-tx, -ty), "SE": (tx, -ty)}
    cs = {"E": (a - r1, 0.0), "N": (0.0, -y2), "W": (-(a - r1), 0.0), "S": (0.0, y2)}
    radius = {"E": r1, "W": r1, "N": r2, "S": r2}
    seq = [("NE", "E"), ("NW", "N"), ("SW", "W"), ("SE", "S")]
    start = P(*pts["SE"])
    segs = []
    for (to, c) in seq:
        x, y = P(*pts[to])
        segs.append(("R", x, y, radius[c], "cw" if swap else "ccw"))
    return path(start, segs, role=role)


def mirror_items(items: list[Item]) -> list[Item]:
    """Items mirrored about the y axis (x -> -x), exactly: only signs change,
    arc directions flip. Used for left/right variants of an asset (a mirrored
    symbol definition instead of a flipped instance)."""

    def m(p):
        return [-p[0] + 0.0, p[1] + 0.0]

    out = []
    for it in items:
        t = it["type"]
        n = dict(it)
        if t == "rect":
            (x1, y1), (x2, y2) = it["p1"], it["p2"]
            n["p1"], n["p2"] = [min(-x1, -x2) + 0.0, max(y1, y2)], [max(-x1, -x2) + 0.0, min(y1, y2)]
        elif t == "line":
            n["p1"], n["p2"] = m(it["p1"]), m(it["p2"])
        elif t == "circle":
            n["center"] = m(it["center"])
        elif t == "arc":
            n["center"] = m(it["center"])
            n["start_angle"] = _r((180.0 - it["start_angle"] - it["sweep"]) % 360.0)
        elif t in ("polygon", "polyline"):
            n["points"] = [m(p) for p in it["points"]]
        elif t == "path":
            n["start"] = m(it["start"])
            segs = []
            for s in it["segments"]:
                if "line_to" in s:
                    segs.append({"line_to": m(s["line_to"])})
                elif "radius" in s:
                    segs.append({"arc_to": m(s["arc_to"]), "radius": s["radius"], "large": s.get("large", False),
                                 "direction": "cw" if s.get("direction", "ccw") == "ccw" else "ccw"})
                else:
                    segs.append({"arc_to": m(s["arc_to"]), "center": m(s["center"]),
                                 "direction": "cw" if s.get("direction", "ccw") == "ccw" else "ccw"})
            n["segments"] = segs
        else:
            raise ValueError("can't mirror item type %r" % t)
        out.append(n)
    return out


def translate(items: list[Item], dx: float, dy: float) -> list[Item]:
    """Items moved by (dx, dy) (rounded to 1e-6 mm like everything else)."""

    def t(p):
        return pt(p[0] + dx, p[1] + dy)

    out = []
    for it in items:
        n = dict(it)
        for k in ("p1", "p2", "center", "start"):
            if k in n:
                n[k] = t(n[k])
        if "points" in n:
            n["points"] = [t(p) for p in n["points"]]
        if "segments" in n:
            segs = []
            for sg in n["segments"]:
                sg = dict(sg)
                for k in ("line_to", "arc_to", "center"):
                    if k in sg:
                        sg[k] = t(sg[k])
                segs.append(sg)
            n["segments"] = segs
        out.append(n)
    return out
