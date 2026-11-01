"""Planning geometry: place elements in a rectangular room, check them
against the planning rules, fit standard layouts, generate variants.

Units are cm (like the Raumpilot books). Room interior from (0, 0) at the
bottom-left to (W, D); x right, y up. Walls: N (y = D), S (y = 0),
W (x = 0), E (x = W).

An element's local frame (same as the old asset catalogue): insertion point
in the middle of its back side (at the wall), the object reaching towards
-y: x in [-w/2, w/2], y in [-d, 0]. Standing at a wall turns it so its back
is at that wall: N 0°, W 90°, S 180°, E 270° (counter-clockwise).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

EPS = 1e-6
WALL_ROT = {"N": 0.0, "W": 90.0, "S": 180.0, "E": 270.0}
ROT_WALL = {v: k for k, v in WALL_ROT.items()}
OPPOSITE = {"N": "S", "S": "N", "E": "W", "W": "E"}
# facing direction (where the user stands) for an element at a wall
FACES = {"N": "S", "S": "N", "E": "W", "W": "E"}


# ---- small geometry ------------------------------------------------------------

@dataclass(frozen=True)
class Rect:
    x1: float
    y1: float
    x2: float
    y2: float

    @staticmethod
    def of(x, y, w, d) -> Rect:
        return Rect(min(x, x + w), min(y, y + d), max(x, x + w), max(y, y + d))

    @property
    def w(self) -> float:
        return self.x2 - self.x1

    @property
    def d(self) -> float:
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.d)

    def overlap(self, o: Rect, eps: float = EPS) -> float:
        """Overlap area (touching edges don't count)."""
        w = min(self.x2, o.x2) - max(self.x1, o.x1)
        d = min(self.y2, o.y2) - max(self.y1, o.y1)
        return w * d if w > eps and d > eps else 0.0

    def inside(self, o: Rect, eps: float = EPS) -> bool:
        return (self.x1 >= o.x1 - eps and self.y1 >= o.y1 - eps
                and self.x2 <= o.x2 + eps and self.y2 <= o.y2 + eps)

    def gap(self, o: Rect) -> float:
        """Clear distance between two rectangles (0 if they touch/overlap)."""
        dx = max(o.x1 - self.x2, self.x1 - o.x2, 0.0)
        dy = max(o.y1 - self.y2, self.y1 - o.y2, 0.0)
        return math.hypot(dx, dy)

    def clip(self, o: Rect) -> Rect | None:
        r = Rect(max(self.x1, o.x1), max(self.y1, o.y1), min(self.x2, o.x2), min(self.y2, o.y2))
        return r if r.w > EPS and r.d > EPS else None

    def as_list(self) -> list[float]:
        return [round(self.x1, 6), round(self.y1, 6), round(self.x2, 6), round(self.y2, 6)]


def rot(px: float, py: float, deg: float) -> tuple[float, float]:
    a = math.radians(deg)
    c, s = round(math.cos(a), 15), round(math.sin(a), 15)
    return px * c - py * s, px * s + py * c


def local_rect_to_world(lx1, ly1, lx2, ly2, ix, iy, deg) -> Rect:
    pts = [rot(x, y, deg) for x, y in ((lx1, ly1), (lx2, ly1), (lx2, ly2), (lx1, ly2))]
    xs = [ix + p[0] for p in pts]
    ys = [iy + p[1] for p in pts]
    return Rect(round(min(xs), 9), round(min(ys), 9), round(max(xs), 9), round(max(ys), 9))


# ---- element specs ---------------------------------------------------------------

@dataclass
class Area:
    """A movement area beside an element (local frame).
    side: front (towards -y) | left (-x) | right (+x) | back (+y, rare) |
    either (left or right, whichever is free - a single bed's access side).
    w: extent along the side (None = the element's side length); d: depth
    away from the element; d_extra: larger depth when another element faces
    it (opposite arrangement) - checked as a distance, drawn lighter.
    align: where along the side (center | start | end | any); start = the
    left end seen from the front. offset: shift along the side (cm)."""
    side: str = "front"
    w: float | None = None
    d: float = 0.0
    d_extra: float | None = None
    align: str = "center"
    offset: float = 0.0
    note: str = ""

    def local(self, ew: float, ed: float, depth: float | None = None, shift: float = 0.0,
              side: str | None = None) -> tuple[float, float, float, float]:
        """The area in the element's frame; shift moves an align="any" area
        along the side, side resolves "either" (see resolve_shifts())."""
        d = self.d if depth is None else depth
        side = side or self.side
        if side == "either":
            side = "left"
        if side in ("front", "back"):
            w = ew if self.w is None else self.w
            if self.align == "start":
                x1 = -ew / 2
            elif self.align == "end":
                x1 = ew / 2 - w
            else:
                x1 = -w / 2
            x1 += self.offset + shift
            if side == "front":
                return x1, -ed - d, x1 + w, -ed
            return x1, 0.0, x1 + w, d
        # along the side, from the back (wall, s = 0) to the front (s = ed)
        w = ed if self.w is None else self.w
        s0 = {"end": ed - w, "center": (ed - w) / 2}.get(self.align, 0.0) + self.offset + shift
        top, bottom = -s0, -(s0 + w)
        if side == "left":
            return -ew / 2 - d, bottom, -ew / 2, top
        return ew / 2, bottom, ew / 2 + d, top


@dataclass
class Spec:
    """What the engine needs to know about an element (cm)."""
    id: str
    w: float
    d: float
    short: str | None = None          # VDI 6000 code for side distances (WB, WC, DU, BW ...)
    wall: bool = True                 # must stand at a wall
    areas: list[Area] = field(default_factory=list)
    overlap_ok: tuple = ()            # element ids whose Stellfläche may overlap (chair under table)
    corner_ok: bool = False           # may sit in a corner touching both walls (shower, bath)
    name: str = ""
    walkable: bool = False            # floor-level (bodengleicher Duschplatz): never blocks movement areas
    group: str | None = None          # same group (kitchen row) may stand in each other's areas (corners)
    movable: bool = False             # chairs: stand in movement areas and under tables/desks

    def shift_range(self, a: Area) -> list[float]:
        """Positions to try for an align="any" area: centred first, then
        sliding along the side while it still overlaps the element by at
        least half of the narrower one (5 cm steps)."""
        if a.align != "any":
            return [0.0]
        ew = self.w if a.side in ("front", "back") else self.d
        aw = ew if a.w is None else a.w
        reach = max(0.0, (aw + ew) / 2 - 0.5 * min(aw, ew))
        out, k = [0.0], 5.0
        while k <= reach + EPS:
            out += [k, -k]
            k += 5.0
        return out


@dataclass
class Placed:
    spec: Spec
    ix: float           # insertion point (middle of the back side)
    iy: float
    deg: float          # rotation (0 = back at N)
    wall: str | None    # N/E/S/W or None (free)
    tag: str = ""
    shifts: dict = field(default_factory=dict)  # area index -> chosen shift (align="any")
    sides: dict = field(default_factory=dict)   # area index -> chosen side (side="either")

    @property
    def rect(self) -> Rect:
        s = self.spec
        return local_rect_to_world(-s.w / 2, -s.d, s.w / 2, 0.0, self.ix, self.iy, self.deg)

    def area_rects(self, extra: bool = False) -> list[tuple[Area, Rect]]:
        out = []
        for i, a in enumerate(self.spec.areas):
            depth = (a.d_extra if extra and a.d_extra else a.d)
            lx1, ly1, lx2, ly2 = a.local(self.spec.w, self.spec.d, depth, self.shifts.get(i, 0.0), self.sides.get(i))
            out.append((a, local_rect_to_world(lx1, ly1, lx2, ly2, self.ix, self.iy, self.deg)))
        return out

    def area_at(self, i: int, shift: float, side: str | None = None) -> Rect:
        a = self.spec.areas[i]
        lx1, ly1, lx2, ly2 = a.local(self.spec.w, self.spec.d, None, shift, side)
        return local_rect_to_world(lx1, ly1, lx2, ly2, self.ix, self.iy, self.deg)

    @property
    def facing(self) -> tuple[float, float]:
        """Unit vector from the element towards its user."""
        x, y = rot(0.0, -1.0, self.deg)
        return round(x, 9), round(y, 9)

    def to_dict(self) -> dict:
        r = self.rect
        return {"element": self.spec.id, "x": round(r.x1, 3), "y": round(r.y1, 3), "w": round(r.w, 3),
                "d": round(r.d, 3), "wall": self.wall, "rotation": self.deg % 360.0,
                "insertion": [round(self.ix, 3), round(self.iy, 3)]}


def at_wall(spec: Spec, wall: str, pos: float, W: float, D: float, tag: str = "") -> Placed:
    """Element against a wall; pos = distance of its left edge (seen from
    inside the room, facing the wall) from that wall's left corner."""
    # walking along each wall with the wall in front of you, "left corner":
    # N: (0, D) -> x grows; E: (W, D) -> y falls; S: (W, 0) -> x falls; W: (0, 0) -> y grows
    c = pos + spec.w / 2
    if wall == "N":
        return Placed(spec, c, D, 0.0, "N", tag)
    if wall == "E":
        return Placed(spec, W, D - c, 270.0, "E", tag)
    if wall == "S":
        return Placed(spec, W - c, 0.0, 180.0, "S", tag)
    if wall == "W":
        return Placed(spec, 0.0, c, 90.0, "W", tag)
    raise ValueError(wall)


def wall_length(wall: str, W: float, D: float) -> float:
    return W if wall in ("N", "S") else D


# ---- doors ---------------------------------------------------------------------

@dataclass
class Door:
    wall: str
    offset: float      # from the wall's left corner seen from inside (as at_wall)
    width: float = 80.0
    swing: str | None = "in"   # in | out | None (sliding)
    hinge: str = "left"        # left | right seen from inside

    def span(self, W: float, D: float) -> Rect:
        """The opening as a thin rect in the wall plane."""
        a, b = self.offset, self.offset + self.width
        if self.wall == "N":
            return Rect(a, D, b, D)
        if self.wall == "S":
            return Rect(W - b, 0.0, W - a, 0.0)
        if self.wall == "W":
            return Rect(0.0, a, 0.0, b)
        return Rect(W, D - b, W, D - a)

    def swing_rect(self, W: float, D: float) -> Rect | None:
        """Bounding square of the inward door swing (None if not inward)."""
        if self.swing != "in":
            return None
        p = at_wall(Spec("door", self.width, self.width), self.wall, self.offset, W, D)
        return p.rect

    def front_rect(self, W: float, D: float, depth: float) -> Rect:
        return at_wall(Spec("door", self.width, depth), self.wall, self.offset, W, D).rect


# ---- side distances --------------------------------------------------------------

def side_distance(table: dict, a: str | None, b: str | None) -> float:
    """Required clear distance between two side-by-side elements (or element
    and wall: b = "Wand") from a matrix {a: {b: cm}}; 0 if unknown."""
    if not a or not b:
        return 0.0
    v = (table.get(a) or {}).get(b)
    if v is None:
        v = (table.get(b) or {}).get(a)
    return float(v or 0.0)


# ---- checking --------------------------------------------------------------------

@dataclass
class Issue:
    kind: str
    message: str
    elements: tuple = ()
    severity: str = "error"
    where: list | None = None

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "severity": self.severity, "message": self.message,
             "elements": list(self.elements)}
        if self.where is not None:
            d["where"] = self.where
        return d


def _blocks(q: Placed, p: Placed) -> bool:
    """Does q's Stellfläche count as an obstacle for p's movement areas?"""
    if q is p or q.spec.walkable or q.spec.movable:
        return False
    if q.spec.id in p.spec.overlap_ok or p.spec.id in q.spec.overlap_ok:
        return False
    return not (p.spec.group and p.spec.group == q.spec.group)


def resolve_shifts(placed: list[Placed], W: float, D: float) -> None:
    """Choose where align="any" areas sit: the first position (centred
    first) that is inside the room and free of other objects."""
    room = Rect(0.0, 0.0, W, D)
    for p in placed:
        p.shifts, p.sides = {}, {}
        for i, a in enumerate(p.spec.areas):
            if a.align != "any" and a.side != "either":
                continue
            best, free = None, False
            for side in (("left", "right") if a.side == "either" else (None,)):
                for sh in p.spec.shift_range(a):
                    r = p.area_at(i, sh, side)
                    if r.inside(room) and not any(r.clip(q.rect) for q in placed if _blocks(q, p)):
                        best, free = (sh, side), True
                        break
                    if best is None and r.inside(room):
                        best = (sh, side)  # inside at least
                if free:
                    break
            sh, side = best if best is not None else (0.0, None)
            p.shifts[i] = sh
            if side:
                p.sides[i] = side


def _label(p: Placed) -> str:
    return p.tag or p.spec.id


def check(placed: list[Placed], W: float, D: float, doors: list[Door] = (), sides: dict | None = None,
          door_reveal: float = 10.0, door_front: float = 0.0) -> list[Issue]:
    """Check a layout: everything inside the room, no overlaps of
    Stellflächen, movement areas free of other objects, side distances,
    opposite arrangement depth, door distances and swing."""
    room = Rect(0.0, 0.0, W, D)
    issues: list[Issue] = []
    sides = sides or {}
    resolve_shifts(placed, W, D)
    for p in placed:
        if not p.rect.inside(room):
            issues.append(Issue("outside", "%s reicht aus dem Raum" % _label(p), (_label(p),), where=p.rect.as_list()))
        for a, r in p.area_rects():
            if not r.inside(room):
                c = r.clip(room)
                missing = r.area - (c.area if c else 0.0)
                issues.append(Issue("movement_outside", "Bewegungsfläche von %s liegt %.0f cm² außerhalb des Raums"
                                    % (_label(p), missing), (_label(p),), where=r.as_list()))
    for p, q in itertools.combinations(placed, 2):
        if q.spec.id in p.spec.overlap_ok or p.spec.id in q.spec.overlap_ok:
            continue
        if (p.spec.walkable and q.spec.walkable) or p.spec.movable or q.spec.movable:
            continue
        ov = p.rect.overlap(q.rect)
        if ov > 0:
            issues.append(Issue("overlap", "%s und %s überschneiden sich" % (_label(p), _label(q)),
                                (_label(p), _label(q)), where=p.rect.clip(q.rect).as_list()))
    for p in placed:
        for a, r in p.area_rects():
            for q in placed:
                if not _blocks(q, p):
                    continue
                c = r.clip(q.rect)
                if c is not None:
                    issues.append(Issue("movement_blocked", "Bewegungsfläche von %s ist durch %s verstellt (%.0f × %.0f cm)"
                                        % (_label(p), _label(q), c.w, c.d), (_label(p), _label(q)),
                                        where=c.as_list()))
    # opposite arrangement: facing elements need the larger depth between them
    for p, q in itertools.combinations(placed, 2):
        fp, fq = p.facing, q.facing
        if abs(fp[0] + fq[0]) > 1e-6 or abs(fp[1] + fq[1]) > 1e-6:
            continue  # not facing each other
        if p.spec.movable or q.spec.movable or p.spec.walkable or q.spec.walkable:
            continue
        cp = ((p.rect.x1 + p.rect.x2) / 2, (p.rect.y1 + p.rect.y2) / 2)
        cq = ((q.rect.x1 + q.rect.x2) / 2, (q.rect.y1 + q.rect.y2) / 2)
        if (cq[0] - cp[0]) * fp[0] + (cq[1] - cp[1]) * fp[1] <= 0:
            continue  # back to back, not front to front
        need = max([a.d_extra or a.d for a in p.spec.areas if a.side == "front"]
                   + [a.d_extra or a.d for a in q.spec.areas if a.side == "front"] + [0.0])
        rp, rq = p.rect, q.rect
        if fp[1] != 0:  # facing in y: overlap in x?
            span = min(rp.x2, rq.x2) - max(rp.x1, rq.x1)
            gap = rq.y1 - rp.y2 if rp.y2 <= rq.y1 else rp.y1 - rq.y2
        else:
            span = min(rp.y2, rq.y2) - max(rp.y1, rq.y1)
            gap = rq.x1 - rp.x2 if rp.x2 <= rq.x1 else rp.x1 - rq.x2
        if span > EPS and gap < need - EPS and gap >= -EPS:
            issues.append(Issue("opposite", "%s und %s stehen sich gegenüber: %.0f cm frei, %.0f cm nötig"
                                % (_label(p), _label(q), gap, need), (_label(p), _label(q))))
    # side distances (elements at the same wall, and to the corner walls)
    for p, q in itertools.combinations(placed, 2):
        if not p.wall or p.wall != q.wall:
            continue
        need = side_distance(sides, p.spec.short, q.spec.short)
        if need <= 0:
            continue
        g = p.rect.gap(q.rect)
        if g < need - EPS:
            issues.append(Issue("side_distance", "%s und %s: seitlich %.0f cm, mindestens %.0f cm"
                                % (_label(p), _label(q), g, need), (_label(p), _label(q))))
    for p in placed:
        if not p.wall:
            continue
        need = side_distance(sides, p.spec.short, "Wand")
        if need <= 0:
            continue
        r = p.rect
        if p.wall in ("N", "S"):
            g = min(r.x1, W - r.x2)
        else:
            g = min(r.y1, D - r.y2)
        if g < need - EPS and not (p.spec.corner_ok and g < EPS):
            issues.append(Issue("wall_distance", "%s: %.0f cm zur seitlichen Wand, mindestens %.0f cm"
                                % (_label(p), g, need), (_label(p),)))
    # doors
    for dr in doors:
        span = dr.span(W, D)
        for p in placed:
            r = p.rect
            if p.wall == dr.wall:
                if dr.wall in ("N", "S"):
                    g = max(span.x1 - r.x2, r.x1 - span.x2)
                else:
                    g = max(span.y1 - r.y2, r.y1 - span.y2)
                if g < door_reveal - EPS:
                    issues.append(Issue("door_distance", "%s: %.0f cm zur Türlaibung, mindestens %.0f cm"
                                        % (_label(p), max(g, 0.0), door_reveal), (_label(p),)))
            sw = dr.swing_rect(W, D)
            if sw is not None and sw.overlap(r) > 0:
                issues.append(Issue("door_swing", "die nach innen aufschlagende Tür trifft %s" % _label(p),
                                    (_label(p),), where=sw.clip(r).as_list()))
            if door_front > 0:
                fr = dr.front_rect(W, D, door_front)
                if fr.overlap(r) > 0:
                    issues.append(Issue("door_front", "vor der Tür fehlt die freie Fläche (%s steht darin)"
                                        % _label(p), (_label(p),)))
    return issues


# ---- generating layouts ------------------------------------------------------------

@dataclass
class Layout:
    W: float
    D: float
    placed: list[Placed]
    doors: list[Door] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    source: str = ""
    name: str = ""
    score: float = 0.0

    def to_dict(self) -> dict:
        return {"name": self.name, "source": self.source, "W": self.W, "D": self.D,
                "elements": [p.to_dict() for p in self.placed],
                "doors": [d.__dict__ for d in self.doors],
                "issues": [i.to_dict() for i in self.issues], "score": round(self.score, 2)}


def _pack(specs: list[Spec], wall: str, W: float, D: float, sides: dict, start: float, mode: str) -> list[Placed] | None:
    """Pack elements along one wall with their side distances."""
    L = wall_length(wall, W, D)
    gaps = [side_distance(sides, specs[0].short, "Wand") if not specs[0].corner_ok else 0.0]
    for a, b in zip(specs, specs[1:]):
        gaps.append(side_distance(sides, a.short, b.short))
    tail = side_distance(sides, specs[-1].short, "Wand") if not specs[-1].corner_ok else 0.0
    total = sum(s.w for s in specs) + sum(gaps[1:])
    if mode == "start":
        pos = max(start, gaps[0])
    elif mode == "end":
        pos = L - tail - total
    else:  # centred on the wall
        pos = (L - total) / 2
    if pos < gaps[0] - EPS or pos + total > L - tail + EPS:
        return None
    out = []
    for i, s in enumerate(specs):
        if i:
            pos += gaps[i]
        out.append(at_wall(s, wall, pos, W, D))
        pos += s.w
    return out


def _score(lay: Layout) -> float:
    """Lower is better: issues first, then spread of the installation walls."""
    walls = {p.wall for p in lay.placed if p.wall}
    err = sum(1 for i in lay.issues if i.severity == "error")
    return err * 1000 + len(walls) * 10


def generate(specs: list[Spec], W: float, D: float, doors: list[Door] = (), sides: dict | None = None,
             limit: int = 8, door_reveal: float = 10.0, door_front: float = 0.0,
             max_tries: int = 20000) -> list[Layout]:
    """Variants for wall elements in a rectangular room: every assignment of
    elements to walls, every order along a wall, packed from either corner
    or centred; checked; the best distinct ones (fewest problems, fewest
    installation walls) first."""
    sides = sides or {}
    walls = ["N", "E", "S", "W"]
    seen, out, tries = set(), [], 0
    n = len(specs)
    for assign in itertools.product(walls, repeat=n):
        by_wall: dict[str, list[Spec]] = {}
        for s, w in zip(specs, assign):
            by_wall.setdefault(w, []).append(s)
        if any(sum(s.w for s in ss) > wall_length(w, W, D) + EPS for w, ss in by_wall.items()):
            continue
        orders = [list(itertools.permutations(ss)) for ss in by_wall.values()]
        for combo in itertools.product(*orders):
            for modes in itertools.product(("start", "end", "center"), repeat=len(combo)):
                tries += 1
                if tries > max_tries:
                    break
                placed: list[Placed] = []
                ok = True
                for (w, _), order, mode in zip(by_wall.items(), combo, modes):
                    part = _pack(list(order), w, W, D, sides, 0.0, mode)
                    if part is None:
                        ok = False
                        break
                    placed += part
                if not ok:
                    continue
                key = tuple(sorted((p.spec.id, round(p.rect.x1, 1), round(p.rect.y1, 1), p.wall) for p in placed))
                if key in seen:
                    continue
                seen.add(key)
                lay = Layout(W, D, placed, list(doors), name="Variante")
                lay.issues = check(placed, W, D, doors, sides, door_reveal, door_front)
                lay.score = _score(lay)
                out.append(lay)
    out.sort(key=lambda lay: lay.score)
    best, shapes = [], set()
    for lay in out:
        sig = tuple(sorted((p.spec.id, p.wall) for p in lay.placed))
        if sig in shapes and len(best) < limit and lay.score >= 1000:
            continue
        shapes.add(sig)
        best.append(lay)
        if len(best) >= limit:
            break
    for i, lay in enumerate(best):
        lay.name = "Variante %d" % (i + 1)
    return best


# ---- standard layouts (from the book) -----------------------------------------------

def transform_point(x: float, y: float, W: float, D: float, turn: int, mirror: bool) -> tuple[float, float]:
    """Turn a point of a W x D room by 90° steps (ccw) and/or mirror it
    left-right. Returns coordinates in the new room."""
    if mirror:
        x = W - x
    for _ in range(turn % 4):
        x, y, W, D = D - y, x, D, W
    return x, y


def turned_size(W: float, D: float, turn: int) -> tuple[float, float]:
    return (W, D) if turn % 2 == 0 else (D, W)


def turn_wall(wall: str | None, turn: int, mirror: bool) -> str | None:
    if wall is None or wall == "none":
        return None
    if mirror:
        wall = {"E": "W", "W": "E"}.get(wall, wall)
    order = ["N", "W", "S", "E"]  # ccw
    return order[(order.index(wall) + turn) % 4]
