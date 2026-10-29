import pytest

from vwmcp.geom import specs as S

M = 25.4 / 0.0254  # metre document (as Vectorworks reports it)


def test_mm_input_into_metre_document_is_exact():
    n, _ = S.normalize({"type": "polygon", "unit": "mm", "class": "W",
                        "points": [[0, 0], [3650, 0], [3650, 240], [0, 240]]}, M)
    assert n["verts"][1][:2] == [3.65, 0.0] and n["verts"][2][:2] == [3.65, 0.24]


def test_relative_points_sum_exactly():
    n, _ = S.normalize({"type": "polygon", "relative": True, "class": "W",
                        "points": [[0.1, 0], [0.2, 0], [0, 0.3], [-0.2, 0]]}, M)
    assert [v[:2] for v in n["verts"]] == [[0.1, 0.0], [0.3, 0.0], [0.3, 0.3], [0.1, 0.3]]


def test_duplicate_points_are_rejected():
    with pytest.raises(S.SpecError, match="identical"):
        S.normalize({"type": "polygon", "points": [[0, 0], [1, 0], [1, 0], [0, 1]]}, M)


def test_path_with_arc_needs_both_ends_on_the_circle():
    with pytest.raises(S.SpecError, match="same circle"):
        S.normalize({"type": "path", "start": [3, 0],
                     "segments": [{"arc_to": [0, 3.1], "center": [0, 0]}]}, M)


def test_path_arc_becomes_control_vertex_and_expected_segments():
    n, exp = S.normalize({"type": "path", "start": [0, 0], "closed": True, "segments": [
        {"line_to": [3, 0]}, {"arc_to": [0, 3], "center": [0, 0], "direction": "ccw"}, {"line_to": [0, 0]}]}, M)
    assert n["verts"] == [[0.0, 0.0, 0, 0.0], [3.0, 0.0, 0, 0.0], [3.0, 3.0, 3, 3.0], [0.0, 3.0, 0, 0.0]]
    assert [s[0] for s in exp] == ["L", "A", "L"]


def test_arc_by_radius_picks_small_or_large_side():
    small, _ = S.normalize({"type": "path", "start": [0, 0], "closed": False,
                            "segments": [{"arc_to": [2, 0], "radius": 2, "direction": "ccw"}]}, M)
    large, _ = S.normalize({"type": "path", "start": [0, 0], "closed": False,
                            "segments": [{"arc_to": [2, 0], "radius": 2, "direction": "ccw", "large": True}]}, M)
    assert len(small["verts"]) == 3 and len(large["verts"]) > 3


def test_default_attributes_are_by_class():
    n, _ = S.normalize({"type": "rect", "p1": [0, 0], "p2": [1, 1], "class": "W"}, M)
    assert n["attributes"] == {"by_class": True}
    n, _ = S.normalize({"type": "rect", "p1": [0, 0], "p2": [1, 1], "attributes": {"fill": "none"}}, M)
    assert "by_class" not in n["attributes"]


def test_rotated_rect_becomes_exact_polygon():
    n, exp = S.normalize({"type": "rect", "corner": [0, 0], "width": 2, "height": 1, "angle": 90}, M)
    assert n["kind"] == "poly" and len(n["verts"]) == 4 and exp


def test_check_created_detects_deviation():
    n, exp = S.normalize({"type": "polygon", "points": [[0, 0], [1, 0], [1, 1]]}, M)
    rec = {"uuid": "u", "kind": "polygon", "closed": True, "vertices": [[0, 0, 0, 0], [1.001, 0, 0, 0], [1, 1, 0, 0]]}
    out = S.check_created(n, exp, rec, 1e-9)
    assert not out["exact"] and out["deviation"] == pytest.approx(0.001, rel=1e-3)
    rec["vertices"][1][0] = 1.0
    assert S.check_created(n, exp, rec, 1e-9)["exact"]


def test_bad_unit_is_rejected():
    with pytest.raises(S.SpecError):
        S.normalize({"type": "line", "p1": [0, 0], "p2": [1, 0], "unit": "inch"}, M)
