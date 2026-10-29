"""Exact 2D curve math for Vectorworks geometry.

Pure Python, stdlib only, Python 3.9 compatible: the same code runs on the
server and (in tests) inside Vectorworks, where it is checked against
Vectorworks' own polygonization.

Segments are tuples:
    ("L", p0, p1)                         straight line p0 -> p1
    ("A", p0, p1, center, radius, ccw)    circular arc p0 -> p1 around center

Vectorworks polyline vertices are [x, y, vertex_type, radius] with
vertex_type 0 = corner, 1 = bezier, 2 = cubic, 3 = arc, 4 = radius. An arc
vertex is a *control point*: Vectorworks replaces the corner at that point
with a circular arc tangent to both adjacent edges (a fillet). Radius 0, or a
radius too large to fit, means "the biggest radius that fits"; an edge shared
with another arc vertex offers each of them half its length. (Verified live
against Vectorworks 2026 polygonization, see tests/test_curves.py.)
"""

import math

CORNER, BEZIER, CUBIC, ARC, RADIUS = 0, 1, 2, 3, 4
TWO_PI = 2.0 * math.pi


class UnsupportedCurve(ValueError):
    """Geometry that this module cannot evaluate exactly (bezier/cubic...)."""


# ---- small vector helpers ---------------------------------------------------

def sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def mul(a, s):
    return (a[0] * s, a[1] * s)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def norm(a):
    return math.hypot(a[0], a[1])


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def unit(a):
    n = norm(a)
    if n == 0.0:
        raise ValueError("zero-length vector")
    return (a[0] / n, a[1] / n)


def pt(p):
    return (float(p[0]), float(p[1]))


# ---- Vectorworks polylines --------------------------------------------------

def _fillet(prev, v, nxt, radius, avail_prev, avail_next):
    """Tangent arc replacing the corner at v. Returns (t1, t2, center, r, ccw)
    or None when the corner is straight or degenerate. Uses no trig, so
    axis-aligned corners come out exact."""
    lp, ln = dist(prev, v), dist(nxt, v)
    if lp == 0.0 or ln == 0.0:
        return None
    u = mul(sub(prev, v), 1.0 / lp)
    w = mul(sub(nxt, v), 1.0 / ln)
    cosphi = max(-1.0, min(1.0, dot(u, w)))
    sinphi = abs(cross(u, w))
    if sinphi < 1e-12:
        return None  # spike or straight: nothing to round
    tan_half = sinphi / (1.0 + cosphi)
    d_req = radius * (1.0 + cosphi) / sinphi if radius > 0.0 else float("inf")
    d = min(d_req, avail_prev, avail_next)
    if d <= 0.0:
        return None
    r = radius if d == d_req else d * tan_half
    t1 = add(v, mul(u, d))
    t2 = add(v, mul(w, d))
    n = unit(sub(w, mul(u, cosphi)))  # normal at t1 towards the centre
    c = add(t1, mul(n, r))
    ccw = cross(sub(t1, c), sub(t2, c)) > 0.0
    return (t1, t2, c, r, ccw)


def polyline_segments(vertices, closed):
    """Segments of a Vectorworks polygon/polyline.

    vertices: [[x, y], ...] or [[x, y, type, radius], ...]."""
    vs_ = []
    for v in vertices:
        t = int(v[2]) if len(v) > 2 else CORNER
        r = float(v[3]) if len(v) > 3 else 0.0
        vs_.append(((float(v[0]), float(v[1])), t, r))
    n = len(vs_)
    if n == 0:
        return []
    if n == 1:
        return []
    if any(t in (BEZIER, CUBIC, RADIUS) for _, t, _ in vs_):
        raise UnsupportedCurve("bezier/cubic/radius vertices")

    def is_arc(i):
        if not closed and (i == 0 or i == n - 1):
            return False  # Vectorworks ignores arcs at open ends
        return vs_[i][1] == ARC

    entry, exit_, arcs = [None] * n, [None] * n, [None] * n
    for i in range(n):
        p = vs_[i][0]
        entry[i] = exit_[i] = p
        if not is_arc(i) or n < 3:
            continue
        ip, inx = (i - 1) % n, (i + 1) % n
        prev, nxt = vs_[ip][0], vs_[inx][0]
        avail_prev = dist(prev, p) * (0.5 if is_arc(ip) else 1.0)
        avail_next = dist(nxt, p) * (0.5 if is_arc(inx) else 1.0)
        f = _fillet(prev, p, nxt, vs_[i][2], avail_prev, avail_next)
        if f is None:
            continue
        t1, t2, c, r, ccw = f
        # an explicit corner at the tangent point (how arcs are drawn here)
        # wins over the recomputed point, which may differ by one ulp
        tiny = 1e-10 * max(1.0, norm(p))
        if not is_arc(ip) and dist(t1, prev) <= tiny:
            t1 = prev
        if not is_arc(inx) and dist(t2, nxt) <= tiny:
            t2 = nxt
        entry[i], exit_[i] = t1, t2
        arcs[i] = ("A", t1, t2, c, r, ccw)

    segs = []
    last = n if closed else n - 1
    for i in range(last):
        j = (i + 1) % n
        if arcs[i] is not None:
            segs.append(arcs[i])
        a, b = exit_[i], entry[j]
        if a != b:
            segs.append(("L", a, b))
    if not closed and n >= 1 and arcs[n - 1] is not None:
        segs.append(arcs[n - 1])
    if closed and segs and arcs[0] is not None:
        # start the ring at vertex 0's arc for a stable order
        pass
    return segs


# ---- other primitives -------------------------------------------------------

def arc_segment(center, radius, start_deg, sweep_deg):
    """Arc object (Vectorworks start angle + signed sweep in degrees)."""
    c = pt(center)
    a0 = math.radians(start_deg)
    a1 = math.radians(start_deg + sweep_deg)
    p0 = (c[0] + radius * math.cos(a0), c[1] + radius * math.sin(a0))
    p1 = (c[0] + radius * math.cos(a1), c[1] + radius * math.sin(a1))
    return ("A", p0, p1, c, float(radius), sweep_deg > 0)


def circle_segments(center, radius):
    """Full circle as two half arcs (start/end points are exact)."""
    c = pt(center)
    r = float(radius)
    p0 = (c[0] + r, c[1])
    p1 = (c[0] - r, c[1])
    return [("A", p0, p1, c, r, True), ("A", p1, p0, c, r, True)]


def points_segments(points, closed):
    pts = [pt(p) for p in points]
    segs = []
    for i in range(len(pts) - 1):
        if pts[i] != pts[i + 1]:
            segs.append(("L", pts[i], pts[i + 1]))
    if closed and len(pts) > 2 and pts[-1] != pts[0]:
        segs.append(("L", pts[-1], pts[0]))
    return segs


# ---- arc helpers ------------------------------------------------------------

def arc_sweep(seg):
    """Signed sweep (radians) of an arc segment, in (-2pi, 2pi]."""
    _, p0, p1, c, r, ccw = seg
    a0 = math.atan2(p0[1] - c[1], p0[0] - c[0])
    a1 = math.atan2(p1[1] - c[1], p1[0] - c[0])
    s = a1 - a0
    if ccw:
        while s <= 0.0:
            s += TWO_PI
    else:
        while s >= 0.0:
            s -= TWO_PI
    return s


def arc_steps(radius, chord_tol):
    """Points per full circle for a chord error <= chord_tol, as a power of
    two so arcs on the same circle share sample angles."""
    if radius <= chord_tol:
        return 16
    step = 2.0 * math.acos(max(-1.0, 1.0 - chord_tol / radius))
    n = max(16, int(math.ceil(TWO_PI / step)))
    k = 1
    while k < n:
        k *= 2
    return min(k, 1 << 16)


def discretize(segs, chord_tol):
    """Point list along segments. Arc samples sit on a global angle grid, so
    two shapes sharing an arc get identical points (no phantom slivers)."""
    out = []
    for s in segs:
        if not out:
            out.append(s[1])
        if s[0] == "L":
            if s[2] != out[-1]:
                out.append(s[2])
            continue
        _, p0, p1, c, r, ccw = s
        n = arc_steps(r, chord_tol)
        d = TWO_PI / n
        a0 = math.atan2(p0[1] - c[1], p0[0] - c[0])
        sw = arc_sweep(s)
        a1 = a0 + sw
        if ccw:
            k = math.floor(a0 / d) + 1
            while k * d < a1 - 1e-12:
                a = k * d
                out.append((c[0] + r * math.cos(a), c[1] + r * math.sin(a)))
                k += 1
        else:
            k = math.ceil(a0 / d) - 1
            while k * d > a1 + 1e-12:
                a = k * d
                out.append((c[0] + r * math.cos(a), c[1] + r * math.sin(a)))
                k -= 1
        out.append(p1)
    return out


def point_segment_distance(p, s):
    """Distance from point p to a segment (line or arc)."""
    if s[0] == "L":
        a, b = s[1], s[2]
        ab = sub(b, a)
        L2 = dot(ab, ab)
        if L2 == 0.0:
            return dist(p, a)
        t = max(0.0, min(1.0, dot(sub(p, a), ab) / L2))
        return dist(p, add(a, mul(ab, t)))
    _, p0, p1, c, r, ccw = s
    ang = math.atan2(p[1] - c[1], p[0] - c[0])
    if angle_in_arc(ang, s):
        return abs(dist(p, c) - r)
    return min(dist(p, p0), dist(p, p1))


def closest_point_on_segment(p, s):
    if s[0] == "L":
        a, b = s[1], s[2]
        ab = sub(b, a)
        L2 = dot(ab, ab)
        if L2 == 0.0:
            return a
        t = max(0.0, min(1.0, dot(sub(p, a), ab) / L2))
        if t == 0.0:
            return a
        if t == 1.0:
            return b
        return add(a, mul(ab, t))
    _, p0, p1, c, r, ccw = s
    ang = math.atan2(p[1] - c[1], p[0] - c[0])
    if angle_in_arc(ang, s) and dist(p, c) > 0.0:
        return (c[0] + r * math.cos(ang), c[1] + r * math.sin(ang))
    return p0 if dist(p, p0) <= dist(p, p1) else p1


def angle_in_arc(ang, s):
    _, p0, p1, c, r, ccw = s
    a0 = math.atan2(p0[1] - c[1], p0[0] - c[0])
    sw = arc_sweep(s)
    rel = ang - a0
    if ccw:
        rel = rel % TWO_PI
        return rel <= sw + 1e-12
    rel = -((-rel) % TWO_PI)
    return rel >= sw - 1e-12


# ---- building Vectorworks polylines from arc segments -----------------------

def circle_center_3pt(a, b, c):
    """Center of the circle through three points."""
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if d == 0.0:
        raise ValueError("points are collinear; no arc through them")
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay)
          + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx)
          + (cx * cx + cy * cy) * (bx - ax)) / d
    return (ux, uy)


def _tangent_intersection(q0, q1, c, ccw):
    """Intersection of the circle tangents at q0 and q1 (the arc control
    vertex). Exact for axis-aligned quarter arcs."""
    t0 = (-(q0[1] - c[1]), q0[0] - c[0]) if ccw else (q0[1] - c[1], -(q0[0] - c[0]))
    t1 = (-(q1[1] - c[1]), q1[0] - c[0]) if ccw else (q1[1] - c[1], -(q1[0] - c[0]))
    den = cross(t0, t1)
    if den == 0.0:
        raise ValueError("arc piece of 0 or 180 degrees has no control vertex")
    t = cross(sub(q1, q0), t1) / den
    return (q0[0] + t0[0] * t, q0[1] + t0[1] * t)


def arc_to_vertices(p0, p1, center, ccw, max_piece_deg=90.0):
    """Vectorworks vertices for the arc p0 -> p1 (excluding p0, including p1
    as a corner vertex): one arc control vertex per piece of at most
    max_piece_deg, at the intersection of the tangents, so Vectorworks
    reproduces exactly this arc."""
    p0, p1, c = pt(p0), pt(p1), pt(center)
    r0, r1 = dist(p0, c), dist(p1, c)
    if abs(r0 - r1) > 1e-9 * max(1.0, r0):
        raise ValueError("start and end are not on one circle around the center "
                         "(radii %r vs %r)" % (r0, r1))
    r = (r0 + r1) / 2.0
    if p0 == p1:
        sw = TWO_PI if ccw else -TWO_PI
    else:
        sw = arc_sweep(("A", p0, p1, c, r, ccw))
    n = max(1, int(math.ceil(abs(math.degrees(sw)) / max_piece_deg - 1e-9)))
    a0 = math.atan2(p0[1] - c[1], p0[0] - c[0])
    qs = [p0]
    for k in range(1, n):
        a = a0 + sw * k / n
        qs.append((c[0] + r * math.cos(a), c[1] + r * math.sin(a)))
    qs.append(p1)
    out = []
    for k in range(n):
        ctl = _tangent_intersection(qs[k], qs[k + 1], c, ccw)
        out.append([ctl[0], ctl[1], ARC, r])
    out.append([p1[0], p1[1], CORNER, 0.0])
    return out


def segments_max_deviation(a, b):
    """Max distance between two segment lists that should describe the same
    geometry (pairwise; same count required)."""
    if len(a) != len(b):
        return float("inf")
    worst = 0.0
    for s, t in zip(a, b):
        if s[0] != t[0]:
            return float("inf")
        worst = max(worst, dist(s[1], t[1]), dist(s[2], t[2]))
        if s[0] == "A":
            if s[5] != t[5]:
                return float("inf")
            worst = max(worst, dist(s[3], t[3]), abs(s[4] - t[4]))
    return worst
