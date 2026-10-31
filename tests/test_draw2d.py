"""2D tools end to end against a small in-memory stand-in for vwlib2d."""

import copy
import json
import uuid as _uuid

import pytest
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError

from vwmcp import app
from vwmcp.journal import Journal
from vwmcp.tools import draw2d, generic

UNITS = {"name": "m", "area_name": "m²", "upi": 0.0254, "mm_per_unit": 25.4 / 0.0254}
BC = {"fill": True, "fill_color": True, "pen_color": True, "line_weight": True, "line_style": True}


class FakeVW2D:
    def __init__(self):
        self.objs: dict[str, dict] = {}
        self.calls = []

    def rec(self, it):
        u = str(_uuid.uuid4())
        k = it["kind"]
        r = {"uuid": u, "class": it.get("class", "Keine"), "layer": it.get("layer") or "EG", "name": "",
             "attrs": {"fill": "solid", "pen": "#000000", "fill_color": "#808080", "by_class": dict(BC)}}
        if k == "poly":
            r.update(kind="polygon", vertices=copy.deepcopy(it["verts"]), closed=it["closed"],
                     holes=copy.deepcopy(it.get("holes") or []))
        elif k == "line":
            r.update(kind="line", p1=list(it["p1"]), p2=list(it["p2"]))
        else:
            r.update(kind=k)
        pts = [v[:2] for v in r.get("vertices", [])] or [r.get("p1", [0, 0]), r.get("p2", [0, 0])]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        r["bbox"] = [min(xs), min(ys), max(xs), max(ys)]
        return r

    def __call__(self, fn, timeout=None, **kw):
        self.calls.append((fn, kw))
        if fn == "units":
            return UNITS
        if fn == "read_objects":
            objs = list(self.objs.values())
            if kw.get("uuids"):
                objs = [self.objs[u] for u in kw["uuids"]]
            elif kw.get("layer") not in (None, "*"):
                objs = [o for o in objs if o["layer"] == kw["layer"]]
            return {"document": "Test.vwx", "units": UNITS, "none_class": "Keine", "active_layer": "EG",
                    "objects": copy.deepcopy(objs), "truncated": False}
        if fn == "draw_items":
            out = []
            for it in kw["items"]:
                r = self.rec(it)
                self.objs[r["uuid"]] = r
                out.append(copy.deepcopy(r))
            return {"document": "Test.vwx", "objects": out}
        if fn == "replace_geometry":
            snaps, after, out = [], [], []
            for ch in kw["changes"]:
                old = self.objs[ch["uuid"]]
                snaps.append({"rec": copy.deepcopy(old), "z": 0})
                old["vertices"] = copy.deepcopy(ch["verts"])
                old["closed"] = ch["closed"]
                after.append(old["uuid"])
                out.append(copy.deepcopy(old))
            return {"document": "Test.vwx", "objects": out,
                    "ops": [{"op": "replaced", "before": snaps, "after": after}]}
        if fn == "revert2d":
            return {"document": "Test.vwx", "deleted": 0, "restored": 0, "missing": [], "mapping": {}}
        if fn == "current_document":
            return "Test.vwx"
        raise AssertionError(fn)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    f = FakeVW2D()
    monkeypatch.setattr(app, "vw2d", f)
    monkeypatch.setattr(app, "vw", f)
    monkeypatch.setattr(generic, "vw", f)
    monkeypatch.setattr(app, "get_journal", lambda: Journal(tmp_path / "j.jsonl"))
    return f


def _wall(x0, x1, cls="Wand"):
    return {"type": "polygon", "points": [[x0, 0], [x1, 0], [x1, 0.3], [x0, 0.3]], "class": cls}


def test_draw_requires_a_class(fake):
    with pytest.raises(ToolError, match="no class"):
        draw2d.vw2d_draw([{"type": "line", "p1": [0, 0], "p2": [1, 0]}])


def test_draw_reports_exactness_and_new_issues_and_journals(fake):
    out = draw2d.vw2d_draw([_wall(0, 4), _wall(4.0003, 8)], layer="EG")
    assert out["all_exact"] and out["max_deviation_mm"] == 0.0
    assert any(i["kind"] == "gap" for i in out["issues"]) and not out["clean"]
    entry = app.get_journal().peek("Test.vwx")
    assert entry["tool"] == "vw2d_draw" and [o["op"] for o in entry["ops"]] == ["created", "created"]


def test_mm_unit_draw(fake):
    out = draw2d.vw2d_draw([{"type": "polygon", "points": [[0, 0], [3650, 0], [3650, 240]], "class": "W"}],
                           unit="mm", layer="EG", check=False)
    (o,) = fake.objs.values()
    assert o["vertices"][1][:2] == [3.65, 0.0] and out["all_exact"]


def test_check_returns_report_and_image(fake):
    draw2d.vw2d_draw([_wall(0, 4), _wall(3.99, 8)], layer="EG", check=False)
    text, img = draw2d.vw2d_check(layer="EG")
    rep = json.loads(text)
    assert rep["summary"]["errors"] >= 1 and isinstance(img, Image)
    assert img.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_check_reads_large_scopes_and_says_when_it_could_not_read_all(fake, monkeypatch):
    draw2d.vw2d_draw([_wall(0, 4)], layer="EG", check=False)
    orig = fake.__call__

    def truncated(fn, timeout=None, **kw):
        res = orig(fn, timeout=timeout, **kw)
        if fn == "read_objects":
            assert kw["limit"] == draw2d.CHECK_LIMIT
            res["truncated"] = True
        return res

    monkeypatch.setattr(app, "vw2d", truncated)
    rep = json.loads(draw2d.vw2d_check(layer="EG", image=False))
    assert "only the first" in rep["summary"]["warning"]


def test_fix_snap_makes_drawing_clean_and_is_revertable(fake):
    draw2d.vw2d_draw([_wall(0, 4), _wall(4.0003, 8)], layer="EG", check=False)
    text = draw2d.vw2d_fix("snap", layer="EG", image=False)
    rep = json.loads(text)
    assert rep["applied"]["moved"] == 2 and rep["after"]["errors"] == 0
    assert app.get_journal().peek("Test.vwx")["tool"] == "vw2d_fix"
    generic.vw_revert_last()
    fn, kw = fake.calls[-1]
    assert fn == "revert2d" and kw["ops"][0]["op"] == "replaced"


def test_fix_with_uuids_reports_only_on_those_objects(fake):
    mine = draw2d.vw2d_draw([_wall(0, 4), _wall(4.0003, 8)], layer="EG", check=False)
    draw2d.vw2d_draw([_wall(20, 24), _wall(24.0004, 28)], layer="EG", check=False)  # someone else's gap
    uuids = [o["uuid"] for o in mine["created"]]
    rep = json.loads(draw2d.vw2d_fix("snap", uuids=uuids, image=False))
    assert rep["applied"]["moved"] == 2
    assert rep["before"]["errors"] == 1 and rep["after"]["errors"] == 0 and rep["after"]["clean"]
    assert rep["remaining"] == [] and rep["after"]["scope_objects"] == 2
    # the other pair was not touched
    other = [o for o in fake.objs.values() if o["uuid"] not in uuids]
    assert sorted(v[0] for o in other for v in o["vertices"])[:4] == [20, 20, 24, 24]


def test_preview_focus_issue(fake):
    draw2d.vw2d_draw([_wall(0, 4), _wall(4.0003, 8)], layer="EG", check=False)
    text, img = draw2d.vw2d_preview(layer="EG", focus_issue=1)
    assert isinstance(img, Image) and json.loads(text)["summary"]["errors"] == 1
