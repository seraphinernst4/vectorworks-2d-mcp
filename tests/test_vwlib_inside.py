"""vwlib / vwlib2d helpers that run inside Vectorworks, against a fake `vs`."""

import types

from vwmcp import calls


def _ns(**fns):
    vs = types.SimpleNamespace(**fns)
    ns = {"vs": vs}
    exec(calls.VWLIB_SOURCE, ns)  # noqa: S102
    exec(calls.VWLIB2D_SOURCE, ns)  # noqa: S102
    return ns


def test_arc_radius_removes_subtraction_noise_only():
    ns = _ns(HWidth=lambda h: 1.2)
    ns["_JOB_CACHE"]["mmpu"] = 1000.0  # document in metres
    r = ns["_arc_radius"]
    # 20.6 - 20.0 = 0.6000000000000014: noise -> exactly 0.6
    assert r(None, (20.0, 5.0), (20.6, 5.0), 90.0) == 0.6
    # full circle: half the stored width
    assert r(None, (0.0, 0.0), (0.6, 0.0), 360.0) == 0.6
    # a real radius with more than picometre detail stays as it is
    third = 1.0 / 3.0
    assert r(None, (0.0, 0.0), (third, 0.0), 45.0) == third
    ns["_JOB_CACHE"]["mmpu"] = 1.0  # document in mm
    assert r(None, (0.0, 0.0), (1000.0 / 3.0, 0.0), 45.0) == 1000.0 / 3.0


def test_named_and_children():
    class H:
        def __init__(self, t, uid, parent=None):
            self.t, self.uid, self.parent = t, uid, parent

    grp = H(11, "g")
    a, b = H(2, "a", grp), H(2, "b", grp)
    outside = H(2, "x", None)
    nxt = {a: b, b: outside}
    objs = {"Keine": H(94, "c"), "Sym": H(16, "s"), "Dummy": H(0, "d")}
    ns = _ns(GetObject=lambda n: objs.get(n), GetTypeN=lambda h: h.t, FInGroup=lambda h: a if h is grp else None,
             NextObj=lambda h: nxt.get(h), GetParent=lambda h: h.parent, GetObjectUuid=lambda h: h.uid)
    assert ns["named"]("Keine", (16,)) is None and ns["named"]("Keine", (94,)) is objs["Keine"]
    assert ns["named"]("Sym", (16,)) is objs["Sym"]
    assert ns["named"]("Dummy") is None and ns["named"]("Nope") is None
    # children stop at an object whose parent is not the group
    assert ns["children"](grp) == [a, b]


def _layer_list(n):
    """n objects in one layer list: handles with prev/next links."""
    class H:
        def __init__(self, i):
            self.i, self.uid, self.t = i, "u%d" % i, 2

    layer = H(-1)
    layer.t = 31
    hs = [H(i) for i in range(n)]
    nxt = {hs[i]: hs[i + 1] for i in range(n - 1)}
    prv = {hs[i + 1]: hs[i] for i in range(n - 1)}
    return layer, hs, nxt, prv


def test_bulk_lookup_and_stacking_map_match_the_slow_versions():
    layer, hs, nxt, prv = _layer_list(300)
    lookups = []

    def by_uuid(u):
        lookups.append(u)
        return next((h for h in hs if h.uid == u), None)

    ns = _ns(GetObjectUuid=lambda h: h.uid, GetObjectByUuid=by_uuid,
             ForEachObject=lambda f, c: [f(h) for h in hs], GetParent=lambda h: layer,
             GetTypeN=lambda h: h.t, FInGroup=lambda h: None, NextObj=lambda h: nxt.get(h),
             PrevObj=lambda h: prv.get(h))
    want = ["u5", "u250", "u17"] + ["u%d" % i for i in range(100, 170)]
    got = ns["handles"](want)
    assert [h.uid for h in got] == want and lookups == []  # one walk, no per-uuid search
    few = ns["handles"](["u3"])
    assert few[0].uid == "u3" and lookups == ["u3"]
    zm = ns["_z_map"](got)
    assert all(zm[str(h)] == ns["_z_index"](h) == h.i for h in got)


class StackVS:
    """A layer list like Vectorworks': first object = back, new objects at
    the end (front); HMoveBackward moves one step towards the first."""

    def __init__(self, names):
        self.layer = types.SimpleNamespace(name="L", t=31)
        self.objs = [types.SimpleNamespace(name=n, t=2, uid="u" + n) for n in names]

    def order(self):
        return "".join(o.name for o in self.objs)

    def NextObj(self, h):
        i = self.objs.index(h)
        return self.objs[i + 1] if i + 1 < len(self.objs) else None

    def PrevObj(self, h):
        i = self.objs.index(h)
        return self.objs[i - 1] if i > 0 else None

    def HMoveBackward(self, h, to_back):
        i = self.objs.index(h)
        self.objs.pop(i)
        self.objs.insert(0 if to_back else max(0, i - 1), h)

    def HMoveForward(self, h, to_front):
        i = self.objs.index(h)
        self.objs.pop(i)
        self.objs.insert(len(self.objs) if to_front else min(len(self.objs), i + 1), h)

    def GetParent(self, h):
        return self.layer

    def GetTypeN(self, h):
        return h.t

    def NumObj(self, lay):
        return len(self.objs)

    def GetObjectByUuid(self, u):
        return next((o for o in self.objs if o.uid == u), None)


def test_place_below_restores_the_stacking_position_from_either_end():
    for names, gone_idx in (("ABCDEFGHIJ", 1), ("ABCDEFGHIJ", 8), ("ABCDEFGHIJ", 5), ("AB", 0)):
        st = StackVS(names)
        ns = _ns()
        ns["vs"].__dict__.update({k: getattr(st, k) for k in dir(st) if k[0].isupper()})
        gone = st.objs[gone_idx]
        z = gone_idx
        above = st.objs[gone_idx + 1].uid if gone_idx + 1 < len(st.objs) else None
        st.objs.remove(gone)
        st.objs.append(gone)  # restored objects land in front
        ns["_place_below"](gone, above, z)
        assert st.order() == names, (names, gone_idx, st.order())
        # without the old index: stepping back from the front
        st.objs.remove(gone)
        st.objs.append(gone)
        ns["_place_below"](gone, above, None)
        assert st.order() == names



def test_read_objects_says_when_it_stopped_early():
    class O:
        def __init__(self, i):
            self.i, self.t = i, 2

    objs = [O(i) for i in range(12)]
    ns = _ns(GetFName=lambda: "T.vwx", GetUnits=lambda: (0, 0, 0, 25.4, "mm", "m2"), ClassList=lambda i: "Keine",
             GetLName=lambda h: "EG", ActLayer=lambda: None, GetTypeN=lambda h: h.t)
    ns["collect"] = lambda *a: objs
    ns["read_obj"] = lambda h, *a, **k: {"uuid": str(h.i), "class": "Keine"}
    out = ns["read_objects"](layer="EG", limit=5)
    assert len(out["objects"]) == 5 and out["truncated"] is True
    out = ns["read_objects"](layer="EG", limit=12)
    assert len(out["objects"]) == 12 and out["truncated"] is False


def _restack_case(names, gone, chain_rename=False):
    """Remove `gone` from the list, put them back in front (as a revert does),
    restack, and return the final order."""
    st = StackVS(names)
    ns = _ns()
    ns["vs"].__dict__.update({k: getattr(st, k) for k in dir(st) if k[0].isupper()})
    snaps = []
    for i, o in enumerate(st.objs):
        if o.name in gone:
            above = st.objs[i + 1].uid if i + 1 < len(st.objs) else None
            snaps.append((o, above, i))
    for o, _a, _z in snaps:
        st.objs.remove(o)
    mapping, restored = {}, {}
    entries = []
    for o, above, z in sorted(snaps, key=lambda e: -e[2]):  # revert restores frontmost first
        if chain_rename:  # restored objects get new uuids
            mapping[o.uid] = o.uid + "'"
            o.uid = o.uid + "'"
            restored[o.uid] = o
        st.objs.append(o)
        entries.append((o, above, z))
    moves = []
    orig = st.HMoveForward
    ns["vs"].HMoveForward = lambda h, f: (moves.append(h.name), orig(h, f))
    ns["_restack"](entries, {}, mapping, restored)
    return st.order(), moves


def test_restack_puts_many_objects_back_in_one_pass():
    names = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for gone in ("BDFHJ", "ABC", "XYZ", "MNOP", "AZ", "Z", "A", "BCDEFGHIJKLMNOPQRSTUVWXY"):
        order, moves = _restack_case(names, set(gone))
        assert order == names, (gone, order)
        order, moves = _restack_case(names, set(gone), chain_rename=True)
        assert order == names, (gone, order)


def test_restack_moves_only_from_the_first_change_on():
    order, moves = _restack_case("ABCDEFGHIJ", {"H"})
    assert order == "ABCDEFGHIJ" and moves == ["H", "I", "J"]


def test_restack_handles_entries_in_any_order():
    """Entries back-to-front (not the usual front-first order) give the same result."""
    names = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for gone in ("BDFHJ", "ABC", "XYZ", "BCDEFGHIJKLMNOPQRSTUVWXY"):
        st = StackVS(names)
        ns = _ns()
        ns["vs"].__dict__.update({k: getattr(st, k) for k in dir(st) if k[0].isupper()})
        entries = []
        for i, o in enumerate(st.objs):
            if o.name in gone:
                entries.append((o, st.objs[i + 1].uid if i + 1 < len(st.objs) else None, i))
        for o, _a, _z in entries:
            st.objs.remove(o)
            st.objs.append(o)
        ns["_restack"](entries, {})  # ascending z
        assert st.order() == names, gone


class FastStackVS(StackVS):
    """StackVS with O(1) neighbours, for long lists."""

    def _pos(self):
        return {id(o): i for i, o in enumerate(self.objs)}

    def NextObj(self, h):
        i = self._p[id(h)] if getattr(self, "_p", None) else self._pos()[id(h)]
        return self.objs[i + 1] if i + 1 < len(self.objs) else None

    def PrevObj(self, h):
        i = self._p[id(h)] if getattr(self, "_p", None) else self._pos()[id(h)]
        return self.objs[i - 1] if i > 0 else None

    def HMoveForward(self, h, to_front):
        super().HMoveForward(h, to_front)
        self._p = self._pos()

    def HMoveBackward(self, h, to_back):
        super().HMoveBackward(h, to_back)
        self._p = self._pos()

    def freeze(self):
        self._p = self._pos()


def test_restack_of_a_chain_longer_than_the_recursion_limit():
    # 3000 neighbours that were all rebuilt: each one's `above` is the next one
    import sys

    names = ["o%04d" % i for i in range(3000)]
    assert len(names) > sys.getrecursionlimit()
    st = FastStackVS(names)
    ns = _ns()
    ns["vs"].__dict__.update({k: getattr(st, k) for k in dir(st) if k[0].isupper()})
    entries = [(o, st.objs[i + 1].uid if i + 1 < len(st.objs) else None, i)
               for i, o in enumerate(st.objs) if i > 0]
    first = st.objs[0]
    st.objs[:] = [first] + [o for o, _a, _z in reversed(entries)]  # restored, in reverse order
    st.freeze()
    ns["_restack"](sorted(entries, key=lambda e: -e[2]), {})
    assert [o.name for o in st.objs] == names


def test_outermost_drops_duplicates_and_parts_of_listed_groups():
    N = types.SimpleNamespace  # str(handle) is unique in Vectorworks: give each a name
    lay = N(n="lay", t=31)
    g = N(n="g", t=11, p=lay)
    inner = N(n="inner", t=11, p=g)
    k1 = N(n="k1", t=2, p=g)
    k2 = N(n="k2", t=2, p=inner)  # two levels down
    a = N(n="a", t=2, p=lay)
    lone = N(n="lone", t=2, p=N(n="g2", t=11, p=lay))  # its group isn't listed
    ns = _ns(GetParent=lambda h: h.p, GetTypeN=lambda h: h.t)
    assert ns["_outermost"]([a, k1, g, a, k2, lone, g]) == [a, g, lone]


def test_place_new_puts_rebuilt_neighbours_back_in_order():
    """replace_geometry: several neighbours rebuilt (new uuids, all in front);
    each goes behind its old front neighbour, which may be rebuilt too."""
    for n_total, rebuilt in ((10, (1, 2, 3, 7)), (60, tuple(range(5, 45)))):  # one by one / in bulk
        names = ["o%02d" % i for i in range(n_total)]
        st = FastStackVS(names)
        ns = _ns()
        ns["vs"].__dict__.update({k: getattr(st, k) for k in dir(st) if k[0].isupper()})
        olds = {i: (st.objs[i], st.objs[i + 1].uid if i + 1 < n_total else None) for i in rebuilt}
        mapping, new_by_uuid, entries = {}, {}, []
        for i in rebuilt:
            o, above = olds[i]
            st.objs.remove(o)
            new = types.SimpleNamespace(name=o.name, t=2, uid=o.uid + "'")
            st.objs.append(new)  # rebuilt objects land in front
            mapping[o.uid], new_by_uuid[new.uid] = new.uid, new
            entries.append((new, above, i))
        st.freeze()
        ns["_place_new"](entries, {}, mapping, new_by_uuid)
        assert [o.name for o in st.objs] == names, n_total


def test_bbox_is_plan_coordinates_in_any_view():
    """vs.GetBBox is the projection on the screen plane: in the front view
    ("Vorne") it collapses to y = 0 (live, 2026 U7). _bbox then uses the
    geometry."""
    import math

    objs = {
        "poly": types.SimpleNamespace(t=5, verts=[((0, 0), 0, 0), ((3, 0), 0, 0), ((3, 2), 0, 0), ((0, 2), 0, 0)]),
        "rect": types.SimpleNamespace(t=3, corners=[(1, 5), (4, 5), (4, 1), (1, 1)]),
        "arc": types.SimpleNamespace(t=6, c=(10, 10), p0=(12, 10), arc=(0.0, 90.0)),
    }
    view = [(-90.0, 0.0, 0.0, (0, 0, 0))]
    ns = _ns(GetTypeN=lambda h: h.t, GetBBox=lambda h: ((0.0, 0.0), (9.0, 0.0)), GetView=lambda: view[0],
             GetVertNum=lambda h: len(h.verts), GetPolylineVertex=lambda h, i: h.verts[i - 1],
             Get2DPt=lambda h, i: (h.corners[i - 1] if h.t == 3 else (h.c, h.p0)[i - 1]),
             GetArc=lambda h: h.arc)
    assert ns["_bbox"](objs["poly"]) == [0, 0, 3, 2]
    assert ns["_bbox"](objs["rect"]) == [1, 1, 4, 5]
    b = ns["_bbox"](objs["arc"])
    assert [round(v, 9) for v in b] == [10, 10, 12, 12] and math.isfinite(b[0])
    # from the top: Vectorworks' own box
    ns["_JOB_CACHE"].clear()
    view[0] = (0.0, 0.0, 0.0, (0, 0, 0))
    assert ns["_bbox"](objs["poly"]) == [0.0, 0.0, 9.0, 0.0]


class _PolyVS:
    """Enough of BeginPoly/EndPoly to see which calls make_poly makes. Like
    Vectorworks 2026 (live), ArcTo makes an arc vertex (3) and the radius
    vertex type (4) only comes from SetPolylineVertex."""

    def __init__(self):
        self.verts, self.calls, self.obj = [], [], None

    def fns(self):
        def add(t):
            return lambda p, r=0.0: (self.calls.append((t, p)), self.verts.append([p[0], p[1], t, r]))

        def end():
            self.obj = types.SimpleNamespace(verts=self.verts)
            self.verts = []

        def setv(h, i, p, t, r, recalc):
            self.calls.append(("set", i, t, r))
            h.verts[i - 1] = [p[0], p[1], t, r]

        return dict(ClosePoly=lambda: None, OpenPoly=lambda: None, BeginPoly=lambda: self.verts.clear(), EndPoly=end,
                    AddPoint=add(0), CurveTo=add(1), CurveThrough=add(2), ArcTo=add(3),
                    LNewObj=lambda: self.obj, GetVertNum=lambda h: len(h.verts),
                    GetPolylineVertex=lambda h, i: ((h.verts[i - 1][0], h.verts[i - 1][1]), h.verts[i - 1][2],
                                                    h.verts[i - 1][3]),
                    SetPolylineVertex=setv, ResetBBox=lambda h: self.calls.append(("reset",)))


def test_make_poly_exact_rebuilds_every_vertex_type():
    """Restoring an imported spline (Bezier vertices) must not raise - a
    revert that stops halfway leaves the drawing in neither state (live,
    0.7 beta)."""
    import pytest

    f = _PolyVS()
    ns = _ns(**f.fns())
    verts = [[0, 0, 0, 0], [1, 2, 1, 0], [3, 1, 2, 0], [4, 0, 3, 0.5], [5, 1, 4, 0.25], [6, 0, 0, 0]]
    with pytest.raises(ValueError, match="vertex type 1"):
        ns["make_poly"](verts, False)
    h = ns["make_poly"](verts, False, exact=True)
    assert [v[2] for v in h.verts] == [0, 1, 2, 3, 4, 0]
    assert h.verts[4][3] == 0.25 and h.verts[3][3] == 0.5
    assert ("set", 5, 4, 0.25) in f.calls and ("reset",) in f.calls
    # corners and arcs only: no fix-up pass
    f.calls.clear()
    ns["make_poly"]([[0, 0, 0, 0], [4, 0, 3, 0.5], [6, 0, 0, 0]], True, exact=True)
    assert not [c for c in f.calls if c[0] in ("set", "reset")]


def test_revert_refuses_before_changing_anything_when_a_snapshot_cant_be_rebuilt():
    import pytest

    deleted = []
    ns = _ns(DelObject=deleted.append, GetLName=lambda h: "L", ActLayer=lambda: None)
    bad = {"op": "replaced", "after": ["new-1"],
           "before": [{"z": 1, "rec": {"kind": "polyline", "uuid": "a", "layer": "L", "class": "None",
                                       "closed": False, "vertices": [[0, 0, 7, 0]]}},
                      {"z": 2, "rec": {"kind": "wall", "uuid": "b", "layer": "L", "class": "None"}}]}
    with pytest.raises(ValueError, match="nothing was changed: 2 object"):
        ns["_revert2d"]([bad])
    assert deleted == []
    ok = {"op": "replaced", "after": [], "before": [
        {"z": 1, "rec": {"kind": "polyline", "uuid": "a", "layer": "L", "class": "None", "closed": False,
                         "vertices": [[0, 0, 0, 0], [1, 1, 1, 0], [2, 0, 0, 0]]}},
        {"z": 2, "rec": {"kind": "group", "uuid": "g"}, "children": [{"z": 1, "rec": {"kind": "line", "uuid": "c"}}]}]}
    assert ns["_check_restorable"]([ok]) is None


def test_group_takes_members_class_not_the_active_one():
    objs = {u: types.SimpleNamespace(uid=u, cls=c, t=2) for u, c in (("a", "Vorhang"), ("b", "Vorhang"), ("c", "X"))}
    grp = types.SimpleNamespace(uid="g", cls="Aktiv", t=11)
    sel = []

    def run(uuids, **g):
        ns = _ns(GetObjectByUuid=lambda u: objs.get(u), GetLName=lambda h: "L", ActLayer=lambda: "L",
                 GetLayer=lambda h: "L", GetLayerByName=lambda n: "L", GetParent=lambda h: "L",
                 GetTypeN=lambda h: getattr(h, "t", 31), DSelectAll=sel.clear, SetSelect=sel.append, Group=lambda: None,
                 FSActLayer=lambda: grp, GetClass=lambda h: h.cls,
                 SetClass=lambda h, c: setattr(h, "cls", c), GetObjectUuid=lambda h: h.uid,
                 ClassList=lambda i: "Keine", ClassNum=lambda: 1, GetDescriptionText=lambda h: "")
        ns["read_obj"] = lambda h, geo: {"uuid": h.uid, "class": h.cls}
        ns["none_class"] = lambda: "Keine"
        ns["_layer_name"] = lambda h: "L"
        ns["current_document"] = lambda: "T.vwx"
        return ns["group_objects"]([dict(uuids=uuids, **g)])

    assert run(["a", "b"])["groups"][0]["class"] == "Vorhang"
    assert run(["a", "c"])["groups"][0]["class"] == "Keine"
    assert run(["a", "c"], **{"class": "Eigen"})["groups"][0]["class"] == "Eigen"


def _look_vs(objs, classes):
    """Fake vs for keep_looks: objects carry raw attributes, classes their own."""
    W, B = (65535, 65535, 65535), (0, 0, 0)

    def cl(key):
        return lambda c: classes[c][key]

    def setcl(key):
        return lambda c, v: classes[c].__setitem__(key, v)

    def ob(key):
        return lambda h: h.a[key]

    fns = dict(
        GetTypeN=lambda h: h.t, GetClass=lambda h: h.cls,
        GetVectorFill=lambda h: (False, ""), GetOpacityN=lambda h: (True, 100, 100),
        GetPenFore=ob("pen"), GetPenBack=lambda h: W, GetFillFore=ob("fill"), GetFillBack=ob("fill"),
        GetFPat=ob("fpat"), GetLSN=lambda h: 2, GetLW=ob("lw"),
        IsFPatByClass=lambda h: h.a.get("bc", False), IsFillColorByClass=lambda h: h.a.get("bc", False),
        IsPenColorByClass=lambda h: h.a.get("bc", False), IsLWByClass=lambda h: h.a.get("bc", False),
        IsLSByClass=lambda h: h.a.get("bc", False),
        GetClVectorFill=lambda c: (False, ""), GetClFPat=cl("fpat"), GetClFillFore=cl("fill"),
        GetClFillBack=cl("fill"), GetClPenFore=cl("pen"), GetClPenBack=lambda c: W, GetClLW=cl("lw"),
        GetClLSN=lambda c: 2,
        SetClPenFore=setcl("pen"), SetClPenBack=lambda c, v: None, SetClFillFore=setcl("fill"),
        SetClFillBack=setcl("fill"), SetClVectorFill=lambda c, v: True, SetClFPat=setcl("fpat"),
        SetClLW=setcl("lw"), SetClLSN=lambda c, v: None,
        ForEachObject=lambda fn, crit: [fn(o) for o in objs if "'%s'" % o.cls in crit],
    )
    return fns, W, B


def test_by_class_keeps_the_look_of_imported_classes():
    """VectorView outline polygons have no fill, their imported class says
    solid black: 'by class' would turn the section into black areas (live
    0.7 beta). The class takes the objects' look first."""
    W, B = (65535, 65535, 65535), (0, 0, 0)
    mk = lambda cls, t=5, **a: types.SimpleNamespace(cls=cls, t=t, a=dict(dict(pen=B, fill=B, fpat=0, lw=10), **a))
    outlines = [mk("VV_Faces") for _ in range(3)] + [mk("VV_Faces", t=2, fpat=1)]  # a line: its fill doesn't count
    fills = [mk("VV_Fill", fpat=1, fill=W, pen=W, lw=0) for _ in range(2)]
    mixed = [mk("Mix", lw=10), mk("Mix", lw=20)]
    shared_cls = [mk("Shared", fpat=0)]
    follower = mk("Shared", bc=True)  # an object elsewhere that already follows the class
    classes = {c: {"fpat": 2, "fill": B, "pen": B, "lw": 10} for c in ("VV_Faces", "VV_Fill", "Mix", "Shared")}
    fns, _W, _B = _look_vs(outlines + fills + mixed + shared_cls + [follower], classes)
    ns = _ns(**fns)
    ops, rep = ns["keep_looks"](outlines + fills + mixed + shared_cls)
    assert sorted(rep["adopted"]) == ["VV_Faces", "VV_Fill"] and rep["mixed"] == ["Mix"] and rep["shared"] == ["Shared"]
    assert classes["VV_Faces"]["fpat"] == 0  # outlines stay unfilled
    assert classes["VV_Fill"] == {"fpat": 1, "fill": W, "pen": W, "lw": 0}
    assert classes["Mix"]["lw"] == 10 and classes["Shared"]["fpat"] == 2  # untouched
    # revert puts the class back
    before = [o for o in ops if o["class"] == "VV_Fill"][0]["before"]
    assert before["fpat"] == 2 and before["fill_fore"] == list(B)
