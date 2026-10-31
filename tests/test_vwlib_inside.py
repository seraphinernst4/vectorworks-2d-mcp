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
