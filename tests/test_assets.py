"""Asset catalogue, asset tools (server side) and place_asset (Vectorworks side,
against a small fake `vs`)."""

import copy
import json
import math
import uuid as _uuid

import pytest
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError

from vwmcp import app, calls, catalog
from vwmcp.catalog import preview as P
from vwmcp.catalog import shapes as SH
from vwmcp.geom import curves as C
from vwmcp.geom import specs as S
from vwmcp.journal import Journal
from vwmcp.tools import assets, generic

MM = {"name": "mm", "area_name": "m²", "upi": 25.4, "mm_per_unit": 1.0}
M = {"name": "m", "area_name": "m²", "upi": 0.0254, "mm_per_unit": 1000.0}


# ---------------------------------------------------------------- catalogue

def _all_variants():
    for a in catalog.CATALOG.values():
        for v in a.views:
            yield a.id, v


@pytest.mark.parametrize("asset_id,view", list(_all_variants()))
def test_every_asset_view_normalizes_exactly(asset_id, view):
    """Every catalogue item is a valid vw2d_draw item (mm) and normalizes."""
    items = catalog.build(asset_id, view)
    assert items
    for it in assets._classed(items, catalog.CATALOG[asset_id].category):
        n, exp = S.normalize(it, 1.0, {})
        assert n["class"].startswith(catalog.CATEGORY_CLASS[catalog.CATALOG[asset_id].category])
        assert n["attributes"] == {"by_class": True}


@pytest.mark.parametrize("asset_id,view", list(_all_variants()))
def test_every_asset_view_has_finite_tangent_paths(asset_id, view):
    """Paths: every arc's start and end lie on its circle (consistent radius)."""
    for it in catalog.build(asset_id, view):
        if it["type"] != "path":
            continue
        cur = it["start"]
        for s in it["segments"]:
            if "arc_to" in s:
                c = s["center"] if "center" in s else S._center_from_radius(
                    tuple(cur), tuple(s["arc_to"]), s["radius"], s["direction"] == "ccw", s["large"])
                r0 = math.dist(cur, c)
                r1 = math.dist(s["arc_to"], c)
                assert r0 > 0 and abs(r0 - r1) < 1e-6 * max(1.0, r0), (asset_id, view, s)
                cur = s["arc_to"]
            else:
                cur = s["line_to"]


def test_mirror_items_is_exact_and_involutive():
    items = catalog.build("spuele", "plan") + catalog.build("baum", "plan") + [SH.arc(10, 5, 3, 20, 70)]
    m = SH.mirror_items(items)
    assert SH.mirror_items(m) == [dict(it) for it in items] or all(
        assets._shape_key(a) == assets._shape_key(b) for a, b in zip(SH.mirror_items(m), items))
    # an arc from 20 deg sweeping 70: mirrored covers 90..160
    arc = m[-1]
    assert arc["center"] == [-10.0, 5.0] and arc["start_angle"] == 90.0 and arc["sweep"] == 70


def test_mirror_detects_symmetric_assets():
    _, mirrored = assets._items("wc_wand", "plan", None, True)
    assert mirrored is False
    _, mirrored = assets._items("spuele", "plan", None, True)
    assert mirrored is True


def test_symbol_names_are_deterministic_and_short():
    assert catalog.symbol_name("wc_wand", "plan") == "WC wandhängend - Grundriss"
    # h only matters in the elevations
    assert catalog.symbol_name("wc_wand", "plan", {"h": 460}) == "WC wandhängend - Grundriss"
    assert catalog.symbol_name("wc_wand", "front", {"h": 460}) == "WC wandhängend - Ansicht (h=460)"
    assert catalog.symbol_name("spuele", "plan", None, True) == "Küchenspüle mit Abtropffläche - Grundriss gespiegelt"
    long = catalog.symbol_name("tisch", "plan", {"b": 1234.5678, "t": 987.654, "chairs": 8, "round_table": 1})
    assert len(long) <= catalog.NAME_MAX


def test_build_rejects_unknown_parameters():
    with pytest.raises(KeyError, match="no parameter"):
        catalog.build("wc_wand", "plan", {"x": 1})
    with pytest.raises(KeyError, match="no view"):
        catalog.build("bett", "front")


def test_find_matches_german_words():
    ids = [a.id for a in catalog.find("toilette")]
    assert "wc_wand" in ids and "wc_stand" in ids
    assert [a.id for a in catalog.find("baum")] == ["baum"]
    assert all(a.category == "kitchen" for a in catalog.find(category="kitchen"))


def test_preview_renders_png():
    png = P.render([("WC", catalog.build("wc_wand", "plan")), ("Baum", catalog.build("baum", "front"))], cols=2)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------- vw2d_assets

def test_vw2d_assets_lists_and_previews():
    out = assets.vw2d_assets(query="wc")
    info = json.loads(out[0])
    assert info["count"] >= 2 and {"wc_wand", "wc_stand"} <= {a["id"] for a in info["assets"]}
    assert isinstance(out[1], Image)


def test_vw2d_assets_any_word_fallback():
    info = json.loads(assets.vw2d_assets(query="toilette zebra", preview=False)[0])
    assert {"wc_wand", "wc_stand"} <= {a["id"] for a in info["assets"]}


def test_vw2d_assets_one_asset_with_params():
    out = assets.vw2d_assets(asset="waschtisch", view="front", params={"b": 800, "mirror": 1})
    info = json.loads(out[0])
    assert info["asset"]["id"] == "waschtisch"
    assert info["symbol_names"] == {"front": "Waschtisch - Ansicht (b=800, mirror=1)"}
    with pytest.raises(ToolError, match="no view"):
        assets.vw2d_assets(asset="bett", view="front")
    with pytest.raises(ToolError, match="unknown asset"):
        assets.vw2d_assets(asset="einhorn")


# ---------------------------------------------------------------- vw2d_place_asset (server side)

def _rec_for(it, cls_override=None):
    """What Vectorworks reads back for a normalized item (ideal: exact)."""
    r = {"uuid": str(_uuid.uuid4()), "class": cls_override or it["class"], "layer": "", "name": ""}
    k = it["kind"]
    if k == "rect":
        (x1, y1), (x2, y2) = it["p1"], it["p2"]
        r.update(kind="rect", corners=[[x1, y1], [x2, y1], [x2, y2], [x1, y2]])
    elif k in ("circle", "arc"):
        r.update(kind="arc", center=list(it["center"]), radius=it["radius"],
                 start_angle=it.get("start", 0.0), sweep=it.get("sweep", 360.0))
    elif k == "line":
        r.update(kind="line", p1=list(it["p1"]), p2=list(it["p2"]))
    elif k == "poly":
        r.update(kind="polygon" if it["closed"] else "polyline", vertices=copy.deepcopy(it["verts"]),
                 closed=it["closed"], holes=copy.deepcopy(it.get("holes") or []))
    return r


class FakeAssetVW:
    def __init__(self, units=MM):
        self.units = units
        self.calls = []
        self.symbols: dict[str, list] = {}

    def __call__(self, fn, timeout=None, **kw):
        self.calls.append((fn, kw))
        if fn == "units":
            return self.units
        if fn == "place_asset":
            created = kw["as_symbol"] and kw["symbol"] not in self.symbols
            if kw["as_symbol"]:
                if created:
                    self.symbols[kw["symbol"]] = [_rec_for(it) for it in kw["items"]]
                definition = self.symbols[kw["symbol"]]
            else:
                definition = [_rec_for(it) for it in kw["items"]]
            inst = [{"uuid": str(_uuid.uuid4()), "kind": "symbol" if kw["as_symbol"] else "group",
                     "class": kw["cls"], "layer": kw["layer"] or "EG",
                     **({"location": pl["position"], "rotation": pl["rotation"]} if kw["as_symbol"] else {})}
                    for pl in kw["placements"]]
            return {"document": "Test.vwx", "symbol_created": created, "folder_created": created,
                    "classes_created": list(kw["new_classes"]) if created else [],
                    "symbol": {"name": kw["symbol"], "uuid": "sd-1"} if kw["as_symbol"] else None,
                    "definition": definition, "instances": inst}
        if fn == "revert2d":
            return {"document": "Test.vwx", "deleted": len(kw["ops"]), "restored": 0, "missing": [],
                    "mapping": {}}
        if fn == "current_document":
            return "Test.vwx"
        raise AssertionError(fn)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    f = FakeAssetVW()
    monkeypatch.setattr(app, "vw2d", f)
    monkeypatch.setattr(app, "vw", f)
    monkeypatch.setattr(generic, "vw", f)
    monkeypatch.setattr(app, "get_journal", lambda: Journal(tmp_path / "j.jsonl"))
    return f


def test_place_asset_creates_symbol_exactly_and_journals(fake):
    out = assets.vw2d_place_asset("wc_wand", position=[1000, 2000], rotation=90)
    assert out["symbol"] == "WC wandhängend - Grundriss" and out["symbol_created"] is True
    assert out["all_exact"] is True and out["max_deviation_mm"] < 1e-9
    assert out["placed"] == 1 and out["placement_deviation_mm"] == 0.0
    assert out["classes_created"] == ["Ausstattung-Sanitär", "Ausstattung-Sanitär-Detail"]
    fn, kw = fake.calls[-1]
    assert kw["folder"] == "Keystone Sanitär" and kw["cls"] == "Ausstattung-Sanitär"
    assert {it["class"] for it in kw["items"]} == {"Ausstattung-Sanitär", "Ausstattung-Sanitär-Detail"}
    assert kw["placements"] == [{"position": [1000.0, 2000.0], "rotation": 90.0}]
    assert "Mitte Wandseite" in kw["description"]
    # revert: instance first, then definition, then folder (journal is reversed in Vectorworks)
    generic.vw_revert_last()
    fn, kw = fake.calls[-1]
    assert fn == "revert2d"
    assert [o["op"] for o in kw["ops"]] == ["symfolder", "symdef", "created"]
    assert kw["ops"][1] == {"op": "symdef", "name": "WC wandhängend - Grundriss"}


def test_place_asset_reuses_existing_symbol(fake):
    assets.vw2d_place_asset("wc_wand")
    out = assets.vw2d_place_asset("wc_wand", placements=[{"position": [0, 0]}, {"position": [900, 0]}])
    assert out["symbol_created"] is False and out["placed"] == 2 and out["all_exact"] is True
    generic.vw_revert_last()
    _, kw = fake.calls[-1]
    assert [o["op"] for o in kw["ops"]] == ["created", "created"]


def test_place_asset_reports_a_differing_document_symbol(fake):
    fake.symbols["WC wandhängend - Grundriss"] = [_rec_for({"kind": "line", "p1": [0, 0], "p2": [1, 1],
                                                           "class": "X"})]
    out = assets.vw2d_place_asset("wc_wand")
    assert out["all_exact"] is False and "kept" in out["note"]


def test_place_asset_converts_units(fake):
    fake.units = M
    out = assets.vw2d_place_asset("dusche", position=[1.2, 3.4], unit="m")
    _, kw = fake.calls[-1]
    assert kw["placements"][0]["position"] == [1.2, 3.4]
    # asset geometry in mm -> document metres: the shower is 0.9 m wide
    rect = kw["items"][0]
    assert rect["kind"] == "rect" and list(rect["p1"]) == [-0.45, 0.0] and list(rect["p2"]) == [0.45, -0.9]
    out = assets.vw2d_place_asset("dusche", position=[1200, 3400], unit="mm")
    assert fake.calls[-1][1]["placements"][0]["position"] == [1.2, 3.4]
    assert out["all_exact"] is True


def test_place_asset_mirror_and_params(fake):
    out = assets.vw2d_place_asset("spuele", mirror=True, params={"b": 1000})
    assert out["mirrored"] is True and out["symbol"] == "Küchenspüle mit Abtropffläche - Grundriss (b=1000) gespiegelt"
    out = assets.vw2d_place_asset("wc_wand", mirror=True)
    assert out["mirrored"] is False and "symmetric" in out["note_mirror"]


def test_place_asset_groups(fake):
    out = assets.vw2d_place_asset("baum", as_symbol=False, position=[5000, 5000], class_name="Bäume")
    assert out["symbol"] is None and out["all_exact"] is True
    _, kw = fake.calls[-1]
    assert kw["as_symbol"] is False and kw["cls"] == "Bäume"
    assert out["instances"][0]["kind"] == "group"


def test_place_asset_errors(fake):
    with pytest.raises(ToolError, match="unknown asset"):
        assets.vw2d_place_asset("einhorn")
    with pytest.raises(ToolError, match="no view"):
        assets.vw2d_place_asset("bett", view="front")
    with pytest.raises(ToolError, match="no parameter"):
        assets.vw2d_place_asset("bett", params={"q": 2})
    with pytest.raises(ToolError, match="placement 0 needs"):
        assets.vw2d_place_asset("bett", placements=[{"rotation": 3}])


# ---------------------------------------------------------------- place_asset inside Vectorworks (fake vs)

class Obj:
    _n = 0

    def __init__(self, t, name="", parent=None):
        Obj._n += 1
        self.t, self.name, self.parent, self.kids = t, name, parent, []
        self.uuid = "u%d" % Obj._n
        self.cls = ""

    def __str__(self):
        return "H%d" % id(self)


class FakeVS:
    """Just enough of Vectorworks for place_asset's control flow."""

    def __init__(self, fail_on_item=None):
        self.names: dict[str, Obj] = {}
        self.layer = Obj(31, "EG")
        self.active_class = "Keine"
        self.classes = ["Keine"]
        self.stack: list[Obj] = []  # open symbol / group / folder
        self.last = None
        self.fail_on_item = fail_on_item
        self.drawn = 0
        self.deleted: list[Obj] = []

    # names, types
    def GetObject(self, n):
        return self.names.get(n)

    def GetTypeN(self, h):
        return h.t

    def GetObjectUuid(self, h):
        return h.uuid

    def NameObject(self, n):
        self._next_name = n

    # folders, symbols, groups
    def BeginFolderN(self, t):
        f = Obj(92, self._next_name)
        self.names[f.name] = f
        self.stack.append(f)

    def EndFolder(self):
        self.stack.pop()

    def BeginSym(self, n):
        s = Obj(16, n)
        self.stack.append(s)

    def EndSym(self):
        s = self.stack.pop()
        self.names[s.name] = s

    def BeginGroup(self):
        self.stack.append(Obj(11))

    def EndGroup(self):
        g = self.stack.pop()
        g.parent = self.layer
        self.last = g

    def InsertSymbolInFolder(self, f, s):
        s.parent = f
        f.kids.append(s)

    def FInFolder(self, f):
        return f.kids[0] if f.kids else None

    def FInGroup(self, h):
        return h.kids[0] if h.kids else None

    def NextObj(self, h):
        sib = h.parent.kids
        i = sib.index(h)
        return sib[i + 1] if i + 1 < len(sib) else None

    def GetParent(self, h):
        return h.parent

    def SetSymbolOptionsN(self, *a):
        pass

    def SetDescriptionText(self, h, t):
        h.desc = t
        return True

    def DelObject(self, h):
        self.deleted.append(h)
        if h.name in self.names and self.names[h.name] is h:
            del self.names[h.name]
        if h.parent is not None and h in h.parent.kids:
            h.parent.kids.remove(h)

    def Symbol(self, n, p, rot):
        o = Obj(15, parent=self.layer)
        o.sym, o.loc, o.rot, o.cls = n, p, rot, self.active_class
        self.layer.kids.append(o)
        self.last = o

    def LNewObj(self):
        return self.last

    def SetClass(self, h, c):
        h.cls = c

    def HMove(self, h, dx, dy):
        h.moved = (dx, dy)

    def HRotate(self, h, p, a):
        h.rotated = (p, a)

    def Count(self, crit):
        name = crit[4:-2]
        return float(sum(1 for o in self.layer.kids if getattr(o, "sym", None) == name))

    # state
    def PushAttrs(self):
        pass

    def PopAttrs(self):
        pass

    def ActLayer(self):
        return self.layer

    def GetLName(self, h):
        return h.name

    def ActiveClass(self):
        return self.active_class

    def NameClass(self, n):
        self.active_class = n
        if n not in self.classes:
            self.classes.append(n)

    def Layer(self, n):
        pass

    def GetLayerByName(self, n):
        return self.layer

    def GetFName(self):
        return "Test.vwx"


def _vw_ns(fake_vs):
    ns = {"vs": fake_vs}
    exec(calls.VWLIB_SOURCE, ns)  # noqa: S102
    exec(calls.VWLIB2D_SOURCE, ns)  # noqa: S102

    def make_one(it):
        if fake_vs.fail_on_item is not None and fake_vs.drawn == fake_vs.fail_on_item:
            raise RuntimeError("boom")
        fake_vs.drawn += 1
        o = Obj(2, parent=fake_vs.stack[-1])
        o.cls = fake_vs.active_class
        fake_vs.stack[-1].kids.append(o)
        return o

    ns.update(_make_one=make_one, apply_attrs=lambda h, a: None,
              read_obj=lambda h, *a, **k: {"uuid": h.uuid, "kind": {15: "symbol", 11: "group"}.get(h.t, "line"),
                                           "class": h.cls},
              class_names=lambda: list(fake_vs.classes),
              ensure_class=lambda n, attrs=None: fake_vs.classes.append(n) if n not in fake_vs.classes else None)
    return ns


ITEMS = [{"kind": "line", "class": "Ausstattung-Sanitär"}, {"kind": "line", "class": "Ausstattung-Sanitär-Detail"}]
NEW = {"Ausstattung-Sanitär": {}, "Ausstattung-Sanitär-Detail": {}}


def test_vw_place_asset_creates_folder_symbol_and_instances():
    v = FakeVS()
    ns = _vw_ns(v)
    out = ns["place_asset"]("WC", ITEMS, [{"position": [0, 0]}, {"position": [5, 0], "rotation": 90}],
                            folder="Keystone Sanitär", cls="Ausstattung-Sanitär", new_classes=NEW,
                            description="d")
    assert out["symbol_created"] and out["folder_created"]
    assert out["classes_created"] == ["Ausstattung-Sanitär", "Ausstattung-Sanitär-Detail"]
    sd = v.names["WC"]
    assert sd.parent is v.names["Keystone Sanitär"] and sd.desc == "d"
    assert [k.cls for k in sd.kids] == ["Ausstattung-Sanitär", "Ausstattung-Sanitär-Detail"]
    assert len(out["definition"]) == 2 and len(out["instances"]) == 2
    assert all(i["class"] == "Ausstattung-Sanitär" for i in out["instances"])
    assert v.active_class == "Keine"  # active class restored
    # second call reuses: no new definition, nothing drawn
    drawn = v.drawn
    out2 = ns["place_asset"]("WC", ITEMS, [{"position": [9, 9]}], folder="Keystone Sanitär",
                             cls="Ausstattung-Sanitär", new_classes=NEW)
    assert not out2["symbol_created"] and not out2["folder_created"] and v.drawn == drawn
    assert out2["classes_created"] == []


def test_vw_place_asset_is_all_or_nothing():
    v = FakeVS(fail_on_item=1)
    ns = _vw_ns(v)
    with pytest.raises(RuntimeError, match="boom"):
        ns["place_asset"]("WC", ITEMS, [{"position": [0, 0]}], folder="Keystone Sanitär",
                          cls="Ausstattung-Sanitär", new_classes=NEW)
    assert "WC" not in v.names and "Keystone Sanitär" not in v.names
    assert not v.stack  # BeginSym was closed
    assert not v.layer.kids


def test_vw_place_asset_refuses_a_name_used_by_something_else():
    v = FakeVS()
    v.names["WC"] = Obj(94, "WC")  # a class
    ns = _vw_ns(v)
    with pytest.raises(ValueError, match="already used"):
        ns["place_asset"]("WC", ITEMS, [{"position": [0, 0]}], cls="Ausstattung-Sanitär")


def test_vw_place_asset_groups_move_and_rotate():
    v = FakeVS()
    ns = _vw_ns(v)
    out = ns["place_asset"]("unused", ITEMS, [{"position": [3, 4], "rotation": 45}], as_symbol=False,
                            cls="Bäume")
    g = v.last
    assert g.t == 11 and g.moved == (3.0, 4.0) and g.rotated == ((3.0, 4.0), 45.0) and g.cls == "Bäume"
    assert len(out["definition"]) == 2 and "WC" not in v.names


def test_vw_revert_symdef_only_when_unused():
    v = FakeVS()
    ns = _vw_ns(v)
    out = ns["place_asset"]("WC", ITEMS, [{"position": [0, 0]}], folder="F", cls="A", new_classes=NEW)
    inst = v.last
    v.objects_by_uuid = {inst.uuid: inst}
    v.GetObjectByUuid = lambda u: v.objects_by_uuid.get(u)
    ops = [{"op": "symfolder", "name": "F"}, {"op": "symdef", "name": "WC"},
           {"op": "created", "uuid": inst.uuid}]
    # the user placed one more instance meanwhile -> definition and folder stay
    v.Symbol("WC", (1, 1), 0)
    r = ns["revert2d"](ops)
    assert "WC" in v.names and r["kept_in_use"] == ["WC"] and "F" in v.names
    # without other instances everything goes
    v.layer.kids.clear()
    ns["place_asset"]("WC", ITEMS, [{"position": [0, 0]}], folder="F", cls="A")
    inst = v.last
    v.objects_by_uuid = {inst.uuid: inst}
    r = ns["revert2d"]([{"op": "symdef", "name": "WC"}, {"op": "created", "uuid": inst.uuid}])
    assert "WC" not in v.names and r["deleted"] == 2
    v.names["F"].kids.clear()
    r = ns["revert2d"]([{"op": "symfolder", "name": "F"}])
    assert "F" not in v.names


@pytest.mark.parametrize("asset_id,view,params", [
    ("baum", "plan", {"style": 1}), ("baum", "plan", {"style": 2}), ("baum", "front", {"style": 1}),
    ("baum", "front", {"style": 2, "h": 6000, "d": 3000}), ("waschtisch", "plan", {"b": 1200, "bowls": 2}),
    ("waschtisch", "front", {"b": 1200, "bowls": 2, "mirror": 1}), ("kuechenzeile", "plan", {"sink_w": 0}),
    ("kuechenzeile", "front", {"l": 4200, "sink": 900}), ("tisch", "plan", {"round_table": 1, "chairs": 5}),
    ("tisch", "plan", {"chairs": 3}), ("dusche", "plan", {"gully": 0, "b": 1200, "t": 1200}),
    ("pkw", "side", {"l": 4200, "h": 1600}), ("regal", "plan", {"b": 2400}),
])
def test_parameter_variants_normalize(asset_id, view, params):
    a = catalog.CATALOG[asset_id]
    for it in assets._classed(catalog.build(asset_id, view, params), a.category):
        S.normalize(it, 1.0, {})
    for it in SH.mirror_items(catalog.build(asset_id, view, params)):
        S.normalize({k: v for k, v in it.items() if k != "role"}, 1.0, {})
