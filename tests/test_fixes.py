from vwmcp.geom import fixes as F
from vwmcp.geom.checks import run_checks
from vwmcp.geom.shapes import units_tolerances

from tests.test_checks import U, _sector, poly


def _plan(recs, **kw):
    res = run_checks(recs, U, 1.0)
    return res, F.plan_snap(res["_all_issues"], res["_shapes"], units_tolerances(U, 1.0), **kw)


def test_snap_closes_a_gap_exactly():
    recs = [poly("A", [(0, 0), (4, 0), (4, 3), (0, 3)]), poly("B", [(4.0003, 0), (8, 0), (8, 3), (4.0003, 3)])]
    res, plan = _plan(recs)
    assert plan["moved"] == 2 and not plan["skipped"]
    (ch,) = plan["polys"]
    moved = {tuple(v[:2]) for v in ch["verts"]}
    assert (4.0, 0.0) in moved and (4.0, 3.0) in moved
    # after applying, the drawing is clean
    for r in recs:
        if r["uuid"] == ch["uuid"]:
            r["vertices"] = ch["verts"]
    assert [i for i in run_checks(recs, U, 1.0)["_all_issues"] if i["severity"] == "error"] == []


def test_fixed_objects_do_not_move():
    recs = [poly("A", [(0, 0), (4, 0), (4, 3), (0, 3)]), poly("B", [(4.0003, 0), (8, 0), (8, 3), (4.0003, 3)])]
    _, plan = _plan(recs, fixed=["B"])
    (ch,) = plan["polys"]
    assert ch["uuid"] == "A"
    assert {tuple(v[:2]) for v in ch["verts"]} >= {(4.0003, 0.0), (4.0003, 3.0)}


def test_t_junction_snaps_onto_edge_and_shares_the_node():
    recs = [poly("A", [(0, 0), (8, 0), (8, 3), (0, 3)]), poly("B", [(2, 3.0004), (3, 3.0004), (3, 5), (2, 5)])]
    res, plan = _plan(recs)
    changed = {c["uuid"]: c for c in plan["polys"]}
    assert {tuple(v[:2]) for v in changed["B"]["verts"]} >= {(2.0, 3.0), (3.0, 3.0)}
    assert plan["inserted"] == 2 and len(changed["A"]["verts"]) == 6


def test_snap_moves_an_arc_end_along_its_circle():
    c = (50.0, 0.0)
    a = _sector("S1", c, 20, 23, 10, 80)
    b = _sector("S2", c, 20, 23, 80.000001, 170)  # 0.4 mm gap along the arcs
    res, plan = _plan([a, b])
    assert plan["moved"] >= 2
    recs = [a, b]
    for ch in plan["polys"]:
        for r in recs:
            if r["uuid"] == ch["uuid"]:
                r["vertices"] = ch["verts"]
    assert [i for i in run_checks(recs, U, 1.0)["_all_issues"] if i["severity"] == "error"] == []


def test_overlaps_top_object_wins_by_default_and_priority_overrides():
    recs = [poly("A", [(0, 0), (4, 0), (4, 3), (0, 3)]), poly("B", [(3.9, 0), (8, 0), (8, 3), (3.9, 3)])]
    res = run_checks(recs, U, 1.0)
    plan = F.plan_overlaps(res["_all_issues"], res["_shapes"])
    assert plan["subtract"] == [{"target": "A", "tools": ["B"]}]
    plan = F.plan_overlaps(res["_all_issues"], res["_shapes"], priority=["A"])
    assert plan["subtract"] == [{"target": "B", "tools": ["A"]}]


def test_clean_removes_duplicate_and_collinear_vertices():
    recs = [poly("A", [(0, 0), (2, 0), (2, 0), (4, 0), (4, 3), (0, 3)])]
    res = run_checks(recs, U, 1.0)
    plan = F.plan_clean(res["_shapes"], units_tolerances(U, 1.0))
    (ch,) = plan["polys"]
    assert [v[:2] for v in ch["verts"]] == [[0, 0], [4, 0], [4, 3], [0, 3]]


def test_duplicates_keep_the_top_copy():
    recs = [poly("A", [(0, 0), (4, 0), (4, 3), (0, 3)]), poly("A2", [(0, 0), (4, 0), (4, 3), (0, 3)])]
    res = run_checks(recs, U, 1.0)
    assert F.plan_duplicates(res["_all_issues"], res["_shapes"]) == {"delete": ["A"]}
