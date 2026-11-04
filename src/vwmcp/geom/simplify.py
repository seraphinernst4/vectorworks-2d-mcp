"""Simplify polylines/polygons within a tolerance (server side, pure).

Douglas-Peucker on the corner points: the result keeps a subset of the
original points, so it never moves a point and never deviates from the
original outline by more than the tolerance. Outlines with true arcs are
left alone (they are exact already). Curve vertices (Bezier/cubic, e.g.
from imported splines) are simplified from Vectorworks' polygonization.
Closed areas must stay valid (no self-intersection); otherwise the object is
simplified less (half the tolerance, then not at all).

Neighbours stay seamless: where two outlines share a run of points (the
faces of a VectorView/Make2D export, adjacent fills), that run is simplified
the same way in both (same points, canonical direction), and a run shared
with an object that is not simplified keeps all its points. Simplifying
each outline on its own opened gaps and overlaps between neighbours (live:
646 overlaps after simplifying a VectorView section).
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


def _context_points(rec: dict) -> list:
    """Corner points of any record, to find shared outlines."""
    if rec.get("kind") == "line":
        return [tuple(rec["p1"][:2]), tuple(rec["p2"][:2])]
    pts = [(v[0], v[1]) for v in rec.get("vertices") or []]
    for h in rec.get("holes") or []:
        pts += [(v[0], v[1]) for v in h]
    return pts


def _runs(pts: list, sets: list, closed: bool):
    """Split an outline at its anchors (points where the set of objects
    sharing them changes). Yields (start, end) index pairs along the outline
    (end may wrap past n for closed outlines) or None when a closed outline
    has no anchor."""
    n = len(pts)
    if closed:
        anchors = [k for k in range(n) if sets[k] and (sets[k] != sets[k - 1] or sets[k] != sets[(k + 1) % n])]
        if not anchors:
            return None
        return [(a, anchors[(i + 1) % len(anchors)] + (n if i + 1 >= len(anchors) else 0))
                for i, a in enumerate(anchors)]
    anchors = [0] + [k for k in range(1, n - 1) if sets[k] and (sets[k] != sets[k - 1] or sets[k] != sets[k + 1])] \
        + [n - 1]
    return list(zip(anchors[:-1], anchors[1:]))


def _simplify_shared(pts: list, sets: list, closed: bool, tol: float, fixed: set,
                     tol_free: float | None = None) -> list[int] | None:
    """Kept indices with shared runs simplified canonically (None: keep all).
    tol_free: tolerance for the runs no other object shares (a retry may
    lower it; shared runs keep tol so they stay equal to the neighbour's)."""
    n = len(pts)
    runs = _runs(pts, sets, closed)
    if runs is None:
        return None  # the whole outline is shared with the same objects: leave it
    keep = set()
    for a, b in runs:
        idx = [k % n for k in range(a, b + 1)]
        run = [pts[k] for k in idx]
        inner = [sets[k] for k in idx[1:-1]]
        shared = inner[0] if inner else frozenset()
        if shared and (shared & fixed or run[0] == run[-1]):
            local = list(range(len(run)))  # shared with an object that stays: keep every point
        elif shared:
            rev = tuple(run[0]) > tuple(run[-1])  # the neighbour sees the same run: same direction, same result
            local = dp(run[::-1] if rev else run, tol)
            if rev:
                local = [len(run) - 1 - i for i in local]
        else:
            local = dp(run, tol if tol_free is None else tol_free)
        keep.update(idx[i] for i in local)
    return sorted(keep)


def plan_simplify(records: list[dict], tol: float, min_vertices: int = 4,
                  context: list[dict] | None = None) -> dict[str, Any]:
    """Returns {"changes": [{uuid, verts, closed, before, after}], "vertices_before",
    "vertices_after", "objects": n, "skipped": {reason: n}}. context: other
    objects of the drawing (not simplified) whose outlines must stay shared."""
    q = max(tol * 1e-6, 1e-12)

    def key(p):
        return (round(p[0] / q), round(p[1] / q))

    cands, skipped = {}, {}
    for i, rec in enumerate(records):
        pts, info = source_points(rec)
        if pts is None:
            if info != "kind":
                skipped[info] = skipped.get(info, 0) + 1
            continue
        closed = bool(rec.get("closed"))
        if len(pts) < max(min_vertices, 3 if closed else 2):
            continue
        cands[i] = (pts, closed)
    owners: dict[tuple, set] = {}
    allrecs = list(records) + list(context or [])
    for i, rec in enumerate(allrecs):
        for p in (cands[i][0] if i in cands else _context_points(rec)):
            owners.setdefault(key(p), set()).add(i)
    fixed = set(range(len(allrecs))) - set(cands)
    reasons: dict[int, str | None] = {}
    changes: dict[int, list[int]] = {}
    for _round in range(4):  # an object that ends up unchanged pins its shared runs in its neighbours
        changes, done = {}, {}
        for i, (pts, closed) in cands.items():
            if i in fixed:
                continue
            rec = records[i]
            sets = [frozenset(owners.get(key(p), ()) - {i}) for p in pts]
            any_shared = any(sets)
            idx = None
            for t in (tol, tol / 2):
                if any_shared:
                    cand = _simplify_shared(pts, sets, closed, tol, fixed, tol_free=t)
                    if cand is None:
                        break
                else:
                    cand = _dp_closed(pts, t) if closed else dp(pts, t)
                if closed:
                    if len(cand) < 3:
                        continue
                    poly = Polygon([pts[k] for k in cand])
                    if not poly.is_valid or poly.area <= 0:
                        continue
                idx = cand
                break
            if idx is None:
                done[i] = "would become invalid" if not any_shared or _runs(pts, sets, closed) is not None \
                    else "shared outline"
                continue
            if len(idx) >= len(rec.get("vertices") or []):
                done[i] = None  # nothing gained (curves stay curves when they are compact already)
                continue
            changes[i] = idx
        reasons.update(done)
        newly = set(done) - fixed
        if not newly:
            break
        fixed |= newly
    out, vb, va = [], 0, 0
    for i, why in reasons.items():
        if why and i not in changes:
            skipped[why] = skipped.get(why, 0) + 1
    for i, idx in changes.items():
        pts, closed = cands[i]
        rec = records[i]
        verts = [[pts[k][0], pts[k][1], CORNER, 0.0] for k in idx]
        out.append({"uuid": rec["uuid"], "verts": verts, "closed": closed, "before": len(rec.get("vertices") or []),
                    "after": len(verts)})
        vb += len(rec.get("vertices") or [])
        va += len(verts)
    return {"changes": out, "objects": len(out), "vertices_before": vb, "vertices_after": va,
            "skipped": skipped}
