import ast
import math
import random

import pytest

from vwmcp import calls
from vwmcp.geom import curves as C


def test_vw_side_code_is_python39():
    ast.parse(calls.VWLIB2D_SOURCE, feature_version=(3, 9))
    src = open(C.__file__, encoding="utf-8").read()
    ast.parse(src, feature_version=(3, 9))


def _arcs(segs):
    return [s for s in segs if s[0] == "A"]


def test_fillet_with_radius():
    # live-verified: control vertex (4, 3), radius 1 -> arc (4, 2) -> (3, 3) around (3, 2)
    segs = C.polyline_segments([[0, 0, 0, 0], [4, 0, 0, 0], [4, 3, 3, 1.0], [0, 3, 0, 0]], True)
    (a,) = _arcs(segs)
    assert a[1] == (4.0, 2.0) and a[2] == pytest.approx((3.0, 3.0))
    assert a[3] == pytest.approx((3.0, 2.0)) and a[4] == pytest.approx(1.0) and a[5] is True


def test_fillet_radius_zero_and_too_big_use_the_largest_that_fits():
    for r in (0.0, 10.0):  # both live-verified to give radius 3 (shorter edge)
        segs = C.polyline_segments([[40, 0, 0, 0], [44, 0, 0, 0], [44, 3, 3, r], [40, 3, 0, 0]], True)
        (a,) = _arcs(segs)
        assert a[4] == pytest.approx(3.0)
        assert a[1] == pytest.approx((44.0, 0.0)) and a[2] == pytest.approx((41.0, 3.0))


def test_neighbouring_arc_vertices_share_the_edge_half_and_half():
    segs = C.polyline_segments([[60, 0, 0, 0], [64, 0, 3, 0.0], [64, 3, 3, 0.0], [60, 3, 0, 0]], True)
    a, b = _arcs(segs)
    assert a[4] == pytest.approx(1.5) and b[4] == pytest.approx(1.5)
    assert a[2] == pytest.approx((64.0, 1.5)) and b[1] == pytest.approx((64.0, 1.5))


def test_arc_at_open_end_is_ignored():
    segs = C.polyline_segments([[90, 0, 3, 1.0], [94, 0, 0, 0], [94, 3, 0, 0]], False)
    assert all(s[0] == "L" for s in segs)


def test_bezier_is_reported_unsupported():
    with pytest.raises(C.UnsupportedCurve):
        C.polyline_segments([[0, 0, 0, 0], [1, 1, 1, 0], [2, 0, 0, 0]], False)


def test_arc_to_vertices_axis_aligned_quarter_is_exact():
    v = C.arc_to_vertices((23, 0), (20, 3), (20, 0), True)
    assert v == [[23.0, 3.0, C.ARC, 3.0], [20.0, 3.0, C.CORNER, 0.0]]


def test_arc_to_vertices_round_trip():
    rnd = random.Random(1)
    worst = 0.0
    for _ in range(500):
        c = (rnd.uniform(-50, 50), rnd.uniform(-50, 50))
        r = rnd.uniform(0.05, 60)
        a0 = rnd.uniform(-math.pi, math.pi)
        sw = rnd.uniform(0.05, 2 * math.pi - 0.05) * rnd.choice([1, -1])
        p0 = (c[0] + r * math.cos(a0), c[1] + r * math.sin(a0))
        p1 = (c[0] + r * math.cos(a0 + sw), c[1] + r * math.sin(a0 + sw))
        verts = [[p0[0], p0[1], 0, 0.0]] + C.arc_to_vertices(p0, p1, c, sw > 0) + [[c[0], c[1], 0, 0.0]]
        arcs = _arcs(C.polyline_segments(verts, True))
        for a in arcs:
            worst = max(worst, abs(a[4] - r) / r, C.dist(a[3], c) / r)
        worst = max(worst, C.dist(arcs[0][1], p0) / r, C.dist(arcs[-1][2], p1) / r)
    assert worst < 1e-10


def test_discretize_uses_a_global_angle_grid():
    c, r = (0.0, 0.0), 10.0
    a = ("A", (10.0, 0.0), (0.0, 10.0), c, r, True)
    b = ("A", (math.cos(0.3) * r, math.sin(0.3) * r), (0.0, 10.0), c, r, True)
    pa, pb = set(C.discretize([a], 1e-3)[1:-1]), set(C.discretize([b], 1e-3)[1:-1])
    shared = [p for p in pb if math.atan2(p[1], p[0]) > 0.3 + 1e-9]
    assert shared and all(p in pa for p in shared)


def test_point_segment_distance_on_arcs_is_exact():
    a = ("A", (5.0, 0.0), (0.0, 5.0), (0.0, 0.0), 5.0, True)
    p = (5 * math.cos(1.0) * 1.0002, 5 * math.sin(1.0) * 1.0002)
    assert C.point_segment_distance(p, a) == pytest.approx(5 * 0.0002, rel=1e-9)
    assert C.point_segment_distance((-1.0, -1.0), a) == pytest.approx(min(math.hypot(6, 1), math.hypot(1, 6)))


def test_circle_center_3pt():
    assert C.circle_center_3pt((1, 0), (0, 1), (-1, 0)) == pytest.approx((0.0, 0.0))
