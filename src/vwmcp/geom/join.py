"""Join loose pieces (lines, open polylines) into polylines (server side, pure).

Make2D, VectorView and most CAD exports deliver outlines as many separate
pieces. Pieces join when their end points meet within the tolerance, they
lie on the same layer, in the same class and group and look the same (pen,
line weight, line style). A chain stops at a junction (three or more pieces
meeting) unless `through_junctions` picks the straightest continuation.

The first piece of a chain is rebuilt as the joined polyline (it keeps its
class, attributes and place in the stacking order), the others are deleted.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

CORNER, ARC = 0, 3


def _attr_key(rec: dict) -> tuple:
    a = rec.get("attrs") or {}
    bc = a.get("by_class") or {}
    return (rec.get("layer"), rec.get("class"), rec.get("parent"), a.get("pen"), a.get("line_weight_mm"),
            a.get("line_style"), bool(bc.get("pen_color")), bool(bc.get("line_weight")),
            bool(bc.get("line_style")))


def piece_points(rec: dict, include_curves: bool = True):
    """Vertex list [[x, y, type, radius]] of a joinable piece, or None.
    Curve vertices (Bezier/cubic) are replaced by Vectorworks' own
    polygonization when include_curves (an approximation)."""
    k = rec.get("kind")
    if k == "line":
        return [[rec["p1"][0], rec["p1"][1], CORNER, 0.0], [rec["p2"][0], rec["p2"][1], CORNER, 0.0]]
    if k in ("polyline", "polygon") and not rec.get("closed", True) and not rec.get("holes"):
        vs_ = rec.get("vertices") or []
        if len(vs_) < 2:
            return None
        if all(int(v[2]) in (CORNER, ARC) for v in vs_):
            if int(vs_[0][2]) != CORNER or int(vs_[-1][2]) != CORNER:
                return None  # an arc vertex at an end has no exact end point
            return [[v[0], v[1], int(v[2]), float(v[3]) if len(v) > 3 else 0.0] for v in vs_]
        if include_curves and rec.get("approx_polygon"):
            return [[p[0], p[1], CORNER, 0.0] for p in rec["approx_polygon"]]
    return None


class _Nodes:
    """End points clustered within the tolerance (grid hashing)."""

    def __init__(self, tol: float):
        self.tol = max(tol, 1e-12)
        self.cell = self.tol * 2
        self.grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        self.pts: list[tuple[float, float]] = []

    def node(self, x: float, y: float) -> int:
        cx, cy = int(math.floor(x / self.cell)), int(math.floor(y / self.cell))
        best, bd = None, self.tol
        for i in (cx - 1, cx, cx + 1):
            for j in (cy - 1, cy, cy + 1):
                for n in self.grid.get((i, j), ()):
                    px, py = self.pts[n]
                    d = math.hypot(px - x, py - y)
                    if d <= bd:
                        best, bd = n, d
        if best is not None:
            return best
        self.pts.append((x, y))
        n = len(self.pts) - 1
        self.grid[(cx, cy)].append(n)
        return n


def _direction(verts: list, at_start: bool) -> tuple[float, float]:
    a, b = (verts[0], verts[1]) if at_start else (verts[-1], verts[-2])
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy) or 1.0
    return dx / L, dy / L


def plan_join(records: list[dict], tol: float, include_curves: bool = True,
              through_junctions: bool = False, no_fill: bool = True) -> dict[str, Any]:
    """Plan the joins. Returns {"changes": [{uuid, verts, closed, attrs?}],
    "drop": [uuids], "chains": n, "pieces_joined": n, "pieces": n,
    "skipped": {reason: n}}. Coordinates in document units."""
    groups: dict[tuple, list[tuple[dict, list]]] = defaultdict(list)
    skipped: dict[str, int] = defaultdict(int)
    for rec in records:
        if rec.get("kind") == "group":
            continue
        pts = piece_points(rec, include_curves)
        if pts is None:
            if rec.get("kind") in ("line", "polyline", "polygon"):
                skipped["closed or not joinable"] += 1
            continue
        if math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1]) <= tol and len(pts) > 2:
            skipped["already closed"] += 1
            continue
        groups[_attr_key(rec)].append((rec, pts))

    changes, drop = [], []
    chains = joined = pieces = 0
    for items in groups.values():
        pieces += len(items)
        nodes = _Nodes(tol)
        ends = []  # per piece: (start node, end node)
        at: dict[int, list[tuple[int, bool]]] = defaultdict(list)  # node -> [(piece, is_start)]
        for i, (rec, pts) in enumerate(items):
            a = nodes.node(pts[0][0], pts[0][1])
            b = nodes.node(pts[-1][0], pts[-1][1])
            ends.append((a, b))
            at[a].append((i, True))
            at[b].append((i, False))
        used = [False] * len(items)

        def step(node: int, came_from: int, came_dir: tuple[float, float]):
            """Next piece at `node` (not used), or None."""
            cands = [(p, s) for (p, s) in at[node] if p != came_from and not used[p]]
            if not cands:
                return None
            if len(at[node]) > 2:
                if not through_junctions:
                    return None
                # straightest continuation
                def turn(c):
                    p, s = c
                    d = _direction(items[p][1], s)
                    return -(d[0] * came_dir[0] + d[1] * came_dir[1])
                cands.sort(key=turn)
            return cands[0]

        for i in range(len(items)):
            if used[i]:
                continue
            used[i] = True
            chain = [(i, False)]  # (piece, reversed)
            # forward from the end of piece i
            node, last = ends[i][1], i
            d = _direction(items[i][1], False)
            d = (-d[0], -d[1])
            while True:
                nx = step(node, last, d)
                if nx is None:
                    break
                p, is_start = nx
                used[p] = True
                rev = not is_start
                chain.append((p, rev))
                node = ends[p][0] if rev else ends[p][1]
                pv = items[p][1][::-1] if rev else items[p][1]
                d = _direction(pv, False)
                d = (-d[0], -d[1])
                last = p
            # backward from the start of piece i (unless the chain closed)
            end_node = node
            closed = end_node == ends[i][0] and len(chain) > 1
            if not closed:
                node, last = ends[i][0], i
                d = _direction(items[i][1], True)
                d = (-d[0], -d[1])
                while True:
                    nx = step(node, last, d)
                    if nx is None:
                        break
                    p, is_start = nx
                    used[p] = True
                    rev = is_start  # we arrive at its start: walk it backwards to prepend
                    chain.insert(0, (p, rev))
                    node = ends[p][1] if rev else ends[p][0]
                    pv = items[p][1][::-1] if rev else items[p][1]
                    d = _direction(pv, True)
                    d = (-d[0], -d[1])
                    last = p
                closed = node == end_node and len(chain) > 1
            if len(chain) < 2:
                continue
            verts: list[list] = []
            for p, rev in chain:
                pv = [list(v) for v in (items[p][1][::-1] if rev else items[p][1])]
                if verts:
                    pv = pv[1:]  # shared end point: the previous piece's end point stays (exact join)
                verts += pv
            if closed:
                verts = verts[:-1]
            keep = items[chain[0][0]][0]
            ch = {"uuid": keep["uuid"], "verts": verts, "closed": bool(closed)}
            if no_fill and closed:
                ch["attrs"] = {"fill": "none"}
            changes.append(ch)
            drop += [items[p][0]["uuid"] for p, _r in chain[1:]]
            chains += 1
            joined += len(chain)
    return {"changes": changes, "drop": drop, "chains": chains, "pieces_joined": joined, "pieces": pieces,
            "objects_after": pieces - joined + chains, "skipped": dict(skipped)}
