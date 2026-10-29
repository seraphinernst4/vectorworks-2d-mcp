import math

from vwmcp.geom import curves as C
from vwmcp.geom.checks import run_checks
from vwmcp.geom.fixes import segments_to_vertices

U = {"mm_per_unit": 25.4 / 0.0254, "name": "m"}  # metre document
BC = {"fill": True, "fill_color": True, "pen_color": True, "line_weight": True, "line_style": True}


def poly(u, pts, cls="Wand", layer="EG", closed=True, verts=None):
    v = verts or [[x, y, 0, 0.0] for x, y in pts]
    xs, ys = [p[0] for p in v], [p[1] for p in v]
    return {"uuid": u, "kind": "polygon" if not verts else "polyline", "class": cls, "layer": layer,
            "vertices": v, "closed": closed, "holes": [], "bbox": [min(xs), min(ys), max(xs), max(ys)],
            "attrs": {"fill": "solid", "by_class": dict(BC)}}


def kinds(res):
    return sorted(i["kind"] for i in res["_all_issues"])


def test_clean_neighbours_have_no_issues():
    recs = [poly("A", [(0, 0), (4, 0), (4, 3), (0, 3)]), poly("B", [(4, 0), (8, 0), (8, 3), (4, 3)]),
            poly("C", [(0, 3), (8, 3), (8, 4), (0, 4)])]
    res = run_checks(recs, U, 1.0, none_class="Keine")
    assert res["_all_issues"] == [] and res["summary"]["clean"]


def test_gap_of_point_three_mm_is_found_once_per_pair():
    recs = [poly("A", [(0, 0), (4, 0), (4, 3), (0, 3)]), poly("B", [(4.0003, 0), (8, 0), (8, 3), (4.0003, 3)])]
    res = run_checks(recs, U, 1.0)
    gaps = [i for i in res["_all_issues"] if i["kind"] == "gap"]
    assert len(gaps) == 1 and gaps[0]["distance_mm"] == __import__("pytest").approx(0.3)
    assert len(gaps[0]["moves"]) == 2


def test_overlap_and_containment():
    recs = [poly("A", [(0, 0), (4, 0), (4, 3), (0, 3)]), poly("B", [(3.99, 0), (8, 0), (8, 3), (3.99, 3)]),
            poly("C", [(1, 1), (2, 1), (2, 2), (1, 2)])]
    res = run_checks(recs, U, 1.0)
    ov = [i for i in res["_all_issues"] if i["kind"] == "overlap"]
    assert len(ov) == 2
    by = {tuple(sorted(i["uuids"])): i for i in ov}
    assert by[("A", "B")]["thickness_mm"] == __import__("pytest").approx(10.0, rel=1e-6)
    assert "komplett" in by[("A", "C")]["message"]


def test_duplicate_area_and_double_line():
    recs = [poly("A", [(0, 0), (4, 0), (4, 3), (0, 3)]), poly("A2", [(0, 0), (4, 0), (4, 3), (0, 3)]),
            {"uuid": "L", "kind": "line", "class": "Linie", "layer": "EG", "p1": [0, 0], "p2": [4, 0],
             "bbox": [0, 0, 4, 0], "attrs": {"by_class": dict(BC)}}]
    res = run_checks(recs, U, 1.0)
    ks = kinds(res)
    assert "duplicate" in ks and "double_line" in ks


def test_other_layers_are_not_compared_by_default():
    recs = [poly("A", [(0, 0), (4, 0), (4, 3), (0, 3)], layer="EG"),
            poly("B", [(1, 1), (5, 1), (5, 4), (1, 4)], layer="OG")]
    assert run_checks(recs, U, 1.0)["_all_issues"] == []
    assert "overlap" in kinds(run_checks(recs, U, 1.0, compare="all"))


def test_ignored_classes_are_left_out():
    recs = [poly("A", [(0, 0), (4, 0), (4, 3), (0, 3)]),
            poly("M", [(1, 1), (2, 1), (2, 2), (1, 2)], cls="Ausstattung-Möbel")]
    assert run_checks(recs, U, 1.0, ignore_classes=["Ausstattung*"])["_all_issues"] == []


def test_almost_closed_outline():
    recs = [poly("A", [(0, 0), (4, 0), (4, 3), (0, 3), (0.0004, 0)], closed=False)]
    res = run_checks(recs, U, 1.0)
    assert any(i["kind"] == "open" and i["severity"] == "error" for i in res["_all_issues"])


def test_no_class_and_attributes_not_by_class():
    r = poly("A", [(0, 0), (1, 0), (1, 1)], cls="Keine")
    r["attrs"]["by_class"]["pen_color"] = False
    res = run_checks([r], U, 1.0, none_class="Keine")
    assert {"classes", "attributes"} <= set(kinds(res))


def _sector(u, c, r0, r1, a0, a1):
    def P(r, a):
        return (c[0] + r * math.cos(math.radians(a)), c[1] + r * math.sin(math.radians(a)))
    segs = [("L", P(r0, a0), P(r1, a0)), ("A", P(r1, a0), P(r1, a1), c, r1, True),
            ("L", P(r1, a1), P(r0, a1)), ("A", P(r0, a1), P(r0, a0), c, r0, False)]
    return poly(u, None, verts=segments_to_vertices(segs, True))


def test_cavea_rings_sharing_true_arcs_are_clean():
    c = (50.0, 0.0)
    recs = [_sector("S1", c, 20, 23, 10, 80), _sector("S2", c, 20, 23, 80, 170),
            _sector("R1", c, 23, 26.5, 10, 80), _sector("R2", c, 23, 26.5, 80, 170)]
    res = run_checks(recs, U, 1.0)
    assert [i for i in res["_all_issues"] if i["severity"] == "error"] == []


def test_ring_with_wrong_radius_is_a_gap():
    c = (50.0, 0.0)
    recs = [_sector("S1", c, 20, 23, 10, 80), _sector("R1", c, 23.0004, 26.5, 10, 80)]
    res = run_checks(recs, U, 1.0)
    assert any(i["kind"] == "gap" for i in res["_all_issues"])
