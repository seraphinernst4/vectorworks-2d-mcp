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


def _numbers(x):
    if isinstance(x, dict):
        return [v for k in sorted(x) for v in _numbers(x[k])]
    if isinstance(x, (list, tuple)):
        return [v for i in x for v in _numbers(i)]
    return [x]


def test_mirror_items_is_exact_and_involutive():
    items = catalog.build("spuele", "plan") + catalog.build("baum", "plan") + [SH.arc(10, 5, 3, 20, 70)]
    twice = SH.mirror_items(SH.mirror_items(items))
    for a, b in zip(items, twice):
        na, nb = _numbers(a), _numbers(b)
        assert len(na) == len(nb)
        for x, y in zip(na, nb):
            if isinstance(x, float):
                assert abs(x - y) <= 1e-9, (a, b)
            else:
                assert x == y
    once = SH.mirror_items(items)
    assert all(abs(p[0] + q[0]) < 1e-12 and p[1] == q[1] for a, b in zip(items, once) if a["type"] == "polygon"
               for p, q in zip(a["points"], b["points"]))
    # an arc from 20 deg sweeping 70: mirrored covers 90..160
    arc = once[-1]
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
        self.layer.kids.append(g)
        self.last = g

    def InsertSymbolInFolder(self, f, s):
        s.parent = f
        f.kids.append(s)

    def FInFolder(self, f):
        return f.kids[0] if f.kids else None

    def FInGroup(self, h):
        return h.kids[0] if h.kids else None

    def NextObj(self, h):
        if h.parent is None or getattr(h.parent, "t", None) == 92 and h not in h.parent.kids:
            lib = self._library()
            i = lib.index(h) if h in lib else len(lib)
            return lib[i + 1] if i + 1 < len(lib) else None
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

    def FSymDef(self):
        lib = self._library()
        return lib[0] if lib else None

    def _library(self):
        return [o for o in self.names.values() if o.t in (16, 92) and getattr(o.parent, "t", None) != 92]

    def GetSymName(self, h):
        return h.sym

    def GetName(self, h):
        return h.name

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



def _extent(items):
    pts = [p for it in items for p in P.item_points(it)[0]]
    for it in items:  # circles exactly (sampling may miss their extremes)
        if it["type"] == "circle":
            (cx, cy), r = it["center"], it["radius"]
            pts += [(cx - r, cy), (cx + r, cy), (cx, cy - r), (cx, cy + r)]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


@pytest.mark.parametrize("asset_id,view", [
    ("stuhl", "front"), ("stuhl", "side"), ("tisch", "front"), ("person", "front"), ("baum", "front"),
    ("pkw", "side"), ("waschmaschine", "front"), ("kuechenzeile", "front"), ("badewanne", "side"),
    ("wc_stand", "side")])
def test_standing_elevations_touch_the_floor(asset_id, view):
    for params in (None, {"h": 1700} if asset_id == "pkw" else None, {"l": 4000} if asset_id == "pkw" else None):
        x0, y0, x1, y1 = _extent(catalog.build(asset_id, view, params))
        assert abs(y0) < 1e-6, (asset_id, view, params, y0)


@pytest.mark.parametrize("asset_id", ["wc_wand", "wc_stand", "urinal", "waschtisch", "handwaschbecken", "dusche",
                                      "badewanne", "spuele", "kochfeld", "waschmaschine", "kuehlschrank", "bett",
                                      "sofa", "schrank", "schreibtisch", "sessel", "nachttisch", "kommode", "regal",
                                      "kuechenzeile"])
def test_wall_mounted_plans_stand_at_y0_towards_minus_y(asset_id):
    x0, y0, x1, y1 = _extent(catalog.build(asset_id, "plan"))
    assert abs(y1) < 1e-6 and y0 < 0


def test_urinal_height_is_its_lowest_edge():
    x0, y0, x1, y1 = _extent(catalog.build("urinal", "front", {"h": 500}))
    assert abs(y0 - 500) < 1e-6


def test_wc_height_is_the_seat_top():
    x0, y0, x1, y1 = _extent(catalog.build("wc_wand", "side", {"h": 460}))
    assert abs(y1 - 460) < 1e-6


@pytest.mark.parametrize("d", [2000.0, 6000.0, 9000.0])
def test_tree_crown_fits_its_diameter_and_is_centred(d):
    for style in (0.0, 1.0, 2.0):
        x0, y0, x1, y1 = _extent(catalog.build("baum", "plan", {"d": d, "style": style}))
        assert max(x1 - x0, y1 - y0) <= d * 1.01 and max(x1 - x0, y1 - y0) >= d * 0.95, (d, style)
        assert abs(x0 + x1) < d * 0.01 and abs(y0 + y1) < d * 0.01


def test_round_ovals_become_circles():
    it = SH.korb_oval(0, 0, 500, 500)
    assert it["type"] == "circle" and it["radius"] == 250
    items = catalog.build("baum", "front", {"style": 1, "d": 3400, "h": 5000})  # crown 3400 x 3400
    assets._classed(items, "plants")
    for it in assets._classed(items, "plants"):
        S.normalize(it, 1.0, {})


def test_wall_parameter_turns_the_asset_away_from_the_wall():
    import math as _m

    def facing(rot):  # where local -y points after rotating counter-clockwise
        a = _m.radians(rot)
        return round(_m.sin(a), 9) + 0.0, round(-_m.cos(a), 9) + 0.0

    away = {"above": (0.0, -1.0), "left": (1.0, 0.0), "below": (0.0, 1.0), "right": (-1.0, 0.0)}
    for wall, direction in away.items():
        assert facing(assets.WALL_ROTATION[wall]) == direction, wall


def test_place_asset_wall_and_bad_rotation(fake):
    assets.vw2d_place_asset("wc_wand", wall="left")
    assert fake.calls[-1][1]["placements"][0]["rotation"] == 90.0
    assets.vw2d_place_asset("wc_wand", placements=[{"position": [0, 0], "wall": "right"}])
    assert fake.calls[-1][1]["placements"][0]["rotation"] == 270.0
    with pytest.raises(ToolError, match="rotation must be a number"):
        assets.vw2d_place_asset("wc_wand", rotation="quer")


def test_bad_parameters_are_clear_errors(fake):
    with pytest.raises(ToolError, match="must be > 0"):
        assets.vw2d_place_asset("bett", params={"b": 0})
    with pytest.raises(ToolError, match="doesn't fit"):
        assets.vw2d_place_asset("kuechenzeile", params={"l": 1800})
    with pytest.raises(ToolError, match="must be a number"):
        assets.vw2d_place_asset("bett", params={"b": "breit"})


@pytest.mark.parametrize("asset, params, msg", [
    ("tisch", {"chairs": 100}, "whole number from 0 to 24"),
    ("tisch", {"chairs": 4.5}, "whole number"),
    ("tisch", {"round_table": 0.5}, "one of 0, 1"),
    ("waschtisch", {"bowls": 3}, "one of 1, 2"),
    ("baum", {"style": 5}, "one of 0, 1, 2"),
    ("baum", {"seed": -1}, "from 0 to 1000"),
    ("baum", {"d": 500000}, "at most 100000 mm"),
    ("kuechenzeile", {"sink_w": -800}, "from 0 to 100000 mm"),
    ("bett", {"b": float("nan")}, "not a number"),
])
def test_parameter_ranges(asset, params, msg):
    with pytest.raises(KeyError, match=msg):
        catalog.build(asset, "plan", params)


def test_every_default_passes_the_range_checks():
    for a in catalog.CATALOG.values():
        for v in a.views:
            assert catalog.build(a.id, v, {k: d for k, (d, _) in a.params.items()})


def test_unused_parameters_make_no_extra_symbols():
    import inspect as _i

    for a in catalog.CATALOG.values():
        for v, fn in a.views.items():
            base = catalog.build(a.id, v)
            for p in _i.signature(fn).parameters:
                if p not in a.params or p in ("gully", "style", "round_table", "mirror", "bowls", "sink_w",
                                              "hob_w", "chairs"):
                    continue
                d = a.params[p][0]
                assert catalog.build(a.id, v, {p: d * 1.1 + 1}) != base, (a.id, v, p)


def test_vw_group_path_is_all_or_nothing():
    v = FakeVS(fail_on_item=3)
    ns = _vw_ns(v)
    with pytest.raises(RuntimeError, match="boom"):
        ns["place_asset"]("x", ITEMS, [{"position": [0, 0]}, {"position": [5, 0]}], as_symbol=False, cls="B")
    assert not [k for k in v.layer.kids if k not in v.deleted]



def test_vw_symdef_used_inside_another_definition_is_kept():
    v = FakeVS()
    ns = _vw_ns(v)
    ns["place_asset"]("WC", ITEMS, [{"position": [0, 0]}], folder=None, cls="A")
    inst = v.last
    outer = Obj(16, "Outer")
    nested = Obj(15, parent=outer)
    nested.sym = "WC"
    outer.kids.append(nested)
    v.names["Outer"] = outer
    v.objects_by_uuid = {inst.uuid: inst}
    v.GetObjectByUuid = lambda u: v.objects_by_uuid.get(u)
    r = ns["revert2d"]([{"op": "symdef", "name": "WC"}, {"op": "created", "uuid": inst.uuid}])
    assert "WC" in v.names and r["kept_in_use"] == ["WC"]
