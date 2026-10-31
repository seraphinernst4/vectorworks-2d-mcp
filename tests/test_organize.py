"""vw2d_organize: rule matching, planning, the tool and the Vectorworks side."""

import types

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from vwmcp import app, calls
from vwmcp import organize as O
from vwmcp.journal import Journal
from vwmcp.tools import generic
from vwmcp.tools import organize as T

BC = {"fill": True, "fill_color": True, "pen_color": True, "line_weight": True, "line_style": True}
NOBC = dict(BC, pen_color=False)


def rec(uuid, kind="line", cls="Keine", layer="EG", pen="#000000", lw=0.25, parent=None, **kw):
    r = {"uuid": uuid, "kind": kind, "class": cls, "layer": layer, "name": kw.pop("name", ""),
         "bbox": kw.pop("bbox", [0, 0, 1, 1]),
         "attrs": {"pen": pen, "line_weight_mm": lw, "line_style": "solid", "fill": "none",
                   "fill_color": "#ffffff", "by_class": kw.pop("bc", BC)}}
    if parent:
        r["parent"] = parent
    r.update(kw)
    return r


def _poly(w, h):
    return [[0.0, 0.0, 0, 0.0], [w, 0.0, 0, 0.0], [w, h, 0, 0.0], [0.0, h, 0, 0.0]]


RECS = [
    rec("w1", "polygon", "A-WALL", pen="#ff0000", lw=0.508, closed=True, vertices=_poly(4.0, 3.0), holes=[]),
    rec("w2", "polygon", "a-wall-ext", pen="#ff0000", lw=0.508, closed=True, vertices=_poly(0.1, 0.04), holes=[]),
    rec("d1", "dimension", "Keine", pen="#00ff00"),
    rec("t1", "text", "Keine", text="Bad 4,2 m²"),
    rec("t2", "text", "Beschriftung", text="Küche", layer="EG"),
    rec("g1", "group", "Keine", attrs=None),
    rec("l1", "line", "0", pen="#808080", lw=0.13, parent="g1", bc=NOBC),
    rec("s1", "symbol", "Keine", symbol="WC wandhängend - Grundriss", attrs=None),
]


def test_globs_are_case_insensitive_and_lists_mean_any():
    assert O.matches(RECS[1], {"class": "A-WALL*"}, 1.0)
    assert O.matches(RECS[0], {"class": ["x", "a-wall"]}, 1.0)
    assert not O.matches(RECS[2], {"class": "A-*"}, 1.0)


def test_match_keys():
    m = 1000.0  # document in metres: area in m² as is
    assert O.matches(RECS[0], {"kind": "polygon", "pen": "#FF0000", "line_weight_mm": 0.5, "closed": True}, m)
    assert O.matches(RECS[0], {"area_m2": [1, None]}, m) and not O.matches(RECS[1], {"area_m2": [1, None]}, m)
    assert O.matches(RECS[3], {"text": r"m²$"}, m) and not O.matches(RECS[4], {"text": r"m²"}, m)
    assert O.matches(RECS[6], {"in_group": True}, m) and not O.matches(RECS[0], {"in_group": True}, m)
    assert O.matches(RECS[7], {"symbol": "WC*"}, m) and not O.matches(RECS[0], {"symbol": "*"}, m)
    assert O.matches(RECS[2], {"kind": ["dimension", "text"], "not": {"pen": "#000000"}}, m)
    assert O.matches(RECS[0], {"size_mm": [500, 2000]}, m)  # bbox 1 x 1 m
    assert O.matches(RECS[6], {"line_weight_mm": [0.1, 0.2]}, m)


def test_line_weights_compare_in_mils():
    for want, stored in ((0.5, 0.508), (0.35, 0.3556), (0.7, 0.7112), (1.0, 0.9906), (0.18, 0.1778),
                         (0.13, 0.127), (0.25, 0.254)):
        assert O.matches({"attrs": {"line_weight_mm": stored}}, {"line_weight_mm": want}, 1.0), want
    assert not O.matches({"attrs": {"line_weight_mm": 0.508}}, {"line_weight_mm": 0.35}, 1.0)
    assert O.matches({"attrs": {"line_weight_mm": 0.508}}, {"line_weight_mm": [0.3, 0.5]}, 1.0)


def test_area_from_exact_geometry():
    assert O.needs_geometry([{"match": {"area_m2": [1, None]}, "class": "x"}])
    r = rec("p", "polygon", closed=True, vertices=_poly(4000.0, 3000.0), holes=[])  # mm document
    assert O.matches(r, {"area_m2": [11.99, 12.01]}, 1.0)
    assert not O.matches(rec("l", "line", p1=[0, 0], p2=[1, 1]), {"area_m2": [0, None]}, 1.0)


def test_exact_names_with_brackets_match_themselves():
    assert O.matches({"class": "A-WALL [EXIST]"}, {"class": "A-WALL [EXIST]"}, 1.0)
    assert O.matches({"layer": "0"}, {"layer": "0"}, 1.0)


def test_validate_rejects_bad_rules():
    with pytest.raises(O.RuleError, match="no target"):
        O.validate([{"match": {"class": "x"}}])
    with pytest.raises(O.RuleError, match="unknown match keys"):
        O.validate([{"match": {"colour": "red"}, "class": "x"}])
    with pytest.raises(O.RuleError, match="text pattern"):
        O.validate([{"match": {"text": "("}, "class": "x"}])
    with pytest.raises(O.RuleError, match="no rules"):
        O.validate([])
    with pytest.raises(O.RuleError, match="line_weight_mm"):
        O.validate([{"match": {"line_weight_mm": "0.25"}, "class": "x"}])
    with pytest.raises(O.RuleError, match="area_m2"):
        O.validate([{"match": {"area_m2": 5}, "class": "x"}])
    with pytest.raises(O.RuleError, match="closed"):
        O.validate([{"match": {"closed": "yes"}, "class": "x"}])


def test_class_and_layer_rules_apply_independently():
    rules = O.class_map_rules({"A-WALL*": "Wand"}) + O.layer_map_rules({"EG": "OG"})
    p = O.plan([RECS[0]], rules, 1000.0)
    assert p["_moves"] == [{"uuids": ["w1"], "class": "Wand", "layer": "OG", "by_class": False}]
    assert [r["matched"] for r in p["rules"]] == [1, 1]


def test_plan_first_rule_wins_and_reports_the_rest():
    rules = O.class_map_rules({"a-wall*": "Wand-Tragend"}) + [
        {"match": {"kind": "dimension"}, "class": "Bemaßung"},
        {"match": {"kind": "text"}, "class": "Beschriftung"},
        {"match": {"in_group": True}, "class": "Möbel", "by_class": True, "layer": "OG"},
    ]
    p = O.plan(RECS, rules, 1000.0)
    moves = {(m["class"], m["layer"], m["by_class"]): sorted(m["uuids"]) for m in p["_moves"]}
    assert moves == {("Wand-Tragend", None, False): ["w1", "w2"], ("Bemaßung", None, False): ["d1"],
                     ("Beschriftung", None, False): ["t1"], ("Möbel", None, True): ["l1"]}
    assert p["unchanged"] == 1  # t2 is already in Beschriftung
    assert p["unmatched"] == 2 and p["to_change"] == 5
    assert p["layer_moves_skipped_in_groups"] == 1  # l1 can't move layers alone
    assert {g["kind"] for g in p["unmatched_groups"]} == {"group", "symbol"}
    assert [r["matched"] for r in p["rules"]] == [2, 1, 2, 1]


def test_plan_without_group_contents():
    p = O.plan(RECS, [{"match": {}, "class": "X"}], 1.0, include_groups=False)
    assert all("l1" not in m["uuids"] for m in p["_moves"])


def test_needs_geometry():
    assert O.needs_geometry([{"match": {"text": "x"}, "class": "a"}])
    assert O.needs_geometry([{"match": {"not": {"symbol": "x"}}, "class": "a"}])
    assert not O.needs_geometry([{"match": {"class": "x"}, "class": "a"}])


# ---------------------------------------------------------------- the tool


class FakeVW:
    def __init__(self):
        self.calls = []

    def __call__(self, fn, timeout=None, **kw):
        self.calls.append((fn, kw))
        if fn == "read_objects":
            return {"document": "Test.vwx", "units": {"mm_per_unit": 1000.0}, "objects": RECS,
                    "truncated": False, "none_class": "Keine"}
        if fn == "organize_apply":
            ops = [{"op": "attrs", "uuid": u, "class_before": "Keine", "layer_before": None}
                   for m in kw["moves"] for u in m["uuids"]]
            return {"document": "Test.vwx", "ops": ops, "changed": len(ops)}
        if fn == "current_document":
            return "Test.vwx"
        if fn == "revert2d":
            return {"document": "Test.vwx", "deleted": 0, "restored": len(kw["ops"]), "missing": [],
                    "mapping": {}}
        raise AssertionError(fn)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    f = FakeVW()
    monkeypatch.setattr(app, "vw2d", f)
    monkeypatch.setattr(app, "vw", f)
    monkeypatch.setattr(generic, "vw", f)
    monkeypatch.setattr(app, "get_journal", lambda: Journal(tmp_path / "j.jsonl"))
    return f


def test_dry_run_changes_nothing(fake):
    out = T.vw2d_organize(class_map={"A-WALL*": "Wand-Tragend"})
    assert out["dry_run"] and out["to_change"] == 2 and out["targets"] == {"class Wand-Tragend": 2}
    assert "dry_run=false" in out["next"]
    assert [c[0] for c in fake.calls] == ["read_objects"]
    assert fake.calls[0][1]["geometry"] is False and fake.calls[0][1]["layer"] == "*"


def test_apply_is_one_journaled_step(fake):
    out = T.vw2d_organize(rules=[{"match": {"text": "m²"}, "class": "Raumstempel"}], dry_run=False)
    assert out["changed"] == 1
    assert fake.calls[0][1]["geometry"] is True  # text needs the content
    fn, kw = fake.calls[-1]
    assert fn == "organize_apply" and kw["moves"] == [{"uuids": ["t1"], "class": "Raumstempel", "layer": None,
                                                       "by_class": False}]
    generic.vw_revert_last()
    fn, kw = fake.calls[-1]
    assert fn == "revert2d" and kw["ops"][0]["uuid"] == "t1"


def test_class_scope_leaves_groups_of_other_classes_alone(fake):
    # the read returns group g1 (class "Keine") and its part l1 (class "0") as context
    out = T.vw2d_organize(rules=[{"match": {}, "class": "Import"}], class_name="A-WALL")
    assert out["objects"] == 1 and out["to_change"] == 1 and out["rules"][0]["sample"] == ["w1"]


def test_bad_rules_are_tool_errors(fake):
    with pytest.raises(ToolError, match="no target"):
        T.vw2d_organize(rules=[{"match": {}}])


# ---------------------------------------------------------------- inside Vectorworks


class H:
    def __init__(self, uid, cls, layer, parent=None, t=2):
        self.uid, self.cls, self.layer_name, self.parent_group, self.t = uid, cls, layer, parent, t


class LayeredVS:
    """Layers as back-to-front lists, like Vectorworks."""

    def __init__(self, objs, fail_at=None):
        self.layers = {n: types.SimpleNamespace(name=n, t=31, objs=[]) for n in ("EG", "OG")}
        for o in objs.values():
            if o.parent_group is None:
                self.layers[o.layer_name].objs.append(o)
        self.objs, self.fail_at = objs, fail_at
        self.classes = ["Keine", "0"]

    def _list(self, h):
        return h.parent_group.kids if h.parent_group is not None else self.layers[h.layer_name].objs

    def GetObjectByUuid(self, u):
        return self.objs.get(u)

    def GetObjectUuid(self, h):
        return h.uid

    def ForEachObject(self, f, c):
        for o in list(self.objs.values()):
            f(o)

    def GetClass(self, h):
        return h.cls

    def SetClass(self, h, c):
        if self.fail_at is not None and h.uid == self.fail_at:
            raise RuntimeError("boom")
        h.cls = c

    def GetTypeN(self, h):
        return h.t

    def GetLayer(self, h):
        return self.layers[h.layer_name]

    def GetLName(self, lay):
        return lay.name

    def GetParent(self, h):
        return h.parent_group if h.parent_group is not None else self.layers[h.layer_name]

    def GetLayerByName(self, n):
        return self.layers.get(n)

    def SetParent(self, h, lay):
        self._list(h).remove(h)
        h.layer_name = lay.name
        lay.objs.append(h)  # lands in front

    def NextObj(self, h):
        lst = self._list(h)
        i = lst.index(h)
        return lst[i + 1] if i + 1 < len(lst) else None

    def PrevObj(self, h):
        lst = self._list(h)
        i = lst.index(h)
        return lst[i - 1] if i > 0 else None

    def FInGroup(self, g):
        return g.kids[0] if getattr(g, "kids", None) else None

    def NumObj(self, lay):
        return len(lay.objs)

    def HMoveBackward(self, h, to_back):
        lst = self._list(h)
        i = lst.index(h)
        lst.pop(i)
        lst.insert(0 if to_back else max(0, i - 1), h)

    def HMoveForward(self, h, to_front):
        lst = self._list(h)
        i = lst.index(h)
        lst.pop(i)
        lst.insert(len(lst) if to_front else min(len(lst), i + 1), h)

    def ClassNum(self):
        return len(self.classes)

    def ClassList(self, i):
        return self.classes[i - 1]

    def GetFName(self):
        return "Test.vwx"


def _vw(objs, fail_at=None):
    vs = LayeredVS(objs, fail_at)
    ns = {"vs": vs}
    exec(calls.VWLIB_SOURCE, ns)  # noqa: S102
    exec(calls.VWLIB2D_SOURCE, ns)  # noqa: S102
    ns["ensure_class"] = lambda n, a=None: vs.classes.append(n) if n not in vs.classes else None
    ns["ensure_layer"] = lambda n, scale=None: None
    return ns, vs


def test_vw_organize_apply_moves_and_journals():
    objs = {"a": H("a", "Keine", "EG"), "b": H("b", "0", "EG"), "c": H("c", "0", "EG")}
    ns, vs = _vw(objs)
    out = ns["organize_apply"]([{"uuids": ["a", "b"], "class": "Wand", "layer": "OG", "by_class": False}])
    assert out["changed"] == 2 and objs["a"].cls == "Wand" and objs["b"].layer_name == "OG"
    assert out["ops"][1] == {"op": "attrs", "uuid": "b", "class_before": "0", "layer_before": "EG",
                             "above": "c", "z": 1}
    assert "Wand" in vs.classes


def test_vw_organize_layer_move_revert_restores_stacking():
    names = "abcdefgh"
    objs = {n: H(n, "Keine", "EG") for n in names}
    ns, vs = _vw(objs)
    moved = ["b", "c", "d", "f", "h"]
    out = ns["organize_apply"]([{"uuids": moved, "class": None, "layer": "OG", "by_class": False}])
    assert "".join(o.uid for o in vs.layers["EG"].objs) == "aeg"
    ns["revert2d"](out["ops"])
    assert "".join(o.uid for o in vs.layers["EG"].objs) == names
    assert all(objs[n].layer_name == "EG" for n in names)


def test_vw_organize_apply_undoes_everything_on_error():
    objs = {"a": H("a", "Keine", "EG"), "b": H("b", "0", "EG")}
    ns, vs = _vw(objs, fail_at="b")
    with pytest.raises(RuntimeError, match="boom"):
        ns["organize_apply"]([{"uuids": ["a", "b"], "class": "Wand", "layer": None, "by_class": False}])
    assert objs["a"].cls == "Keine"  # reverted


def test_vw_organize_apply_refuses_layer_moves_inside_groups():
    g = H("g", "Keine", "EG", t=11)
    k = H("k", "Keine", "EG", parent=g)
    g.kids = [k]
    objs = {"a": H("a", "Keine", "EG"), "g": g, "k": k}
    ns, vs = _vw(objs)
    with pytest.raises(ValueError, match="inside a group"):
        ns["organize_apply"]([{"uuids": ["a", "k"], "class": None, "layer": "OG", "by_class": False}])
    assert objs["a"].layer_name == "EG"  # the first move was undone
    assert [o.uid for o in vs.layers["EG"].objs] == ["a", "g"]  # and back in its place


def test_vw_organize_big_layer_move_revert_restores_stacking_in_bulk():
    names = ["o%02d" % i for i in range(60)]
    objs = {n: H(n, "Keine", "EG") for n in names}
    ns, vs = _vw(objs)
    moved = names[3:50:1]  # 47 objects: the bulk path
    out = ns["organize_apply"]([{"uuids": moved, "class": "X", "layer": "OG", "by_class": False}])
    assert [o.uid for o in vs.layers["OG"].objs] == moved
    ns["revert2d"](out["ops"])
    assert [o.uid for o in vs.layers["EG"].objs] == names
    assert all(objs[n].cls == "Keine" for n in names)


def test_vw_revert_of_attribute_ops_with_raw_attributes():
    objs = {"a": H("a", "X", "EG")}
    ns, vs = _vw(objs)
    seen = []
    ns["restore_raw_attrs"] = lambda h, r: seen.append((h.uid, r))
    out = ns["revert2d"]([{"op": "attrs", "uuid": "a", "class_before": "Keine", "layer_before": None,
                           "before": {"pen_fore": [0, 0, 0], "lw": 7}}])
    assert out["restored"] == 1 and objs["a"].cls == "Keine" and seen == [("a", {"pen_fore": [0, 0, 0], "lw": 7})]
