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
