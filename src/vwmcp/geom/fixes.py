"""Plan exact corrections from check results (pure geometry, no Vectorworks).

The planners return edit lists that vwlib2d applies in one job:
  poly changes  [{uuid, verts, closed, holes}]   -> replace_geometry
  line changes  [{uuid, p1, p2}]                 -> set_line_points
  deletions     [uuid]                           -> delete_objects
  subtractions  [{target, tools}]                -> boolean subtract
"""

from __future__ import annotations

import copy
import math

from vwmcp.geom import curves as C
from vwmcp.geom.shapes import Shape


class _Editable:
    """Vertex lists of one object that the planner may change."""

    def __init__(self, s: Shape):
        self.shape = s
        r = s.rec
        self.kind = s.kind
        if s.kind in ("polygon", "polyline"):
            self.rings = [copy.deepcopy(r["vertices"])] + copy.deepcopy(list(r.get("holes") or []))
            self.closed = bool(r.get("closed"))
        elif s.kind == "rect":
            self.rings = [[[p[0], p[1], 0, 0.0] for p in r["corners"]]]
            self.closed = True
        elif s.kind == "line":
            self.rings = [[[r["p1"][0], r["p1"][1], 0, 0.0], [r["p2"][0], r["p2"][1], 0, 0.0]]]
            self.closed = False
        else:
            raise ValueError("not editable")
        self.frozen: set[tuple[int, int]] = set()
        self.changed = False

    def find(self, p, eps):
        for ri, ring in enumerate(self.rings):
            for vi, v in enumerate(ring):
                if v[2] == 0 and abs(v[0] - p[0]) <= eps and abs(v[1] - p[1]) <= eps:
                    return ri, vi
        return None

    def neighbors_are_corners(self, ri, vi):
        ring = self.rings[ri]
        n = len(ring)
        closed = self.closed or ri > 0
        idx = []
        if vi > 0 or closed:
            idx.append((vi - 1) % n)
        if vi < n - 1 or closed:
            idx.append((vi + 1) % n)
        return all(ring[i][2] == 0 for i in idx)

    def move_arc_safe(self, ri, vi, target, eps):
        """Move a corner next to arc control vertices, keeping each adjacent
        arc on its circle. Returns False if that's impossible."""
        ring = self.rings[ri]
        closed = self.closed or ri > 0
        segs_old = C.polyline_segments(ring, closed)
        p = (ring[vi][0], ring[vi][1])
        # every arc that starts or ends at p must keep its center/radius
        for s in segs_old:
            if s[0] != "A":
                continue
            if C.dist(s[1], p) <= eps or C.dist(s[2], p) <= eps:
                if abs(C.dist(target, s[3]) - s[4]) > max(eps, 1e-12 * s[4]):
                    return False
        new_segs = []
        for s in segs_old:
            a = tuple(target) if C.dist(s[1], p) <= eps else s[1]
            b = tuple(target) if C.dist(s[2], p) <= eps else s[2]
            if s[0] == "L":
                new_segs.append(("L", a, b))
            else:
                new_segs.append(("A", a, b, s[3], s[4], s[5]))
        self.rings[ri] = segments_to_vertices(new_segs, closed)
        return True


def segments_to_vertices(segs, closed):
    """Exact Vectorworks vertices for a segment list (arcs as control
    vertices between corner vertices)."""
    verts: list = []
    for s in segs:
        if not verts:
            verts.append([s[1][0], s[1][1], 0, 0.0])
        if s[0] == "L":
            verts.append([s[2][0], s[2][1], 0, 0.0])
        else:
            verts += C.arc_to_vertices(s[1], s[2], s[3], s[5])
    if closed and len(verts) > 1 and verts[-1][:2] == verts[0][:2]:
        verts.pop()
    return verts


def _edges_containing(ring, closed, p, eps):
    n = len(ring)
    last = n if closed else n - 1
    for i in range(last):
        a, b = ring[i], ring[(i + 1) % n]
        if a[2] != 0 or b[2] != 0:
            continue
        if C.dist(a[:2], p) <= eps or C.dist(b[:2], p) <= eps:
            return None  # already a vertex there
        if C.point_segment_distance(p, ("L", tuple(a[:2]), tuple(b[:2]))) <= eps:
            return i
    return None


def plan_snap(issues, shapes, tol, fixed=(), share_vertices=True):
    """Move near-miss points exactly onto their targets (another object's
    vertex or edge). fixed: uuids that must not move."""
    by_uuid = {s.uuid: s for s in shapes}
    eds: dict[str, _Editable] = {}
    fixed = set(fixed)
    skipped, moved, inserted = [], 0, 0
    eps = tol["eps"]

    def ed(u):
        if u not in eds:
            s = by_uuid.get(u)
            if s is None or not s.editable:
                return None
            try:
                eds[u] = _Editable(s)
            except ValueError:
                return None
        return eds[u]

    moves = []
    for it in issues:
        if it["kind"] in ("near", "gap") and it.get("moves"):
            moves += it["moves"]
    moves.sort(key=lambda m: m["distance_mm"])
    order = {s.uuid: s.order for s in shapes}
    for m in moves:
        mover, other = m["uuid"], m["other"]
        p, t = tuple(m["point"]), tuple(m["target"])
        if m["target_kind"] == "node" and order.get(other, 0) > order.get(mover, 0) and other not in fixed:
            # node to node: the newer object (drawn later) adapts to the older one
            mover, other, p, t = other, mover, t, p
        e = ed(mover) if mover not in fixed else None
        loc = e.find(p, eps) if e else None
        if (e is None or loc is None or loc in e.frozen) and m["target_kind"] == "node":
            # the other object's point moves instead
            e2 = ed(other) if other not in fixed else None
            loc2 = e2.find(t, eps) if e2 else None
            if e2 is not None and loc2 is not None and loc2 not in e2.frozen:
                mover, other, e, loc, p, t = other, mover, e2, loc2, t, p
        if e is None or loc is None:
            skipped.append({"uuid": mover, "point": list(p),
                            "reason": "Objekt ist fixiert, kein Polygon/Linie oder Punkt ist ein Bogenpunkt"})
            continue
        if loc in e.frozen:
            skipped.append({"uuid": mover, "point": list(p), "reason": "Punkt wurde schon gefangen"})
            continue
        ri, vi = loc
        if e.neighbors_are_corners(ri, vi):
            e.rings[ri][vi] = [t[0], t[1], 0, 0.0]
        elif not e.move_arc_safe(ri, vi, t, eps):
            skipped.append({"uuid": mover, "point": list(p),
                            "reason": "Ziel liegt nicht auf dem Kreis des angrenzenden Bogens"})
            continue
        e.changed = True
        new_loc = e.find(t, eps)
        if new_loc:
            e.frozen.add(new_loc)
        moved += 1
        # freeze the target on the other object; optionally share the node
        e2 = ed(other)
        if e2 is not None:
            tl = e2.find(t, eps)
            if tl is not None:
                e2.frozen.add(tl)
            elif share_vertices and m["target_kind"] == "edge":
                for ri2, ring in enumerate(e2.rings):
                    k = _edges_containing(ring, e2.closed or ri2 > 0, t, eps)
                    if k is not None and e2.kind != "line":
                        ring.insert(k + 1, [t[0], t[1], 0, 0.0])
                        e2.frozen.add((ri2, k + 1))
                        e2.changed = True
                        inserted += 1
                        break
    polys, lines = [], []
    for u, e in eds.items():
        if not e.changed:
            continue
        if e.kind == "line":
            r = e.rings[0]
            lines.append({"uuid": u, "p1": r[0][:2], "p2": r[1][:2]})
        else:
            polys.append({"uuid": u, "verts": e.rings[0], "closed": e.closed, "holes": e.rings[1:]})
    return {"polys": polys, "lines": lines, "moved": moved, "inserted": inserted, "skipped": skipped}


def plan_close(issues, shapes, tol):
    by_uuid = {s.uuid: s for s in shapes}
    polys = []
    for it in issues:
        if it["kind"] != "open":
            continue
        s = by_uuid.get(it["uuids"][0])
        if s is None or s.kind not in ("polygon", "polyline"):
            continue
        verts = copy.deepcopy(s.rec["vertices"])
        if len(verts) > 2 and C.dist(verts[0][:2], verts[-1][:2]) <= tol["tol"]:
            verts.pop()
        polys.append({"uuid": s.uuid, "verts": verts, "closed": True,
                      "holes": copy.deepcopy(list(s.rec.get("holes") or []))})
    return {"polys": polys}


def plan_clean(shapes, tol, merge_short=False, uuids=None):
    """Remove duplicate and collinear corner vertices (and optionally merge
    corners closer than the tolerance)."""
    eps, lim = tol["eps"], tol["tol"] if merge_short else tol["eps"]
    polys, removed = [], 0
    for s in shapes:
        if s.kind not in ("polygon", "polyline") or s.approx or (uuids and s.uuid not in uuids):
            continue
        rings = [s.rec["vertices"]] + list(s.rec.get("holes") or [])
        new_rings, changed = [], False
        for ri, ring in enumerate(rings):
            closed = s.closed or ri > 0
            out = []
            for v in ring:
                if out and v[2] == 0 and out[-1][2] == 0 and C.dist(v[:2], out[-1][:2]) <= lim:
                    changed = True
                    removed += 1
                    continue
                out.append(list(v))
            if closed and len(out) > 1 and out[-1][2] == 0 and out[0][2] == 0 and \
                    C.dist(out[-1][:2], out[0][:2]) <= lim:
                out.pop()
                changed = True
                removed += 1
            i = 0
            while len(out) > 3 and i < len(out):
                n = len(out)
                if not closed and (i == 0 or i == n - 1):
                    i += 1
                    continue
                a, p, b = out[i - 1], out[i], out[(i + 1) % n]
                if a[2] == p[2] == b[2] == 0 and \
                        C.point_segment_distance(p[:2], ("L", tuple(a[:2]), tuple(b[:2]))) <= eps:
                    out.pop(i)
                    changed = True
                    removed += 1
                    continue
                i += 1
            new_rings.append(out)
        if changed:
            polys.append({"uuid": s.uuid, "verts": new_rings[0], "closed": s.closed, "holes": new_rings[1:]})
    return {"polys": polys, "removed": removed}


def plan_duplicates(issues, shapes):
    """Delete the lower copy of each duplicate pair (the top one stays)."""
    order = {s.uuid: s.order for s in shapes}
    delete = set()
    for it in issues:
        if it["kind"] != "duplicate":
            continue
        a, b = it["uuids"][:2]
        if a in delete or b in delete:
            continue
        delete.add(a if order.get(a, 0) < order.get(b, 0) else b)
    return {"delete": sorted(delete)}


def plan_overlaps(issues, shapes, priority=None):
    """For each overlap the winner keeps its area; the loser is clipped.
    priority: uuids or class names, first wins. Without priority the object
    on top (drawn later) wins, so the visible result stays the same."""
    by_uuid = {s.uuid: s for s in shapes}
    pri = list(priority or [])

    def rank(s: Shape):
        for i, key in enumerate(pri):
            if key == s.uuid or key == s.cls or (key.endswith("*") and s.cls.startswith(key[:-1])):
                return (0, i)
        return (1, -s.order)

    clips: dict[str, list[str]] = {}
    for it in issues:
        if it["kind"] != "overlap":
            continue
        a, b = (by_uuid.get(u) for u in it["uuids"][:2])
        if a is None or b is None:
            continue
        win, lose = (a, b) if rank(a) <= rank(b) else (b, a)
        clips.setdefault(lose.uuid, [])
        if win.uuid not in clips[lose.uuid]:
            clips[lose.uuid].append(win.uuid)
    # a clipped object must not be used as a tool afterwards in the same run
    order = sorted(clips, key=lambda u: rank(by_uuid[u]), reverse=True)
    return {"subtract": [{"target": u, "tools": clips[u]} for u in order]}


def deviation_report(expected_segs, got_rec, tol_eps):
    """Compare intended segments with what Vectorworks stored."""
    from vwmcp.geom.shapes import from_record
    s = from_record(got_rec)
    got = s.segments
    if not expected_segs:
        return None
    dev = C.segments_max_deviation(expected_segs, got)
    if math.isinf(dev):
        # different segmentation (e.g. Vectorworks merged collinear edges):
        # compare points both ways instead
        pts = [q for seg in expected_segs for q in (seg[1], seg[2])]
        dev = max((min(C.point_segment_distance(p, g) for g in got) for p in pts), default=0.0) if got else math.inf
    return dev
