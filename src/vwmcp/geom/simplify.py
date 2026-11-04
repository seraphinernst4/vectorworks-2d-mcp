"""Simplify polylines/polygons within a tolerance (server side, pure).

Douglas-Peucker on the corner points: the result keeps a subset of the
original points, so it never moves a point and never deviates from the
original outline by more than the tolerance. Outlines with true arcs are
left alone (they are exact already). Curve vertices (Bezier/cubic, e.g.
from imported splines) are simplified from Vectorworks' polygonization.
Closed areas must stay valid (no self-intersection); otherwise the object is
simplified less (half the tolerance, then not at all).

Neighbours stay seamless: a point several outlines share (the faces of a
VectorView/Make2D export, adjacent fills) is kept by all of them as soon as
one keeps it, and a point that lies on a neighbour's edge (T-junction) keeps
that edge; each stretch between kept points is simplified again, so the
tolerance still holds. Points closer than 5 % of the tolerance count as one
(exporters write shared corners with noise). Simplifying each outline on its
own opened gaps and overlaps between neighbours (live: 646 overlaps after
simplifying a VectorView section).
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


def _constrained(pts: list, closed: bool, forced: set, tol: float) -> list[int]:
    """Douglas-Peucker that keeps the forced points: each stretch between
    two of them is simplified on its own (deviation stays within tol)."""
    n = len(pts)
    if not forced:
        return _dp_closed(pts, tol) if closed else dp(pts, tol)
    f = sorted(forced | ({0, n - 1} if not closed else set()))
    keep = set(f)
    pairs = list(zip(f[:-1], f[1:]))
    if closed:
        pairs.append((f[-1], f[0] + n))
    for a, b in pairs:
        idx = [k % n for k in range(a, b + 1)]
        keep.update(idx[i] for i in dp([pts[k] for k in idx], tol))
    return sorted(keep)


def _tjunctions(allpts: list, cands: dict, eps: float) -> dict:
    """{(obj, point index): [(cand, k, k+1), ...]}: points that lie on the
    inside of a candidate's edge (not at its ends) - where outlines meet in a
    T, the edge must stay when the point stays."""
    import numpy as np
    import shapely

    segs, owner = [], []
    for i, (pts, closed) in cands.items():
        n = len(pts)
        for k in range(n if closed else n - 1):
            segs.append((pts[k], pts[(k + 1) % n]))
            owner.append((i, k, (k + 1) % n))
    if not segs:
        return {}
    lines = shapely.linestrings([[a, b] for a, b in segs])
    tree = shapely.STRtree(lines)
    flat = [(i, k, p) for i, pts in allpts.items() for k, p in enumerate(pts)]
    if not flat:
        return {}
    pts_geo = shapely.points(np.array([[p[0], p[1]] for _i, _k, p in flat]))
    pi, si = tree.query(pts_geo, predicate="dwithin", distance=eps)
    out: dict = {}
    for a, b in zip(pi.tolist(), si.tolist()):
        i, k, p = flat[a]
        c, s0, s1 = owner[b]
        if c == i:
            continue
        (ax, ay), (bx, by) = segs[b]
        if (abs(p[0] - ax) <= eps and abs(p[1] - ay) <= eps) or (abs(p[0] - bx) <= eps and abs(p[1] - by) <= eps):
            continue  # at the edge's end: a shared point, not a T
        out.setdefault((i, k), []).append((c, s0, s1))
    return out


def plan_simplify(records: list[dict], tol: float, min_vertices: int = 4,
                  context: list[dict] | None = None, share_eps: float | None = None) -> dict[str, Any]:
    """Returns {"changes": [{uuid, verts, closed, before, after}], "vertices_before",
    "vertices_after", "objects": n, "skipped": {reason: n}}. context: other
    objects of the drawing (not simplified) whose outlines must stay shared."""
    eps = tol * 0.05 if share_eps is None else share_eps
    q = max(eps, 1e-12)
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
    allrecs = list(records) + list(context or [])
    allpts = {i: (cands[i][0] if i in cands else _context_points(r)) for i, r in enumerate(allrecs)}

    # clusters: points within eps are one point
    cells: dict[tuple, list] = {}
    reps: list[tuple] = []
    clus: dict[int, list[int]] = {}
    for i, pts in allpts.items():
        ids = []
        for p in pts:
            kx, ky = math.floor(p[0] / q), math.floor(p[1] / q)
            hit = None
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for c in cells.get((kx + dx, ky + dy), ()):
                        r = reps[c]
                        if abs(r[0] - p[0]) <= eps and abs(r[1] - p[1]) <= eps:
                            hit = c
                            break
                    if hit is not None:
                        break
                if hit is not None:
                    break
            if hit is None:
                hit = len(reps)
                reps.append((p[0], p[1]))
                cells.setdefault((kx, ky), []).append(hit)
            ids.append(hit)
        clus[i] = ids
    users: dict[int, set] = {}
    for i, ids in clus.items():
        for c in ids:
            users.setdefault(c, set()).add(i)
    shared = {c for c, u in users.items() if len(u) > 1}
    tj = _tjunctions(allpts, cands, eps) if len(allrecs) > 1 else {}

    # objects that are not simplified keep all their points
    base = set()
    for i in allpts:
        if i not in cands:
            base.update(clus[i])
    tol_of = {i: tol for i in cands}
    keepall: set = set()
    forced = {i: {k for k, c in enumerate(clus[i]) if c in base} for i in cands}
    keep: dict[int, list[int]] = {}
    for _outer in range(6):  # validity: simplify less, then not at all
        for _inner in range(50):  # until the shared points agree
            K = set(base)
            extra: dict[int, set] = {i: set() for i in cands}
            for i, (pts, closed) in cands.items():
                if i in keepall:
                    keep[i] = list(range(len(pts)))
                else:
                    keep[i] = _constrained(pts, closed, forced[i], tol_of[i])
                    if len(keep[i]) >= len(records[i].get("vertices") or []):
                        keep[i] = list(range(len(pts)))  # stays as it is: all its points stay for the neighbours
                for k in keep[i]:
                    K.add(clus[i][k])
                    for c, s0, s1 in tj.get((i, k), ()):
                        extra[c].update((s0, s1))
            for i in allpts:
                if i not in cands:
                    for k in range(len(allpts[i])):
                        for c, s0, s1 in tj.get((i, k), ()):
                            extra[c].update((s0, s1))
            changed = False
            for i in cands:
                nf = {k for k, c in enumerate(clus[i]) if c in K and c in shared} | extra[i] | forced[i]
                if nf != forced[i]:
                    forced[i], changed = nf, True
            if not changed:
                break
        bad = []
        for i, (pts, closed) in cands.items():
            if closed and i not in keepall:
                idx = keep[i]
                poly = Polygon([pts[k] for k in idx]) if len(idx) >= 3 else None
                if poly is None or not poly.is_valid or poly.area <= 0:
                    bad.append(i)
        if not bad:
            break
        for i in bad:
            if tol_of[i] > tol / 2:
                tol_of[i] = tol / 2
            else:
                keepall.add(i)
    out, vb, va = [], 0, 0
    for i, (pts, closed) in cands.items():
        rec = records[i]
        nv = len(rec.get("vertices") or [])
        if i in keepall:
            skipped["would become invalid"] = skipped.get("would become invalid", 0) + 1
            continue
        idx = keep.get(i) or list(range(len(pts)))
        if len(idx) >= nv:
            continue  # nothing gained (curves stay curves when they are compact already)
        verts = [[pts[k][0], pts[k][1], CORNER, 0.0] for k in idx]
        out.append({"uuid": rec["uuid"], "verts": verts, "closed": closed, "before": nv, "after": len(verts)})
        vb += nv
        va += len(verts)
    return {"changes": out, "objects": len(out), "vertices_before": vb, "vertices_after": va,
            "skipped": skipped}
