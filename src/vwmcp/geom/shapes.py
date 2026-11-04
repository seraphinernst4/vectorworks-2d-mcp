"""Vectorworks object records (from vwlib2d.read_obj) -> exact shapes.

A Shape keeps the exact segments (lines + true circular arcs) for distance
checks and offers a shapely geometry for area operations. Arcs are sampled
on a global angle grid (see curves.discretize), so two objects sharing an
arc get identical sample points and no phantom slivers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely import make_valid
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from vwmcp.geom import curves as C

AREA_KINDS = {"rect", "oval", "polygon", "polyline", "arc", "rounded_rect", "freehand"}
TEXT_KINDS = {"text", "dimension", "locus", "symbol"}


@dataclass
class Shape:
    uuid: str
    kind: str
    cls: str
    layer: str
    name: str
    rec: dict
    rings: list = field(default_factory=list)  # list of segment lists
    closed: bool = False
    approx: bool = False
    order: int = 0
    _geom: BaseGeometry | None = None
    _chord: float | None = None
    _valid: bool | None = None
    _anchors: list | None = None
    _segidx: tuple | None = None
    _anchor_grid: tuple | None = None

    # ---- classification ----
    @property
    def is_area(self) -> bool:
        return self.closed and bool(self.rings) and self.kind in AREA_KINDS

    @property
    def is_line(self) -> bool:
        return (not self.closed) and bool(self.rings) and self.kind in AREA_KINDS | {"line"}

    @property
    def attrs(self) -> dict:
        return self.rec.get("attrs") or {}

    @property
    def filled(self) -> bool:
        return self.attrs.get("fill", "solid") != "none"

    @property
    def segments(self) -> list:
        return [s for r in self.rings for s in r]

    @property
    def editable(self) -> bool:
        """Vertices can be moved exactly (polygons, polylines, rects, lines)."""
        return self.kind in ("polygon", "polyline", "rect", "line") and not self.approx

    # ---- geometry ----
    def geometry(self, chord: float) -> BaseGeometry:
        if self._geom is not None and self._chord == chord:
            return self._geom
        if self.is_area:
            rings = [C.discretize(r, chord) for r in self.rings if r]
            rings = [r for r in rings if len(r) >= 3]
            if not rings:
                g = Polygon()
            else:
                g = Polygon(rings[0], rings[1:])
                self._valid = g.is_valid
                if not self._valid:
                    g = polygonal(make_valid(g))
        else:
            pts = C.discretize(self.segments, chord) if self.rings else []
            g = LineString(pts) if len(pts) >= 2 else LineString()
        self._geom, self._chord = g, chord
        return g

    def is_valid(self, chord: float) -> bool:
        self.geometry(chord)
        return True if self._valid is None else self._valid

    def anchors(self) -> list[tuple[float, float]]:
        """Exact corner points of the outline (segment endpoints). Empty for
        circles, ovals and approximated curves (their sample points are not
        drawing points)."""
        if self._anchors is not None:
            return self._anchors
        if self.approx or self.kind == "oval" or (self.kind == "arc" and self.closed):
            self._anchors = []
            return self._anchors
        out: list[tuple[float, float]] = []
        for ring in self.rings:
            for s in ring:
                if not out or out[-1] != s[1]:
                    out.append(s[1])
            if ring and not self.closed:
                out.append(ring[-1][2])
        seen, uniq = set(), []
        for p in out:
            if p not in seen:
                seen.add(p)
                uniq.append(p)
        self._anchors = uniq
        return uniq

    def nearest_anchor(self, p, within: float) -> tuple[float, tuple[float, float] | None]:
        """Nearest exact vertex within `within` (else (inf, None)); ties: the
        first in anchor order."""
        anchors = self.anchors()
        if len(anchors) <= 32:
            cands = enumerate(anchors)
        else:
            if self._anchor_grid is None or self._anchor_grid[0] != within:
                cell = max(within, 1e-12)
                grid: dict[tuple[int, int], list[int]] = {}
                for i, q in enumerate(anchors):
                    grid.setdefault((int(math.floor(q[0] / cell)), int(math.floor(q[1] / cell))), []).append(i)
                self._anchor_grid = (within, cell, grid)
            _w, cell, grid = self._anchor_grid
            cx, cy = int(math.floor(p[0] / cell)), int(math.floor(p[1] / cell))
            idx = sorted(i for gx in (cx - 1, cx, cx + 1) for gy in (cy - 1, cy, cy + 1)
                         for i in grid.get((gx, gy), ()))
            cands = ((i, anchors[i]) for i in idx)
        vd, vp = math.inf, None
        for _i, q in cands:
            dq = math.hypot(q[0] - p[0], q[1] - p[1])
            if dq < vd:
                vd, vp = dq, q
        if vd > within:
            return math.inf, None
        return vd, vp

    def _segment_index(self):
        """Grid of segment numbers (long outlines: only nearby segments are measured)."""
        if self._segidx is None:
            segs = self.segments
            boxes = [_seg_box(s) for s in segs]
            total = sum(max(b[2] - b[0], b[3] - b[1]) for b in boxes)
            cell = max(total / max(len(segs), 1) * 2.0, 1e-12)
            grid: dict[tuple[int, int], list[int]] = {}
            big = []
            for i, b in enumerate(boxes):
                x0, y0 = int(math.floor(b[0] / cell)), int(math.floor(b[1] / cell))
                x1, y1 = int(math.floor(b[2] / cell)), int(math.floor(b[3] / cell))
                if (x1 - x0 + 1) * (y1 - y0 + 1) > 64:
                    big.append(i)  # long segment: always measured
                    continue
                for gx in range(x0, x1 + 1):
                    for gy in range(y0, y1 + 1):
                        grid.setdefault((gx, gy), []).append(i)
            self._segidx = (segs, cell, grid, big)
        return self._segidx

    def distance_to(self, p, within: float | None = None) -> tuple[float, tuple[float, float] | None]:
        """Exact distance from p to the outline and the closest point. With
        `within`, only distances up to it count (farther: (inf, None)) - fast
        on long outlines."""
        if within is None:
            segs = self.segments
            cand = range(len(segs))
        else:
            segs, cell, grid, big = self._segment_index()  # cached segment list too
            r = within
        if within is None or len(segs) <= 32:
            cand = range(len(segs))
        else:
            x0, x1 = int(math.floor((p[0] - r) / cell)), int(math.floor((p[0] + r) / cell))
            y0, y1 = int(math.floor((p[1] - r) / cell)), int(math.floor((p[1] + r) / cell))
            if (x1 - x0 + 1) * (y1 - y0 + 1) > 4096:
                cand = range(len(segs))
            else:
                found = set(big)
                for gx in range(x0, x1 + 1):
                    for gy in range(y0, y1 + 1):
                        found.update(grid.get((gx, gy), ()))
                cand = sorted(found)
        best, bp = math.inf, None
        for i in cand:
            s = segs[i]
            d = C.point_segment_distance(p, s)
            if d < best:
                best, bp = d, C.closest_point_on_segment(p, s)
        if within is not None and best > within:
            return math.inf, None
        return best, bp


def _seg_box(s) -> tuple[float, float, float, float]:
    """Bounding box of a segment (arcs: their whole circle - safe and simple)."""
    if s[0] == "L":
        (ax, ay), (bx, by) = s[1], s[2]
        return (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))
    c, r = s[3], s[4]
    return (c[0] - r, c[1] - r, c[0] + r, c[1] + r)


def polygonal(g: BaseGeometry) -> BaseGeometry:
    """Only the polygon parts of a geometry (drops stray lines/points)."""
    polys = []
    for part in shapely_parts(g):
        if isinstance(part, Polygon) and not part.is_empty:
            polys.append(part)
    if not polys:
        return Polygon()
    return polys[0] if len(polys) == 1 else MultiPolygon(polys)


def shapely_parts(g: BaseGeometry):
    if g.is_empty:
        return []
    if hasattr(g, "geoms"):
        return [q for p in g.geoms for q in shapely_parts(p)]
    return [g]


def _ellipse_ring(center, width, height, angle_deg, chord):
    a, b = width / 2.0, height / 2.0
    n = C.arc_steps(max(a, b), chord)
    ca, sa = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    pts = []
    for k in range(n):
        t = 2 * math.pi * k / n
        x, y = a * math.cos(t), b * math.sin(t)
        pts.append((center[0] + x * ca - y * sa, center[1] + x * sa + y * ca))
    return C.points_segments(pts, True)


def from_record(rec: dict, order: int = 0, chord: float = 1e-6) -> Shape:
    s = Shape(uuid=rec["uuid"], kind=rec["kind"], cls=rec.get("class", ""),
              layer=rec.get("layer", ""), name=rec.get("name", ""), rec=rec, order=order)
    k = rec["kind"]
    try:
        if k in ("polygon", "polyline"):
            s.closed = bool(rec.get("closed"))
            try:
                rings = [C.polyline_segments(rec["vertices"], s.closed)]
                rings += [C.polyline_segments(h, True) for h in rec.get("holes") or []]
            except C.UnsupportedCurve:
                approx = rec.get("approx_polygon")
                if not approx:
                    raise
                s.approx = True
                rings = [C.points_segments(approx, s.closed)]
            s.rings = [r for r in rings if r]
        elif k == "rect":
            s.closed = True
            s.rings = [C.points_segments(rec["corners"], True)]
        elif k == "oval":
            s.closed = True
            s.rings = [_ellipse_ring(rec["center"], rec["width"], rec["height"], rec.get("angle", 0.0), chord)]
        elif k == "arc":
            if abs(abs(rec["sweep"]) - 360.0) < 1e-9:
                s.closed = True
                s.rings = [C.circle_segments(rec["center"], rec["radius"])]
            else:
                s.rings = [[C.arc_segment(rec["center"], rec["radius"], rec["start_angle"], rec["sweep"])]]
        elif k == "line":
            s.rings = [[("L", tuple(rec["p1"]), tuple(rec["p2"]))]] if rec["p1"] != rec["p2"] else []
        elif k in ("rounded_rect", "freehand") and rec.get("approx_polygon"):
            s.closed = k == "rounded_rect"
            s.approx = True
            s.rings = [C.points_segments(rec["approx_polygon"], s.closed)]
    except (KeyError, ValueError, ZeroDivisionError):
        s.rings = []
    return s


def from_records(recs: list[dict], chord: float) -> list[Shape]:
    return [from_record(r, i, chord) for i, r in enumerate(recs)]


class PointIndex:
    """Where the objects are, for 'is this point attached to another
    object?': exact corner points (anchors) in a grid, outlines by bbox."""

    def __init__(self, shapes: list[Shape], cell: float):
        self.cell = max(cell, 1e-12)
        self.grid: dict[tuple[int, int], list[tuple[tuple[float, float], Shape]]] = {}
        self.shapes = [s for s in shapes if s.rings]
        self.boxes = {}
        for s in self.shapes:
            for p in s.anchors():
                self.grid.setdefault(self._key(p), []).append((p, s))
            pts = [q for ring in s.rings for seg in ring for q in (seg[1], seg[2])]
            if pts:
                r = max((seg[4] for ring in s.rings for seg in ring if seg[0] == "A"), default=0.0)
                self.boxes[s.uuid] = (min(q[0] for q in pts) - r, min(q[1] for q in pts) - r,
                                      max(q[0] for q in pts) + r, max(q[1] for q in pts) + r)

    def _key(self, p):
        return (math.floor(p[0] / self.cell), math.floor(p[1] / self.cell))

    def vertex_of_other(self, p, uuid, eps, accept=None) -> bool:
        """Another object has a corner point at p."""
        kx, ky = self._key(p)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for q, s in self.grid.get((kx + dx, ky + dy), ()):
                    if s.uuid != uuid and (accept is None or accept(s)) and \
                            abs(q[0] - p[0]) <= eps and abs(q[1] - p[1]) <= eps:
                        return True
        return False

    def touches_other(self, p, uuid, eps, accept=None) -> bool:
        """Another object's outline passes through p (corner or edge)."""
        if self.vertex_of_other(p, uuid, eps, accept):
            return True
        for s in self.shapes:
            if s.uuid == uuid or (accept is not None and not accept(s)):
                continue
            b = self.boxes.get(s.uuid)
            if b is None or p[0] < b[0] - eps or p[0] > b[2] + eps or p[1] < b[1] - eps or p[1] > b[3] + eps:
                continue
            if s.distance_to(p, eps)[0] <= eps:
                return True
        return False


def units_tolerances(units: dict, tolerance_mm: float) -> dict:
    """Document-unit tolerances. mm_per_unit is rounded to 12 significant
    digits (25.4 / 0.0254 is not exactly 1000 in floating point)."""
    mpu = float(f"{units['mm_per_unit']:.12g}")
    return {
        "mpu": mpu,
        "tol": tolerance_mm / mpu,          # near-miss / snap distance
        "eps": 1e-6 / mpu,                  # 1 nanometre: "identical"
        "chord": 1e-3 / mpu,                # 1 micrometre arc sampling
        "min_thick": 5e-3 / mpu,            # reported overlap/gap thickness
    }
