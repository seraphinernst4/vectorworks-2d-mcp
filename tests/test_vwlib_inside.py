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
