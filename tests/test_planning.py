"""Planning knowledge (Raumpilot): engine, knowledge base, styles, symbols,
tools (server side, Vectorworks faked)."""

import json
import math
import uuid as _uuid

import pytest
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError

from vwmcp import app
from vwmcp.geom import specs as S
from vwmcp.journal import Journal
from vwmcp.planning import engine as E
from vwmcp.planning import kb, style, symbols
from vwmcp.tools import generic
from vwmcp.tools import planning as T

MM = {"name": "mm", "area_name": "m²", "upi": 25.4, "mm_per_unit": 1.0}
SIDES = {"HWB": {"WC": 20, "Wand": 20, "DU": 20}, "WC": {"Wand": 20, "DU": 20}}


def wc():
    return E.Spec("wc", 40, 60, "WC", areas=[E.Area("front", 80, 60, 75)])


def hwb():
    return E.Spec("hwb", 45, 35, "HWB", areas=[E.Area("front", 70, 45, 75)])


def shower():
    return E.Spec("du", 80, 80, "DU", areas=[E.Area("front", 90, 75, align="any")], corner_ok=True)


# ---- engine --------------------------------------------------------------------------

@pytest.mark.parametrize("wall,rect", [
    ("N", [10, 140, 50, 200]), ("S", [150, 0, 190, 60]), ("W", [0, 10, 60, 50]), ("E", [140, 150, 200, 190]),
])
def test_at_wall_measures_from_the_left_corner_seen_from_inside(wall, rect):
    p = E.at_wall(wc(), wall, 10.0, 200.0, 200.0)
    assert p.rect.as_list() == rect
    assert p.wall == wall
    # it faces into the room
    cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    fx, fy = p.facing
    assert 0 < cx + fx * 50 < 200 and 0 < cy + fy * 50 < 200


def test_area_front_side_and_extra_depth():
    p = E.at_wall(wc(), "N", 50.0, 200.0, 200.0)
    (a, r), = p.area_rects()
    assert r.as_list() == [30, 80, 110, 140]  # 80 wide, 60 deep in front
    (a, r), = p.area_rects(extra=True)
    assert r.as_list() == [30, 65, 110, 140]
    bed = E.Spec("bett", 100, 200, areas=[E.Area("left", None, 70), E.Area("right", None, 70)])
    q = E.at_wall(bed, "N", 100.0, 400.0, 400.0)
    rs = [r.as_list() for _, r in q.area_rects()]
    assert rs == [[30, 200, 100, 400], [200, 200, 270, 400]]


def test_check_finds_overlap_blocked_area_side_distance_and_door():
    W, D = 160.0, 165.0
    a = E.at_wall(wc(), "N", 20.0, W, D, "wc")
    b = E.at_wall(hwb(), "N", 70.0, W, D, "hwb")  # only 10 cm from the WC
    iss = E.check([a, b], W, D, [], SIDES)
    assert any(i.kind == "side_distance" and "10 cm, mindestens 20" in i.message for i in iss)
    c = E.Placed(hwb(), 40.0, 100.0, 0.0, None, "box")  # standing in the WC's movement area
    kinds = {i.kind for i in E.check([a, c], W, D, [], SIDES)}
    assert "movement_blocked" in kinds
    door = E.Door("N", 0.0, 80.0, "in")
    iss = E.check([a], W, D, [door], SIDES)
    assert {"door_distance", "door_swing"} <= {i.kind for i in iss}


def test_opposite_elements_need_the_larger_depth():
    W, D = 120.0, 175.0
    a = E.at_wall(wc(), "N", 40.0, W, D, "wc")
    b = E.at_wall(hwb(), "S", 37.5, W, D, "hwb")
    iss = E.check([a, b], W, D, [], {})
    assert any(i.kind == "opposite" and "80 cm frei, 75 cm nötig" not in i.message for i in iss) is False
    b2 = E.at_wall(hwb(), "S", 37.5, W, 160.0, "hwb")
    a2 = E.at_wall(wc(), "N", 40.0, W, 160.0, "wc")
    iss = E.check([a2, b2], W, 160.0, [], {})
    assert any(i.kind == "opposite" and "65 cm frei, 75 cm nötig" in i.message for i in iss)


def test_any_aligned_area_slides_to_a_free_place():
    W, D = 160.0, 165.0
    du = E.at_wall(shower(), "N", 0.0, W, D, "du")
    w = E.Placed(hwb(), 0.0, 42.5, 90.0, "W", "hwb")  # under the shower on the left wall
    iss = E.check([du, w], W, D, [], {})
    assert not [i for i in iss if i.kind == "movement_blocked" and i.elements[0] == "du"]
    assert du.shifts[0] != 0.0


def test_walkable_floor_shower_never_blocks():
    W, D = 200.0, 180.0
    floor = E.Spec("dp", 120, 120, walkable=True)
    p = E.at_wall(floor, "N", 80.0, W, D, "dp")
    w = E.at_wall(E.Spec("wb", 60, 55, areas=[E.Area("front", 120, 120, align="any")]), "N", 20.0, W, D, "wb")
    iss = E.check([p, w], W, D, [], {})
    assert not [i for i in iss if i.kind == "movement_blocked"]


def test_generate_duschbad_finds_valid_variants_and_respects_the_door():
    lays = E.generate([wc(), hwb(), shower()], 160, 165, [E.Door("S", 70, 80, "in")], SIDES, limit=6)
    ok = [lay for lay in lays if not any(i.severity == "error" for i in lay.issues)]
    assert ok
    for lay in ok:
        assert not [i for i in lay.issues if i.kind.startswith("door")]


def test_transform_point_and_turn_wall_agree():
    W, D = 300.0, 200.0
    for turn in range(4):
        for mirror in (False, True):
            Wt, Dt = E.turned_size(W, D, turn)
            # a point on each wall lands on the turned wall
            for wall, pt in {"N": (100, D), "S": (100, 0), "W": (0, 50), "E": (W, 50)}.items():
                x, y = E.transform_point(*pt, W, D, turn, mirror)
                tw = E.turn_wall(wall, turn, mirror)
                on = {"N": abs(y - Dt), "S": abs(y), "W": abs(x), "E": abs(x - Wt)}[tw]
                assert on < 1e-9, (turn, mirror, wall)


# ---- knowledge base --------------------------------------------------------------------

def test_data_is_complete_and_consistent():
    els = kb.elements()
    assert len(els) >= 60 and len(kb.rules()) >= 500 and len(kb.layouts()) >= 50
    for e in els.values():
        assert e["kind"] in symbols.KINDS, e["id"]
        assert e["w"] > 0 and e["d"] > 0
        assert e.get("sources"), e["id"]
        for a in e.get("areas", []) + e.get("areas_bf", []) + e.get("areas_rg", []):
            assert a["side"] in ("front", "left", "right", "back", "either") and a["d"] > 0
    for lay in kb.layouts().values():
        if lay.get("reference_only"):
            continue
        for x in lay["elements"]:
            assert x["element"] in els or x["element"] in kb.ACCESSORIES, (lay["id"], x["element"])


def test_vdi_6000_side_distances():
    """Raumpilot Grundlagen S. 266 (VDI 6000 Blatt 1)."""
    s = kb.sides()
    assert E.side_distance(s, "WB", "WC") == 20
    assert E.side_distance(s, "SW", "Wand") == 25
    assert E.side_distance(s, "DU", "WM/TR") == 3
    assert E.side_distance(s, "EWT", "DU") == 15
    assert E.side_distance(s, "DU", "Wand") == 0  # may stand in the corner
    assert kb.door_reveal() == 10


def test_core_elements_have_the_book_values():
    wcu = kb.element("wc_wandeinbau")
    assert (wcu["w"], wcu["d"]) == (40, 60) and wcu["short"] == "WC"
    a = wcu["areas"][0]
    assert (a["w"], a["d"], a["d_extra"]) == (80, 60, 75)
    assert wcu["areas_rg"][0]["w"] == 150 and wcu["areas_bf"][0]["w"] == 120
    wb = kb.element("einzelwaschtisch")
    assert (wb["w"], wb["d"], wb["areas"][0]["w"], wb["areas"][0]["d"]) == (60, 55, 90, 55) and wb["wall"]
    tub = kb.element("badewanne")
    assert (tub["w"], tub["d"]) == (170, 75) and tub["areas_rg"][0]["w"] >= tub["areas_rg"][0]["d"]
    assert kb.element("duschplatz_150")["walkable"]


def test_barrier_free_areas_from_the_independent_check():
    """Raumpilot Grundlagen S. 274 (transfer area beside the wheelchair WC),
    S. 306/309 (in front of furniture and beds), S. 238/308 (wheelchair)."""
    wcu = kb.element("wc_wandeinbau")
    tr = [a for a in wcu["areas_rg"] if a["side"] == "either"]
    assert tr and (tr[0]["d"], tr[0]["w"]) == (95, 70)
    bed = kb.element("doppelbett")
    assert {"side": "front", "d": 90.0} == {k: bed["areas_bf"][-1][k] for k in ("side", "d")}
    assert any(a["side"] == "front" and a["d"] == 90 for a in kb.element("einzelbett")["areas_bf"])
    ks = kb.element("kleiderschrank")
    assert ks["areas"][0]["d"] == 70 and ks["areas_bf"][0]["d"] == 90 and ks["areas_rg"][0]["d"] == 150
    # the whole kitchen row has 120 in front (wheelchair 150), not only the base cabinets
    for eid in ("kuehlschrank", "kueche_hochschrank", "kueche_unterschrank"):
        e = kb.element(eid)
        assert e["areas"][0]["d"] == 120 and e["areas_rg"][0]["d"] == 150, eid
    # armchair: the living-room chapter's standard size, the others as variants
    sessel = kb.element("sessel")
    assert (sessel["w"], sessel["d"]) == (90, 85) and any(v["id"] == "60x60" for v in sessel["variants"])


def test_sources_are_printed_pages():
    """Sources and page refs inside texts use the printed page (PDF - 2)."""
    assert kb.layouts()["kueche_l"]["sources"] == [{"book": "G", "page": 206}]
    assert {"book": "G", "page": 217} in kb.element("essplatz_6p")["sources"]
    rule = next(r for r in kb.rules() if r["id"] == "g3.leuchte")
    assert rule["sources"] == [{"book": "G", "page": 218}]
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location(
        "build_planning_kb", pathlib.Path(__file__).parent.parent / "tools" / "build_planning_kb.py")
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    assert b.printed_refs("Tabelle S. 105 = wie S. 54") == "Tabelle S. 103 = wie S. 52"
    assert b.printed_refs("p232, Seiten 84-86") == "p230, Seiten 82-84"
    assert b.printed_refs("VDI 6000 Blatt 1, M 1:50") == "VDI 6000 Blatt 1, M 1:50"


CORE_LAYOUTS = ["wc_raum_a", "wc_raum_b", "wc_raum_c", "duschbad_a", "duschbad_wc_a", "duschbad_wc_b",
                "wannenbad_wc_a", "wannenbad_wc_b", "wannenbad_wc_c", "vollbad_a", "barrierefrei_bad_beispiel",
                "rollstuhl_bad_beispiel", "rf_bf_waschtisch", "rf_bf_wc", "bf_bf_wc", "bf_bf_dusche",
                "kueche_einzeilig", "kueche_zweizeilig", "kueche_l", "kueche_u", "kueche_arbeitsinsel",
                "kueche_l_essplatz", "kueche_einzeilig_rollstuhl", "kueche_u_barrierefrei"]


def test_the_core_standard_situations_are_usable():
    """Bathrooms, WC rooms and kitchens from Raumpilot Grundlagen S. 203-276
    pass their own rules (otherwise they would only be reference data)."""
    for lid in CORE_LAYOUTS:
        assert lid in kb.layouts(), lid
        assert not kb.layouts()[lid].get("reference_only"), lid


def test_every_book_layout_passes_its_own_rules():
    """The Raumpilot standard situations are the reference: transcribed
    correctly and checked with the same rules, they have no problems."""
    bad = []
    for lay in kb.layouts().values():
        if lay.get("reference_only"):
            continue
        mode = lay.get("mode") or "standard"
        f = kb.fit(lay, lay["W"], lay["D"], mode=mode)
        assert f is not None, lay["id"]
        errs = [i for i in E.check(f.placed, f.W, f.D, f.doors, kb.sides(mode), kb.door_reveal())
                if i.severity == "error"]
        if errs:
            bad.append((lay["id"], [i.message for i in errs]))
    assert not bad


def test_fit_keeps_walls_when_the_room_is_bigger_and_turned():
    lay = kb.layouts()["duschbad_wc_b"]
    for turn in range(4):
        for mirror in (False, True):
            W0, D0 = E.turned_size(lay["W"], lay["D"], turn)
            f = kb.fit(lay, W0 + 40, D0 + 25, turn, mirror)
            assert f is not None
            errs = [i for i in E.check(f.placed, f.W, f.D, [], kb.sides(), 10) if i.severity == "error"]
            assert not errs, (turn, mirror, [i.message for i in errs])
            assert all(p.wall for p in f.placed)
    assert kb.fit(lay, lay["W"] - 1, lay["D"]) is None


def test_search_with_umlauts_and_stems():
    hits = kb.search("Dusche")
    assert "duschwanne" in hits["elements"]
    assert kb.search("tür breite")["rules"]
    assert kb.search("treppe steigung")["rules"]
    assert kb.source_text([{"book": "G", "page": 266}, {"norm": "VDI 6000"}]) == "Raumpilot Grundlagen S. 266, VDI 6000"


# ---- symbols ------------------------------------------------------------------------------

@pytest.mark.parametrize("eid", sorted(kb.elements()))
def test_every_element_symbol_normalizes(eid):
    e = kb.elements()[eid]
    st = style.resolve("raumpilot")
    items = T.element_items(e, e["w"], e["d"], dict(e.get("params") or {}), st)
    assert items
    for it in items:
        n, _ = S.normalize(it, 1.0, {})
        assert n["class"]
    # within the Stellfläche (local frame: x in [-w/2, w/2], y in [-d, 0]), 1 mm tolerance
    from vwmcp.catalog.preview import item_points
    for it in items:
        pts, _ = item_points(it)
        for x, y in pts:
            assert -e["w"] * 5 - 1 <= x <= e["w"] * 5 + 1 and -e["d"] * 10 - 1 <= y <= 1, (eid, it["type"], x, y)


# ---- styles --------------------------------------------------------------------------------

@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VWMCP_HOME", str(tmp_path))
    return tmp_path


def test_default_style_is_raumpilot(home):
    st = style.resolve()
    assert st["name"] == "raumpilot" and st["classes"]["movement"]["fill"] == "#b3d0df"
    assert style.class_for(st, "object", "sanitaer")[0] == "Ausstattung-Sanitär"
    assert style.class_for(st, "detail", "kueche")[0] == "Ausstattung-Küche-Detail"


def test_user_style_overrides_and_persists(home):
    style.save("Büro X", {"classes": {"movement": {"name": "A-Bewegung", "fill": "#dddddd"}},
                          "show": {"movement_extra": False},
                          "symbols": {"wc_wandeinbau": {"symbol": "WC Geberit", "dy": -30}}}, activate=True)
    st = style.resolve()
    assert st["name"] == "Büro X" and st["classes"]["movement"]["name"] == "A-Bewegung"
    assert st["classes"]["movement"]["line_style"] == "none"  # inherited
    assert st["show"]["movement_extra"] is False and st["show"]["movement"] is True
    assert json.loads((home / "styles.json").read_text())["active"] == "Büro X"
    style.save("Büro X", {"symbols": {"wc_wandeinbau": None}})
    assert style.resolve("Büro X")["symbols"] == {}
    style.delete("Büro X")
    assert style.active_name() == "raumpilot"


@pytest.mark.parametrize("bad,msg", [
    ({"classes": {"wall": {}}}, "unknown class role"),
    ({"classes": {"movement": {"fill": "blau"}}}, "#rrggbb"),
    ({"show": {"movement": "ja"}}, "true/false"),
    ({"symbols": {"wc": {"name": "x"}}}, "symbol"),
    ({"farbe": 1}, "unknown style keys"),
])
def test_style_validation(home, bad, msg):
    with pytest.raises(style.StyleError, match=msg):
        style.save("x", bad)


def test_style_base_chain(home):
    style.save("a", {"classes": {"room": {"line_weight": 0.5}}})
    style.save("b", {"base": "a", "geometry": "outline"})
    st = style.resolve("b")
    assert st["classes"]["room"]["line_weight"] == 0.5 and st["geometry"] == "outline"
    with pytest.raises(style.StyleError, match="base of b"):
        style.delete("a")


# ---- tools ---------------------------------------------------------------------------------

class FakeVW:
    def __init__(self):
        self.calls = []
        self.instances = []

    def __call__(self, fn, timeout=None, **kw):
        self.calls.append((fn, kw))
        if fn == "units":
            return MM
        if fn == "place_elements":
            mk = lambda: {"uuid": str(_uuid.uuid4())}  # noqa: E731
            return {"document": "T.vwx", "classes_created": [], "folders_created": ["Keystone Sanitär"],
                    "symbols_created": [g["symbol"] for g in kw["groups"] if g["items"]], "symbols_imported": [],
                    "groups": [{"symbol": g["symbol"], "uuid": "sd", "definition": [],
                                "instances": [mk() for _ in g["placements"]]} for g in kw["groups"]],
                    "underlay": [mk() for _ in kw["underlay"]], "overlay": [mk() for _ in kw["overlay"] or []]}
        if fn == "element_instances":
            return self.instances
        if fn == "read_objects":
            return {"objects": [{"uuid": "room", "bbox": [1000.0, 2000.0, 2600.0, 3650.0], "layer": "EG"}]}
        if fn == "current_document":
            return "T.vwx"
        if fn == "revert2d":
            return {"document": "T.vwx", "deleted": len(kw["ops"]), "mapping": {}}
        raise AssertionError(fn)


@pytest.fixture
def fake(home, monkeypatch):
    f = FakeVW()
    monkeypatch.setattr(app, "vw2d", f)
    monkeypatch.setattr(app, "vw", f)
    monkeypatch.setattr(generic, "vw", f)
    monkeypatch.setattr(app, "get_journal", lambda: Journal(home / "j.jsonl"))
    return f


def test_knowledge_overview_query_element_room_layout(fake):
    d = json.loads(T.vw2d_knowledge()[0])
    assert "sanitaer" in d["elements_by_category"] and d["rule_topics"]
    out = T.vw2d_knowledge(element="wc_wandeinbau", mode="rollstuhlgerecht")
    assert json.loads(out[0])["element"]["source"].startswith("Raumpilot Grundlagen")
    assert isinstance(out[1], Image)
    out = T.vw2d_knowledge(room="duschbad_wc")
    assert json.loads(out[0])["room"]["standard_layouts"] and isinstance(out[1], Image)
    out = T.vw2d_knowledge(layout="wc_raum_b")
    assert json.loads(out[0])["source"].startswith("Raumpilot Grundlagen S.")
    t = next(iter(kb.topics()))
    assert json.loads(T.vw2d_knowledge(topic=t)[0])["count"] >= 1
    with pytest.raises(ToolError, match="unknown element"):
        T.vw2d_knowledge(element="nope")


def test_plan_room_without_vectorworks(fake):
    out = T.vw2d_plan_room(160, 165, room="duschbad_wc", door={"wall": "S", "offset": 70, "width": 80})
    info = json.loads(out[0])
    assert info["variants"] and info["variants"][0]["ok"] and "Raumpilot" in info["variants"][0]["name"]
    assert isinstance(out[1], Image)
    assert all(fn != "place_elements" for fn, _ in fake.calls)
    small = json.loads(T.vw2d_plan_room(100, 100, elements=["wc_wandeinbau", "badewanne"])[0])
    assert not any(v["ok"] for v in small["variants"]) and "hint" in small


def test_plan_room_draws_one_undo_step(fake):
    out = T.vw2d_plan_room(160, 165, room="duschbad_wc", draw=True, origin=[1000, 2000], dimensions=True)
    info = json.loads(out[0])
    fn, kw = next(c for c in fake.calls if c[0] == "place_elements")
    # movement areas first (inside the room), room outline, then symbols, then dimensions
    assert kw["underlay"][0]["kind"] in ("poly", "rect")
    classes = {it["class"] for it in kw["underlay"]}
    assert {"Bewegungsfläche", "Raumbegrenzung"} <= classes
    for it in kw["underlay"]:
        if it["class"].startswith("Bewegungsfläche"):
            xs = [p[0] for p in it.get("points", [])] or [it.get("p1", [0])[0], it.get("p2", [0])[0]]
            assert all(1000 - 1e-6 <= x <= 2600 + 1e-6 for x in xs)
    assert {g["symbol"].split(" ")[0] for g in kw["groups"]} >= {"WC"}
    assert any(it["kind"] == "chain_dim" or it["kind"] == "dim" for it in kw["overlay"])
    assert info["drawn"]["variant"] == 1
    generic.vw_revert_last()
    fn, kw = fake.calls[-1]
    assert fn == "revert2d" and {o["op"] for o in kw["ops"]} >= {"created", "symdef", "symfolder"}


def test_place_element_with_own_symbol_mapping(fake):
    style.save("büro", {"symbols": {"wc_wandeinbau": {"symbol": "WC Büro", "dy": -30, "rotation": 180,
                                                      "library": "/Bib.vwx"}}}, activate=True)
    out = T.vw2d_place_element("wc_wandeinbau", position=[1000, 1000], wall="left", unit="mm")
    fn, kw = fake.calls[-1]
    g = kw["groups"][0]
    assert g["symbol"] == "WC Büro" and g["items"] == [] and g["library"] == "/Bib.vwx"
    # Keystone's insertion (1000, 1000), turned 90°: the symbol's point is 30 cm into the room
    (pl,) = g["placements"]
    assert pl["position"] == pytest.approx([1300.0, 1000.0]) and pl["rotation"] == pytest.approx(270.0)
    assert out["symbols"][0]["own_symbol"] is True
    # movement area drawn from the wall: 80 wide x (60 + 60) deep, turned
    rects = [it for it in kw["underlay"] if it["class"] == "Bewegungsfläche"]
    assert rects


def test_place_element_keystone_symbol_and_modes(fake):
    T.vw2d_place_element("einzelwaschtisch", position=[0, 0], mode="rollstuhlgerecht", unit="cm")
    fn, kw = fake.calls[-1]
    g = kw["groups"][0]
    assert g["symbol"].startswith("Einzelwaschtisch") and g["items"] and "Keystone-Element einzelwaschtisch" in g["description"]
    (r,) = [it for it in kw["underlay"] if it["class"] == "Bewegungsfläche"]
    xs = sorted([r["p1"][0], r["p2"][0]])
    assert xs == pytest.approx([-750.0, 750.0])  # 150 cm wide
    with pytest.raises(ToolError, match="no variant"):
        T.vw2d_place_element("einzelwaschtisch", variant="riesig")


def test_check_room_reads_keystone_elements(fake):
    # a WC on the left wall of a 160 x 165 room at (1000, 2000) mm
    fake.instances = [{"uuid": "u-wc", "symbol": "WC 40×60", "rotation": 90.0, "layer": "EG",
                       "description": "Keystone-Element wc_wandeinbau 40x60 cm. ...", "location": [1000.0, 2400.0]},
                      {"uuid": "u-x", "symbol": "Fremd", "description": "", "rotation": 0.0, "layer": "EG",
                       "location": [1500.0, 2500.0]}]
    out = T.vw2d_check_room(room_uuid="room")
    info = json.loads(out[0])
    assert info["room_cm"] == [160.0, 165.0]
    (e,) = info["elements"]
    assert e["element"] == "wc_wandeinbau" and e["wall"] == "W"
    assert info["ok"] and info["not_recognised"] == ["Fremd"]


def test_style_tool(fake):
    assert T.vw2d_style("list")["styles"] == ["raumpilot"]
    T.vw2d_style("save", "mein", {"classes": {"object": {"pen": "#000000"}}})
    assert T.vw2d_style("show")["style"]["classes"]["object"]["pen"] == "#000000"
    with pytest.raises(ToolError):
        T.vw2d_style("save", "mein", {"show": {"movement": 3}})
    T.vw2d_style("use", "raumpilot")
    assert T.vw2d_style("show")["active"] == "raumpilot"


def test_tools_with_pictures_have_no_structured_output():
    """A tool that returns text + preview image must not declare structured
    output: pydantic >= 2.14.2 cannot serialise the Image and the call fails
    ("Error executing tool"), seen live in Claude Desktop on 01.11.2026."""
    import typing

    from mcp.server.mcpserver import Image as Img

    from vwmcp.server import mcp

    pic = [json.dumps({"ok": True}), Img(data=b"\x89PNG\r\n\x1a\n", format="png")]
    checked = []
    for t in mcp._tool_manager.list_tools():
        ret = str(typing.get_type_hints(t.fn).get("return"))
        if "Image" in ret or ret in ("list[typing.Any]", "typing.Any"):
            assert t.output_schema is None, t.name
            t.fn_metadata.convert_result(pic)  # must not raise
            checked.append(t.name)
    assert {"vw2d_knowledge", "vw2d_plan_room", "vw2d_check_room", "vw2d_assets"} <= set(checked)


def test_door_offset_is_read_off_the_plan():
    """offset runs in plan from the room's bottom-left corner (x on N/S, y on
    W/E); the hinge is seen from inside (inward door: DIN links/rechts)."""
    W, D = 160.0, 165.0
    s = E.Door("S", 70.0, 80.0, "in", "left")
    assert s.span(W, D).as_list() == [70.0, 0.0, 150.0, 0.0]
    assert s.swing_rect(W, D).as_list() == [70.0, 0.0, 150.0, 80.0]
    assert E.Door("E", 20.0, 80.0).span(W, D).as_list() == [160.0, 20.0, 160.0, 100.0]
    assert E.Door("N", 20.0, 80.0).span(W, D).as_list() == [20.0, 165.0, 100.0, 165.0]
    assert E.Door("W", 20.0, 80.0).swing_rect(W, D).as_list() == [0.0, 20.0, 80.0, 100.0]
    # left hinge of an inward door in the south wall, seen from inside = the east jamb
    lay = E.Layout(W, D, [], [s])
    _, over = T._room_overlay(lay, style.resolve("raumpilot"), (0.0, 0.0), outline=False, dims=False)
    leaf = [o for o in over if o["type"] == "line"][0]
    assert leaf["p1"] == [1500.0, 0.0] and leaf["p2"] == [1500.0, 800.0]
    # the tool takes it in the room's unit
    d = T._door_arg({"wall": "unten", "offset": 0.7, "width": 0.8}, 100.0)[0]
    assert (d.wall, d.offset, d.width) == ("S", 70.0, 80.0)
