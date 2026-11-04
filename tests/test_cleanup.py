"""0.7: profile, DXF scan, join, simplify, stock-taking, import/join/simplify/group tools."""

import copy
import json
import math
import uuid as _uuid

import pytest
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError

from vwmcp import app, dxfscan, organize, profile
from vwmcp.geom import render as R
from vwmcp.geom.inventory import take_stock
from vwmcp.geom.join import plan_join
from vwmcp.geom.simplify import _seg_dist, plan_simplify
from vwmcp.journal import Journal
from vwmcp.tools import cleanup, draw2d, generic

UNITS = {"name": "m", "area_name": "m²", "upi": 0.0254, "mm_per_unit": 1000.0}
BC = {"fill": True, "fill_color": True, "pen_color": True, "line_weight": True, "line_style": True}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VWMCP_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


# ---- profile ----------------------------------------------------------------------------------

def test_profile_defaults_and_roundtrip(home):
    p = profile.load()
    assert p == profile.defaults()
    assert p["detail"] == "keep" and p["check_mode"] == "auto" and p["rules"] == []
    p2 = profile.save({"detail": "simplify_ok", "confirm_above": 50})
    assert p2["detail"] == "simplify_ok" and p2["confirm_above"] == 50
    stored = json.loads((home / "profile.json").read_text())
    assert stored == {"detail": "simplify_ok", "confirm_above": 50}  # only what differs from the defaults
    assert profile.load()["detail"] == "simplify_ok"
    profile.reset(["detail"])
    assert profile.load()["detail"] == "keep" and profile.load()["confirm_above"] == 50
    profile.reset()
    assert profile.load() == profile.defaults()


def test_profile_validation():
    with pytest.raises(profile.ProfileError, match="one of"):
        profile.save({"detail": "maybe"})
    with pytest.raises(profile.ProfileError, match="unknown setting"):
        profile.save({"colour": "red"})
    with pytest.raises(profile.ProfileError, match="between"):
        profile.save({"tolerance_mm": 0})
    with pytest.raises(profile.ProfileError, match="number"):
        profile.save({"confirm_above": "many"})
    with pytest.raises(profile.ProfileError, match="list of texts"):
        profile.save({"rules": [3]})


def test_profile_rules_and_instructions():
    profile.add_rule("Ruinen organisch lassen, nur vereinfachen")
    profile.add_rule("Schnittlinien 0,5 mm")
    profile.add_rule("Schnittlinien 0,5 mm")  # no duplicates
    p = profile.load()
    assert p["rules"] == ["Ruinen organisch lassen, nur vereinfachen", "Schnittlinien 0,5 mm"]
    block = profile.instructions_block(p)
    assert "1. Ruinen organisch lassen" in block and "2. Schnittlinien 0,5 mm" in block
    assert "keep every edge" in block
    profile.remove_rule(1)
    assert profile.load()["rules"] == ["Schnittlinien 0,5 mm"]
    with pytest.raises(profile.ProfileError, match="no rule 5"):
        profile.remove_rule(5)


def test_profile_changes_the_instructions():
    text = app.build_instructions(profile.defaults())
    assert "THE USER DECIDES" in text and "always wins" in text
    assert "keep every edge" in text and "vw2d_fix until no errors" in text
    simp = app.build_instructions(dict(profile.defaults(), detail="simplify_ok", check_mode="report",
                                       confirm_above=0, rules=["Vorhänge als Fläche"]))
    assert "keep every edge" not in simp and "You may simplify" in simp
    assert "Check and report; fix only what the user asks for." in simp
    assert "need no extra confirmation" in simp and "Vorhänge als Fläche" in simp
    # the static parts stay
    assert "IMPORTED AND GENERATED DRAWINGS" in simp and "KEYSTONE UPDATE REQUIRED" in simp


def test_broken_profile_file_falls_back(home):
    home.mkdir(parents=True, exist_ok=True)
    (home / "profile.json").write_text("{not json")
    assert profile.load() == profile.defaults()
    (home / "profile.json").write_text(json.dumps({"detail": "nonsense", "tolerance_mm": 2}))
    p = profile.load()
    assert p["detail"] == "keep" and p["tolerance_mm"] == 2  # invalid values ignored, valid ones kept


def test_profile_tool(home):
    out = cleanup.vw2d_profile("show")
    keys = [s["key"] for s in out["settings"]]
    assert keys == profile.ORDER
    out = cleanup.vw2d_profile("set", values={"merge_parts": "always"})
    row = next(s for s in out["settings"] if s["key"] == "merge_parts")
    assert row["value"] == "always" and row["changed"] and "ONE closed area" in out["rules_now"]
    out = cleanup.vw2d_profile("add_rule", rule="Flächen grau")
    assert "Flächen grau" in out["rules_now"]
    with pytest.raises(ToolError, match="one of"):
        cleanup.vw2d_profile("set", values={"areas": "x"})
    with pytest.raises(ToolError, match="give values"):
        cleanup.vw2d_profile("set")
    out = cleanup.vw2d_profile("reset")
    assert all(not s["changed"] for s in out["settings"])


# ---- DXF scan ---------------------------------------------------------------------------------

def _dxf(version, entities):
    lines = ["0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", version, "9", "$INSUNITS", "70", "6",
             "0", "ENDSEC", "0", "SECTION", "2", "BLOCKS", "0", "BLOCK", "2", "Stuhl", "0", "LINE", "8", "0",
             "0", "ENDBLK", "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]
    for e in entities:
        lines += e
    lines += ["0", "ENDSEC", "0", "EOF"]
    return "\n".join(lines) + "\n"


LINE = ["0", "LINE", "8", "A", "10", "0", "20", "0", "11", "1", "21", "0"]
SPLINE = ["0", "SPLINE", "8", "A", "70", "8", "71", "3"]
RATIONAL = ["0", "SPLINE", "8", "A", "70", "12", "71", "2"]
POLY = ["0", "POLYLINE", "8", "A", "66", "1", "0", "VERTEX", "10", "0", "20", "0", "0", "VERTEX", "10", "1",
        "20", "0", "0", "SEQEND"]


def test_dxfscan_counts_entities_and_rational_splines(tmp_path):
    f = tmp_path / "a.dxf"
    f.write_text(_dxf("AC1032", [LINE, LINE, SPLINE, RATIONAL, RATIONAL, POLY, ["0", "INSERT", "2", "Stuhl"]]))
    s = dxfscan.scan(f)
    assert s["countable"] and s["version"] == "2018" and s["insunits"] == 6
    assert s["entities"] == {"LINE": 2, "SPLINE": 3, "POLYLINE": 1, "INSERT": 1}  # VERTEX/SEQEND are parts
    assert s["total"] == 7 and s["splines"] == 3 and s["rational_splines"] == 2
    assert s["block_definitions"] == 1  # the LINE inside the block is not counted as an entity
    assert dxfscan.expected_losses(s) == {"objects": 2, "why": dxfscan.expected_losses(s)["why"]}


def test_dxfscan_r12_and_dwg(tmp_path):
    f = tmp_path / "r12.dxf"
    f.write_text(_dxf("AC1009", [LINE, SPLINE, RATIONAL]))
    s = dxfscan.scan(f)
    assert s["version"] == "R12" and dxfscan.expected_losses(s)["objects"] is None
    st = take_stock([_line((0, 0), (1, 0))], 1000.0, 1e-6, source=s, arrived_by_layer={"L": 1})
    assert any("Rhino lässt sie beim R12-Export weg" in t for t in st["next_steps"])
    d = tmp_path / "x.dwg"
    d.write_bytes(b"AC1027" + b"\0" * 40)
    assert dxfscan.scan(d) == {"format": "DWG", "version": "2013", "countable": False}
    assert dxfscan.expected_losses(dxfscan.scan(d)) == {}


# ---- join -------------------------------------------------------------------------------------

def _line(p1, p2, cls="A", layer="L", pen="#000000", **kw):
    r = {"uuid": str(_uuid.uuid4()), "kind": "line", "p1": list(p1), "p2": list(p2), "class": cls, "layer": layer,
         "attrs": {"pen": pen, "line_weight_mm": 0.18, "line_style": "solid", "by_class": dict(BC)}}
    r.update(kw)
    xs, ys = [p1[0], p2[0]], [p1[1], p2[1]]
    r["bbox"] = [min(xs), min(ys), max(xs), max(ys)]
    return r


def _poly(pts, closed=False, cls="A", layer="L", types=None):
    vs_ = [[x, y, (types[i] if types else 0), 0.0] for i, (x, y) in enumerate(pts)]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return {"uuid": str(_uuid.uuid4()), "kind": "polyline", "vertices": vs_, "closed": closed, "holes": [],
            "class": cls, "layer": layer, "bbox": [min(xs), min(ys), max(xs), max(ys)],
            "attrs": {"pen": "#000000", "line_weight_mm": 0.18, "line_style": "solid", "by_class": dict(BC)}}


def test_join_chains_reversed_pieces_exactly():
    a = _line((0, 0), (1, 0))
    b = _line((2, 0), (1, 0))  # reversed
    c = _line((2, 0), (2, 1))
    plan = plan_join([b, c, a], 1e-6)
    assert plan["chains"] == 1 and plan["pieces_joined"] == 3 and len(plan["drop"]) == 2
    ch = plan["changes"][0]
    pts = [tuple(v[:2]) for v in ch["verts"]]
    assert pts in ([(0, 0), (1, 0), (2, 0), (2, 1)], [(2, 1), (2, 0), (1, 0), (0, 0)])
    assert ch["closed"] is False and "attrs" not in ch


def test_join_closed_loop_has_no_fill():
    pts = [(0, 0), (1, 0), (1, 1), (0, 1)]
    pieces = [_line(pts[i], pts[(i + 1) % 4]) for i in range(4)]
    plan = plan_join(pieces, 1e-6)
    ch = plan["changes"][0]
    assert plan["chains"] == 1 and ch["closed"] is True and len(ch["verts"]) == 4
    assert ch["attrs"] == {"fill": "none"}


def test_join_self_crossing_loop_stays_open():
    """A figure eight closed into a polygon would fill oddly (live: 6
    self-intersection errors after joining a section)."""
    pts = [(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)]
    plan = plan_join([_line(pts[i], pts[i + 1]) for i in range(4)], 1e-6)
    assert plan["chains"] == 1 and plan["changes"][0]["closed"] is False
    assert len(plan["changes"][0]["verts"]) == 5 and "attrs" not in plan["changes"][0]


def test_join_tolerance_and_exact_shared_point():
    a = _line((0, 0), (1, 0))
    b = _line((1.0004, 0), (2, 0))  # 0.4 mm gap in a metre document
    assert plan_join([a, b], 0.0001)["chains"] == 0
    plan = plan_join([a, b], 0.0005)
    v = plan["changes"][0]["verts"]
    assert len(v) == 3  # the shared end point appears once
    assert any(abs(p[0] - 1.0) < 1e-12 or abs(p[0] - 1.0004) < 1e-12 for p in v)


def test_join_stops_at_junctions_unless_asked():
    a = _line((-1, 0), (0, 0))
    b = _line((0, 0), (1, 0))
    c = _line((0, 0), (0, 1))
    assert plan_join([a, b, c], 1e-6)["chains"] == 0
    plan = plan_join([a, b, c], 1e-6, through_junctions=True)
    assert plan["chains"] == 1
    pts = sorted(tuple(v[:2]) for v in plan["changes"][0]["verts"])
    assert pts == [(-1, 0), (0, 0), (1, 0)]  # the straight way on, not the turn


def test_join_respects_class_layer_look_and_groups():
    a = _line((0, 0), (1, 0))
    assert plan_join([a, _line((1, 0), (2, 0), cls="B")], 1e-6)["chains"] == 0
    assert plan_join([a, _line((1, 0), (2, 0), layer="M")], 1e-6)["chains"] == 0
    assert plan_join([a, _line((1, 0), (2, 0), pen="#ff0000")], 1e-6)["chains"] == 0
    assert plan_join([a, _line((1, 0), (2, 0), parent="g1")], 1e-6)["chains"] == 0
    assert plan_join([a, _line((1, 0), (2, 0))], 1e-6)["chains"] == 1


def test_join_polylines_and_curves():
    p = _poly([(1, 0), (1.5, 0.5), (2, 0)])
    a = _line((0, 0), (1, 0))
    plan = plan_join([a, p], 1e-6)
    assert plan["chains"] == 1 and len(plan["changes"][0]["verts"]) == 4
    closed = _poly([(5, 5), (6, 5), (6, 6)], closed=True)
    arc_end = _poly([(2, 0), (3, 0), (4, 0)], types=[0, 0, 3])
    plan = plan_join([closed, arc_end, _line((4, 0), (5, 0))], 1e-6)
    assert plan["chains"] == 0 and plan["skipped"]["closed or not joinable"] == 2
    curve = _poly([(1, 0), (1.5, 1), (2, 0)], types=[1, 1, 1])
    curve["approx_polygon"] = [[1, 0], [1.25, 0.4], [1.5, 0.5], [1.75, 0.4], [2, 0]]
    assert plan_join([a, curve], 1e-6)["chains"] == 1
    assert plan_join([a, curve], 1e-6, include_curves=False)["chains"] == 0


def test_join_many_fragments_fast():
    # 2000 collinear fragments of one long line join into one polyline
    pieces = [_line((i * 0.01, 0), ((i + 1) * 0.01, 0)) for i in range(2000)]
    plan = plan_join(pieces, 1e-6)
    assert plan["chains"] == 1 and plan["pieces_joined"] == 2000 and plan["objects_after"] == 1


# ---- simplify ---------------------------------------------------------------------------------

def _wavy(n=400, amp=0.002, closed=False):
    pts = [(i / n * 4.0, amp * math.sin(i * 7.3) + 0.3 * math.sin(i / n * math.pi)) for i in range(n + 1)]
    return _poly(pts, closed=closed)


def test_simplify_keeps_within_tolerance():
    rec = _wavy()
    plan = plan_simplify([rec], 0.01)
    ch = plan["changes"][0]
    assert ch["after"] < ch["before"] / 4
    kept = [(v[0], v[1]) for v in ch["verts"]]
    orig = [(v[0], v[1]) for v in rec["vertices"]]
    assert kept[0] == orig[0] and kept[-1] == orig[-1] and set(kept) <= set(orig)  # a subset, nothing moved
    worst = max(min(_seg_dist(p, kept[i], kept[i + 1]) for i in range(len(kept) - 1)) for p in orig)
    assert worst <= 0.01 + 1e-12


def test_simplify_closed_stays_valid_and_skips_arcs():
    sq = []
    for k in range(4):
        x0, y0 = [(0, 0), (1, 0), (1, 1), (0, 1)][k]
        x1, y1 = [(1, 0), (1, 1), (0, 1), (0, 0)][k]
        sq += [(x0 + (x1 - x0) * t / 10, y0 + (y1 - y0) * t / 10) for t in range(10)]
    rec = _poly(sq, closed=True)
    plan = plan_simplify([rec], 0.001)
    assert len(plan["changes"][0]["verts"]) == 4  # the four corners
    arcs = _poly([(0, 0), (1, 0), (2, 1), (3, 0), (4, 0), (5, 0), (6, 0), (7, 0)], types=[0, 0, 3, 0, 0, 0, 0, 0])
    plan = plan_simplify([arcs], 0.5)
    assert plan["changes"] == [] and plan["skipped"] == {"true arcs": 1}


def test_simplify_curve_vertices_use_polygonization_only_when_it_pays():
    curve = _poly([(0, 0), (1, 1), (2, 0)], types=[1, 1, 1])
    curve["approx_polygon"] = [[i / 100, math.sin(i / 100 * math.pi)] for i in range(201)]
    assert plan_simplify([curve], 0.01, min_vertices=2)["changes"] == []  # 3 curve points beat any polyline
    many = _poly([(i / 50, 0) for i in range(60)], types=[1] * 60)
    many["approx_polygon"] = [[i / 100, 0.0] for i in range(120)]
    plan = plan_simplify([many], 0.001)
    assert plan["changes"][0]["after"] == 2


# ---- stock-taking -----------------------------------------------------------------------------

def test_take_stock_reports_losses_fragments_and_tips():
    recs = [_line((i, 0), (i + 1, 0), cls="Make2D$Visible$Curves") for i in range(10)]
    recs += [_line((0, 5), (0.0004, 5), cls="Make2D$Visible$Curves")]
    recs[-1]["attrs"]["by_class"] = dict(BC, pen_color=False)
    src = {"countable": True, "total": 20, "rational_splines": 9, "splines": 9, "version": "2018"}
    st = take_stock(recs, 1000.0, 1e-6, source=src, arrived_by_layer={"L": 11})
    assert st["objects"] == 11 and st["lines"]["shorter_than_1mm"] == 1
    assert st["joinable"] == {"pieces": 10, "into_polylines": 1, "objects_after": 2}
    assert st["source"]["missing"] == 9 and st["looks_generated"]
    tips = " ".join(st["next_steps"])
    assert "9 gewichtete Splines" in tips and "vw2d_join" in tips and "preset" in tips and "nach Klasse" in tips
    st = take_stock(recs, 1000.0, 1e-6, source={"format": "DWG", "countable": False})
    assert "DWG lässt sich nicht nachzählen" in " ".join(st["next_steps"])


# ---- organize: area match keys ----------------------------------------------------------------

def test_organize_bbox_and_touches():
    inside = _line((1, 1), (2, 2))
    across = _line((2.5, 2.5), (4, 4))
    assert organize.matches(inside, {"bbox": [0, 0, 3, 3]}, 1000)
    assert not organize.matches(across, {"bbox": [0, 0, 3, 3]}, 1000)
    assert organize.matches(across, {"touches": [0, 0, 3, 3]}, 1000)
    assert not organize.matches(_line((5, 5), (6, 6)), {"touches": [0, 0, 3, 3]}, 1000)
    with pytest.raises(organize.RuleError, match="x1, y1, x2, y2"):
        organize.validate([{"match": {"bbox": [0, 0, 1]}, "class": "X"}])


# ---- render cap -------------------------------------------------------------------------------

def test_render_shows_at_most_60_marks(monkeypatch):
    calls = []
    real_plot = R.plt.Axes.plot

    def spy(self, *a, **kw):
        if kw.get("ms") == 14:
            calls.append(1)
        return real_plot(self, *a, **kw)

    monkeypatch.setattr(R.plt.Axes, "plot", spy)
    recs = [_line((i, 0), (i + 1, 0)) for i in range(5)]
    from vwmcp.geom.shapes import from_records
    shapes = from_records(recs, 0.001)
    issues = [{"id": i, "kind": "gap", "severity": "error", "at": [i * 0.01, 0]} for i in range(300)]
    png = R.render(recs, shapes, issues, 0.001)
    assert png[:4] == b"\x89PNG" and len(calls) == 60
    calls.clear()
    R.render(recs, shapes, [], 0.001)
    assert calls == []


# ---- tools against a fake Vectorworks ---------------------------------------------------------

class FakeVW:
    def __init__(self, objs=()):
        self.objs = {o["uuid"]: copy.deepcopy(o) for o in objs}
        self.calls = []
        self.layers = {"Konstruktionsebene-1": 100.0}

    def __call__(self, fn, timeout=None, **kw):
        self.calls.append((fn, kw))
        if fn == "units":
            return UNITS
        if fn == "current_document":
            return "Test.vwx"
        if fn == "structure":
            return {"layers": [{"name": n, "scale": s} for n, s in self.layers.items()]}
        if fn == "read_objects":
            objs = list(self.objs.values())
            if kw.get("uuids"):
                objs = [self.objs[u] for u in kw["uuids"] if u in self.objs]
            elif kw.get("layer") not in (None, "*"):
                objs = [o for o in objs if o["layer"] == kw["layer"]]
            return {"document": "Test.vwx", "units": UNITS, "none_class": "Keine", "active_layer": "L",
                    "objects": copy.deepcopy(objs), "truncated": False}
        if fn == "join_pieces":
            out, snaps, after = [], [], []
            for ch in kw["changes"]:
                old = self.objs.pop(ch["uuid"])
                snaps.append({"rec": old, "z": 0})
                new = dict(old, uuid=str(_uuid.uuid4()), kind="polyline", vertices=ch["verts"], closed=ch["closed"])
                new.pop("p1", None)
                new.pop("p2", None)
                self.objs[new["uuid"]] = new
                after.append(new["uuid"])
                out.append(copy.deepcopy(new))
            dsn = [{"rec": self.objs.pop(u), "z": 0} for u in kw["drop"]]
            return {"document": "Test.vwx", "objects": out, "deleted": len(kw["drop"]),
                    "ops": [{"op": "replaced", "before": snaps, "after": after},
                            {"op": "replaced", "before": dsn, "after": []}]}
        if fn == "replace_geometry":
            out, snaps, after = [], [], []
            for ch in kw["changes"]:
                o = self.objs[ch["uuid"]]
                snaps.append({"rec": copy.deepcopy(o), "z": 0})
                o["vertices"], o["closed"] = ch["verts"], ch["closed"]
                after.append(o["uuid"])
                out.append(copy.deepcopy(o))
            return {"document": "Test.vwx", "objects": out, "ops": [{"op": "replaced", "before": snaps, "after": after}]}
        if fn == "group_objects":
            groups = []
            for g in kw["groups"]:
                u = str(_uuid.uuid4())
                groups.append({"uuid": u, "kind": "group", "name": g.get("name"), "layer": "L", "class": "Keine"})
            return {"document": "Test.vwx", "groups": groups, "ops": [{"op": "grouped", "uuid": g["uuid"]}
                                                                       for g in groups]}
        if fn == "import_dwg":
            recs = kw.pop("_recs", None)
            new = [_line((i, 0), (i + 1, 0), cls="Make2D$Visible$Curves", layer="schnitt") for i in range(5)]
            for r in new:
                self.objs[r["uuid"]] = r
            _ = recs
            return {"document": "Test.vwx", "result_code": 0, "seconds": 0.1, "new_layers": ["schnitt"],
                    "grown_layers": {}, "ops": [{"op": "layers_created", "layers": ["schnitt"]}]}
        if fn == "revert2d":
            return {"document": "Test.vwx", "deleted": 0, "restored": 0, "missing": [],
                    "mapping": {str(i): str(i + 1) for i in range(50)}}
        raise AssertionError(fn)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    def install(objs=()):
        f = FakeVW(objs)
        monkeypatch.setattr(app, "vw2d", f)
        monkeypatch.setattr(app, "vw", f)
        monkeypatch.setattr(generic, "vw", f)
        j = Journal(tmp_path / "j.jsonl")
        monkeypatch.setattr(app, "get_journal", lambda: j)
        f.journal = j
        return f
    return install


def _text(res):
    return json.loads(res[0] if isinstance(res, list) else res)


def test_join_tool_dry_run_then_apply(fake):
    f = fake([_line((i, 0), (i + 1, 0)) for i in range(6)])
    res = cleanup.vw2d_join(layer="L", dry_run=True)
    out = _text(res)
    assert out["dry_run"] and out["polylines"] == 1 and out["objects_after"] == 1
    assert isinstance(res[1], Image)
    assert not any(c[0] == "join_pieces" for c in f.calls)
    out = _text(cleanup.vw2d_join(layer="L", dry_run=False, image=False))
    assert out["applied"] and len(f.objs) == 1
    entry = f.journal.pop("Test.vwx")
    assert entry["tool"] == "vw2d_join" and len(entry["ops"]) == 2


def test_join_tool_profile_decides_dry_run(fake):
    fake([_line((i, 0), (i + 1, 0)) for i in range(6)])
    profile.save({"confirm_above": 3})
    assert _text(cleanup.vw2d_join(layer="L", image=False)).get("dry_run")
    profile.save({"confirm_above": 0})
    assert _text(cleanup.vw2d_join(layer="L", image=False)).get("applied")


def test_simplify_tool_uses_layer_scale_and_applies(fake):
    rec = _wavy()
    rec["layer"] = "Konstruktionsebene-1"
    f = fake([rec])
    out = _text(cleanup.vw2d_simplify(layer="Konstruktionsebene-1", image=False))
    assert out["dry_run"] and out["tolerance_mm"] == pytest.approx(10.0)  # 0.1 mm on paper at 1:100
    assert out["vertices_after"] < out["vertices_before"]
    out = _text(cleanup.vw2d_simplify(layer="Konstruktionsebene-1", dry_run=False, tolerance_mm=5, image=False))
    assert out["applied"] and len(f.objs[rec["uuid"]]["vertices"]) == out["vertices_after"]


def test_group_tool(fake):
    a, b, c = _line((0, 0), (1, 0)), _line((0, 1), (1, 1), cls="B"), _line((5, 5), (6, 6))
    f = fake([a, b, c])
    out = cleanup.vw2d_group(layer="L", bbox=[-1, -1, 2, 2], name="Vorhang")
    sent = [kw for fn, kw in f.calls if fn == "group_objects"][0]["groups"]
    assert len(sent) == 1 and sorted(sent[0]["uuids"]) == sorted([a["uuid"], b["uuid"]])
    assert sent[0]["name"] == "Vorhang" and out["objects"] == 2
    assert sent[0]["class"] is None  # the bridge picks the members' class
    cleanup.vw2d_group(layer="L", bbox=[-1, -1, 2, 2], group_class="Vorhang")
    assert [kw for fn, kw in f.calls if fn == "group_objects"][-1]["groups"][0]["class"] == "Vorhang"
    d = _line((0, 0.5), (1, 0.5))  # second object of class A
    f = fake([a, b, c, d])
    cleanup.vw2d_group(layer="L", bbox=[-1, -1, 2, 2], per_class=True, name="Vorhang")
    sent = [kw for fn, kw in f.calls if fn == "group_objects"][0]["groups"]
    assert len(sent) == 1 and sorted(sent[0]["uuids"]) == sorted([a["uuid"], d["uuid"]])  # B alone: no group
    assert sent[0]["name"] == "Vorhang A"
    fake([a, b, c])
    with pytest.raises(ToolError, match="only one object"):
        cleanup.vw2d_group(layer="L", bbox=[-1, -1, 2, 2], per_class=True)
    with pytest.raises(ToolError, match="at least two"):
        cleanup.vw2d_group(layer="L", bbox=[4, 4, 7, 7])


def test_import_tool(fake, tmp_path):
    f = fake()
    dxf = tmp_path / "schnitt.dxf"
    dxf.write_text(_dxf("AC1032", [LINE] * 5 + [RATIONAL] * 3))
    out = cleanup.vw2d_import(path=str(dxf))
    assert out["imported"]["new_layers"] == ["schnitt"] and out["layers"] == ["schnitt"]
    assert out["source"]["entities"] == 8 and out["source"]["missing"] == 3
    assert any("gewichtete Splines" in t for t in out["next_steps"])
    assert f.journal.pop("Test.vwx")["ops"] == [{"op": "layers_created", "layers": ["schnitt"]}]
    out = cleanup.vw2d_import(layer="schnitt")
    assert out["objects"] == 5 and "imported" not in out
    with pytest.raises(ToolError, match="not found"):
        cleanup.vw2d_import(path=str(tmp_path / "nope.dwg"))
    with pytest.raises(ToolError, match="give the path"):
        cleanup.vw2d_import()
    rh = tmp_path / "modell.3dm"
    rh.write_bytes(b"3D Geometry File Format")
    with pytest.raises(ToolError, match="DWG 2018"):
        cleanup.vw2d_import(path=str(rh))


def test_check_preset_drawing(fake):
    pieces = [_line((i, 0), (i + 0.999, 0)) for i in range(5)]  # 1 mm gaps between fragments
    fake(pieces)
    full = json.loads(draw2d.vw2d_check(layer="L", image=False))
    drawing = json.loads(draw2d.vw2d_check(layer="L", image=False, preset="drawing"))
    assert full["summary"]["errors"] > 0 or full["summary"]["warnings"] > 0
    assert drawing["summary"]["errors"] == 0
    with pytest.raises(ToolError, match="preset"):
        draw2d.vw2d_check(layer="L", image=False, preset="nope")


def test_check_preset_drawing_nested_outlines_are_no_overlap(fake):
    """A window outline inside a facade outline is a drawing; two stacked
    fills of one class are an error (live 0.7 beta: joined outlines of a
    section counted as 8 overlap errors)."""
    outer = _poly([(0, 0), (10, 0), (10, 10), (0, 10)], closed=True)
    inner = _poly([(2, 2), (4, 2), (4, 4), (2, 4)], closed=True)
    for r in (outer, inner):
        r["attrs"]["fill"] = "none"
    fake([outer, inner])
    drawing = json.loads(draw2d.vw2d_check(layer="L", image=False, preset="drawing"))
    assert drawing["summary"]["errors"] == 0
    full = json.loads(draw2d.vw2d_check(layer="L", image=False, checks=["overlap"]))
    assert full["summary"]["errors"] == 1  # the plan check stays strict
    for r in (outer, inner):
        r["attrs"]["fill"] = "solid"
    fake([outer, inner])
    drawing = json.loads(draw2d.vw2d_check(layer="L", image=False, preset="drawing"))
    assert [i["kind"] for i in drawing["issues"]] == ["overlap"]
    twin = dict(inner, uuid="twin")
    twin["attrs"] = dict(inner["attrs"], fill="none")
    inner["attrs"]["fill"] = "none"
    fake([outer, inner, twin])
    drawing = json.loads(draw2d.vw2d_check(layer="L", image=False, preset="drawing"))
    assert [i["kind"] for i in drawing["issues"]] == ["duplicate"]  # a doubled outline still counts


def test_revert_mapping_is_compact(fake):
    f = fake()
    f.journal.record("Test.vwx", "vw2d_fix", [{"op": "replaced", "before": [], "after": []}])
    out = generic.vw_revert_last()
    assert out["mapping"]["count"] == 50


def test_join_and_simplify_refuse_zero_tolerance(fake):
    fake([_line((0, 0), (1, 0)), _line((1, 0), (2, 0))])
    with pytest.raises(ToolError, match="more than 0"):
        cleanup.vw2d_join(layer="L", tolerance_mm=0)
    with pytest.raises(ToolError, match="more than 0"):
        cleanup.vw2d_simplify(layer="L", tolerance_mm=-1)
