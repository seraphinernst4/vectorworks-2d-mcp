"""Simplify polylines/polygons within a tolerance (server side, pure).

Douglas-Peucker on the corner points: the result keeps a subset of the
original points, so it never moves a point and never deviates from the
original outline by more than the tolerance. Outlines with true arcs are
left alone (they are exact already). Curve vertices (Bezier/cubic, e.g.
from imported splines) are simplified from Vectorworks' polygonization.
Closed areas must stay valid (no self-intersection); otherwise the object is
simplified less (half the tolerance, then not at all).
"""

from __future__ import annotations

import math
from typing import Any

from shapely.geometry import Polygon

CORNER, ARC = 0, 3


def _seg_dist(p, a, b) -> float:
    ax, ay = a[0], a[1]
    dx, dy = b[0] - ax, b[1] - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def dp(points: list, tol: float) -> list[int]:
    """Indices of the points Douglas-Peucker keeps (open polyline, ends kept)."""
    n = len(points)
    if n <= 2:
        return list(range(n))
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = points[i], points[j]
        best, idx = -1.0, -1
        for k in range(i + 1, j):
            d = _seg_dist(points[k], a, b)
            if d > best:
                best, idx = d, k
        if best > tol:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return [i for i in range(n) if keep[i]]


def _dp_closed(points: list, tol: float) -> list[int]:
    """Closed ring: split at the point farthest from the first, run DP on both halves."""
    n = len(points)
    if n <= 3:
        return list(range(n))
    far = max(range(n), key=lambda k: math.hypot(points[k][0] - points[0][0], points[k][1] - points[0][1]))
    a = dp(points[:far + 1], tol)
    b = dp(points[far:] + [points[0]], tol)
    idx = a + [far + k for k in b[1:-1]]
    return sorted(set(idx))


def source_points(rec: dict):
    """(points, from_curves) or (None, reason)."""
    if rec.get("kind") not in ("polygon", "polyline") or rec.get("holes"):
        return None, "kind" if rec.get("kind") not in ("polygon", "polyline") else "holes"
    vs_ = rec.get("vertices") or []
    types = {int(v[2]) for v in vs_}
    if types <= {CORNER}:
        return [(v[0], v[1]) for v in vs_], False
    if ARC in types:
        return None, "true arcs"
    if rec.get("approx_polygon"):
        pts = [tuple(p[:2]) for p in rec["approx_polygon"]]
        if rec.get("closed") and len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        return pts, True
    return None, "curve vertices"


def plan_simplify(records: list[dict], tol: float, min_vertices: int = 4) -> dict[str, Any]:
    """Returns {"changes": [{uuid, verts, closed, before, after}], "vertices_before",
    "vertices_after", "objects": n, "skipped": {reason: n}}."""
    changes, skipped = [], {}
    vb = va = 0
    for rec in records:
        pts, info = source_points(rec)
        if pts is None:
            if info != "kind":
                skipped[info] = skipped.get(info, 0) + 1
            continue
        closed = bool(rec.get("closed"))
        n = len(pts)
        if n < max(min_vertices, 3 if closed else 2):
            continue
        idx = None
        for t in (tol, tol / 2):
            cand = _dp_closed(pts, t) if closed else dp(pts, t)
            if closed:
                if len(cand) < 3:
                    continue
                poly = Polygon([pts[i] for i in cand])
                if not poly.is_valid or poly.area <= 0:
                    continue
            idx = cand
            break
        if idx is None:
            skipped["would become invalid"] = skipped.get("would become invalid", 0) + 1
            continue
        if len(idx) >= len(rec.get("vertices") or []):
            continue  # nothing gained (curves stay curves when they are compact already)
        verts = [[pts[i][0], pts[i][1], CORNER, 0.0] for i in idx]
        changes.append({"uuid": rec["uuid"], "verts": verts, "closed": closed, "before": len(rec.get("vertices") or []),
                        "after": len(verts)})
        vb += len(rec.get("vertices") or [])
        va += len(verts)
    return {"changes": changes, "objects": len(changes), "vertices_before": vb, "vertices_after": va,
            "skipped": skipped}
