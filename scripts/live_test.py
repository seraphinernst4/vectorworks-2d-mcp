#!/usr/bin/env python3
"""Live test of Keystone against a running Vectorworks (macOS).

    python scripts/live_test.py [--src <repo>/src] [--only protocol,tools,pitfalls,regression]

Uses the real path: job folder -> AppleScript -> "MCP Run Job" -> bridge.
Sends one request at a time; don't work in Vectorworks while it runs
(about 2 minutes) and keep an empty, unsaved test document in front.
Writes ~/.vwmcp/live_report.json and logs to ~/.vwmcp/live_test.log.

Protocol tests:
  ping x10        round-trip times (claim / total / inside Vectorworks)
  libs            helper libraries by reference: first vs. cached call
  busy_cancel     Vectorworks busy (like an open dialog) -> the second request
                  is cancelled after the claim timeout and must never run later
  long_job        a job running longer than the claim timeout is waited for
  arg_check       vs.Rect(0, 0, 1) -> clean error, nothing drawn, no dialog
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(os.environ.get("VWMCP_HOME", Path.home() / ".vwmcp"))
LOG = ROOT / "live_test.log"
REPORT = ROOT / "live_report.json"


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def wait_idle(limit: float = 30.0) -> None:
    """Wait until no job is queued or running (e.g. the job that started us)."""
    end = time.time() + limit
    while time.time() < end:
        busy = [p for p in (ROOT / "jobs").glob("*") if p.suffix in (".json", ".working", ".running")]
        if not busy:
            return
        time.sleep(0.5)


def count_objects(b) -> int:
    code = "_n = []\ndef _c(h):\n    _n.append(1)\nvs.ForEachObject(_c, '(ALL)')\nlen(_n)"
    return b.run(code)["value"]


def protocol_tests(report: dict) -> None:
    from vwmcp import calls
    from vwmcp.client import Bridge, BridgeTimeout

    b = Bridge(root=ROOT)
    # ping x10
    times = []
    for _ in range(10):
        res = b.run("vs.GetFName()")
        assert res["ok"], res
        times.append(dict(b.last_timing))
    report["ping"] = {"bridge_version": b.bridge_version, "runs": times,
                      "median_total_s": sorted(t["total_s"] for t in times)[5]}
    log(f"ping: median {report['ping']['median_total_s']} s, bridge {b.bridge_version}")

    # libs: first call compiles, later calls use the cache
    lt = []
    for _ in range(3):
        st = calls.call2d(b, "structure")
        lt.append(dict(b.last_timing))
    report["libs"] = {"runs": lt, "document": st.get("document")}
    log(f"libs: {[t['vw_s'] for t in lt]} s inside Vectorworks")

    # arg check through the real menu command
    before = count_objects(b)
    res = b.run("vs.Rect(0, 0, 1)")
    after = count_objects(b)
    report["arg_check"] = {"ok": res["ok"], "error": res["error"], "objects_before": before,
                           "objects_after": after,
                           "pass": (not res["ok"]) and "VSArgumentError" in (res["error"] or "") and before == after}
    log(f"arg_check: pass={report['arg_check']['pass']}")

    # busy: A keeps Vectorworks busy 25 s; B must be cancelled and never run
    marker = ROOT / "live_marker_B.txt"
    marker.unlink(missing_ok=True)
    out = {}

    def run_a():
        try:
            out["a"] = Bridge(root=ROOT).run("import time\ntime.sleep(25)\n'A done'", timeout=120)["value"]
        except Exception as e:  # noqa: BLE001
            out["a"] = repr(e)

    ta = threading.Thread(target=run_a)
    ta.start()
    time.sleep(3)
    bb = Bridge(root=ROOT, claim_timeout=10)
    t0 = time.monotonic()
    try:
        bb.run(f"open({str(marker)!r}, 'w').write('B ran')")
        out["b"] = "RAN (unexpected)"
    except BridgeTimeout as e:
        out["b"] = f"cancelled after {time.monotonic() - t0:.1f}s: {e}"
    ta.join()
    time.sleep(10)  # the queued Apple Event for B runs now: it must find nothing
    cancelled = sorted(p.name for p in (ROOT / "jobs").glob("*.cancelled"))
    report["busy_cancel"] = {"a": out.get("a"), "b": out.get("b"), "marker_exists": marker.exists(),
                             "cancelled_files": cancelled[-3:],
                             "pass": out.get("a") == "A done" and out["b"].startswith("cancelled")
                             and not marker.exists()}
    log(f"busy_cancel: pass={report['busy_cancel']['pass']} ({out.get('b', '')[:60]})")

    # long job: claimed at once, runs longer than the claim timeout
    t0 = time.monotonic()
    try:
        v = Bridge(root=ROOT, claim_timeout=10).run("import time\ntime.sleep(18)\n'long done'")["value"]
    except Exception as e:  # noqa: BLE001
        v = repr(e)
    report["long_job"] = {"value": v, "seconds": round(time.monotonic() - t0, 1), "pass": v == "long done"}
    log(f"long_job: pass={report['long_job']['pass']} in {report['long_job']['seconds']} s")


PITFALL_CODE = r"""
import math
out = {}
prev_layer = vs.GetLName(vs.ActLayer())
vs.Layer("KS-Fallstricke")
made = []
def info(h):
    t = vs.GetTypeN(h)
    d = {"type": t}
    if t == 6:
        d["center"] = list(vs.Get2DPt(h, 1)); d["start_pt"] = list(vs.Get2DPt(h, 2))
        d["arc"] = list(vs.GetArc(h))
    d["hcenter"] = list(vs.HCenter(h)); d["w"] = vs.HWidth(h); d["h"] = vs.HHeight(h)
    return d
# circle: ArcByCenter vs Oval (center 10, 1.5 m, radius 0.6 m)
vs.ArcByCenter(10.0, 1.5, 0.6, 0.0, 360.0); h = vs.LNewObj(); made.append(h); out["circle_arcbycenter"] = info(h)
vs.Oval((9.4, 2.1), (10.6, 0.9)); h = vs.LNewObj(); made.append(h); out["circle_oval"] = info(h)
vs.Oval((9400 / 1000.0, 2100 / 1000.0), (10600 / 1000.0, 900 / 1000.0)); h = vs.LNewObj(); made.append(h); out["circle_oval_mm"] = info(h)
# arc: ArcByCenter vs Arc(bbox, start, sweep)
vs.ArcByCenter(16.0, 1.0, 0.8, 0.0, 90.0); h = vs.LNewObj(); made.append(h); out["arc_arcbycenter"] = info(h)
vs.Arc((15.2, 1.8), (16.8, 0.2), 0.0, 90.0); h = vs.LNewObj(); made.append(h); out["arc_arc_bbox"] = info(h)
vs.Arc((15.2, 1.8), (16.8, 0.2), 30.0, 90.0); h = vs.LNewObj(); made.append(h); out["arc_30_90"] = info(h)
# empty group: FInGroup must not point into the layer list
vs.BeginGroup(); vs.Locus((0.0, 0.0)); vs.EndGroup(); g = vs.LNewObj()
kid = vs.FInGroup(g); vs.DelObject(kid)
raw = vs.FInGroup(g)
out["empty_group"] = {"group_type": vs.GetTypeN(g), "raw_first_child_nil": str(raw) == "0",
                      "raw_first_child_type": None if str(raw) == "0" else vs.GetTypeN(raw),
                      "raw_child_parent_is_group": None if str(raw) == "0" else
                          vs.GetObjectUuid(vs.GetParent(raw)) == vs.GetObjectUuid(g)}
made.append(g)
for h in made:
    vs.DelObject(h)
vs.Layer(prev_layer)
lay = vs.GetLayerByName("KS-Fallstricke")
if str(lay) != "0" and str(vs.FInLayer(lay)) == "0":
    vs.DelObject(lay)
result = out
"""


def pitfall_tests(report: dict) -> None:
    from vwmcp.client import Bridge

    b = Bridge(root=ROOT)
    res = b.run(PITFALL_CODE, timeout=60)
    report["pitfalls"] = res["value"] if res["ok"] else {"error": res["error"], "tb": res["traceback"]}
    log(f"pitfalls: {'ok' if res['ok'] else res['error']}")


REG_LAYER = "KS-Test-neu"
REG_ITEMS = [
    {"type": "rect", "corner": [0, 0], "width": 3650, "height": 4800},
    {"type": "polygon", "points": [[5000, 0], [8000, 0], [8000, 1000], [6000, 1000], [6000, 3000], [5000, 3000]]},
    {"type": "circle", "center": [10000, 1500], "radius": 600},
    {"type": "path", "start": [12000, 0], "segments": [{"line_to": [14000, 0]},
     {"arc_to": [14000, 2000], "center": [14000, 1000], "direction": "ccw"}, {"line_to": [12000, 2000]}],
     "closed": True},
    {"type": "arc", "center": [16000, 1000], "radius": 800, "start_angle": 0, "sweep": 90},
    {"type": "line", "p1": [0, -1000], "p2": [3650, -1000]},
    {"type": "text", "text": "KS Test", "position": [0, 5200], "size": 10},
    {"type": "dimension", "p1": [0, 0], "p2": [3650, 0], "offset": -500, "orientation": "horizontal"},
]


def _first(objs: dict) -> dict:
    return (objs.get("objects") or [{}])[0]


def regression_tests(report: dict) -> None:
    """Same scenario as the 0.3.6 baseline: draw (deviation 0), check, move +
    revert, class change + revert, delete + revert, classes and layers."""
    from vwmcp.tools import draw2d, generic

    from vwmcp import app

    r: dict = {}
    t0 = time.monotonic()
    # start from an empty test layer (earlier runs leave their objects)
    app.get_bridge().run(
        "_l = vs.GetLayerByName(%r)\n_k = []\n"
        "def _c(h):\n    _k.append(h)\n"
        "vs.ForEachObject(_c, \"(L='%s')\")\n"
        "for _h in _k:\n    vs.DelObject(_h)\nlen(_k)" % (REG_LAYER, REG_LAYER))
    draw2d.vw2d_setup(classes=[{"name": "KS-Test-Wand", "attributes": {"pen": "#000000", "fill": "#808080",
                                                                       "line_weight": 0.35}},
                               {"name": "KS-Test-Moebel", "attributes": {"pen": "#333333", "fill": "none",
                                                                         "line_weight": 0.18}}],
                      layers=[{"name": REG_LAYER, "scale": 50}], active_layer=REG_LAYER)
    d = draw2d.vw2d_draw(REG_ITEMS, unit="mm", layer=REG_LAYER, class_name="KS-Test-Wand")
    r["draw"] = [[o["kind"], o["exact"], o["deviation_mm"]] for o in d["created"]]
    r["all_exact"], r["max_deviation_mm"] = d["all_exact"], d["max_deviation_mm"]
    rect, circle = d["created"][0]["uuid"], d["created"][2]["uuid"]
    chk = draw2d.vw2d_check(layer=REG_LAYER, image=False)
    r["check"] = chk.get("summary") if isinstance(chk, dict) else str(chk)[:400]
    draw2d.vw2d_transform([rect], "move", dx=100, dy=0, unit="mm")
    moved = _first(draw2d.vw2d_read(uuids=[rect]))
    rv = generic.vw_revert_last()
    rect2 = (rv.get("mapping") or {}).get(rect, rect)
    back = _first(draw2d.vw2d_read(uuids=[rect2]))
    r["move_revert"] = {"moved_bbox": moved.get("bbox"), "reverted_tool": rv.get("reverted_tool"),
                        "corners_after_revert": back.get("corners"),
                        "pass": back.get("corners") == [[0.0, 4.8], [3.65, 4.8], [3.65, 0.0], [0.0, 0.0]]}
    draw2d.vw2d_set_attributes([rect2], class_name="KS-Test-Moebel")
    c1 = _first(draw2d.vw2d_read(uuids=[rect2])).get("class")
    rv = generic.vw_revert_last()
    c2 = _first(draw2d.vw2d_read(uuids=[rect2])).get("class")
    r["class_revert"] = {"changed_to": c1, "after_revert": c2, "pass": c1 == "KS-Test-Moebel" and c2 == "KS-Test-Wand"}
    draw2d.vw2d_delete([circle])
    rv = generic.vw_revert_last()
    circle2 = (rv.get("mapping") or {}).get(circle)
    circ = _first(draw2d.vw2d_read(uuids=[circle2])) if circle2 else {}
    r["delete_revert"] = {"restored": rv.get("restored"), "center": circ.get("center"), "radius": circ.get("radius"),
                          "pass": bool(circle2) and abs((circ.get("radius") or 0) - 0.6) < 1e-9,
                          "bit_exact": circ.get("radius") == 0.6 and circ.get("center") == [10.0, 1.5]}
    r["deviation_zero"] = r["max_deviation_mm"] == 0.0
    st = draw2d.vw2d_structure()
    names = json.dumps(st)
    r["structure"] = {"has_layer": REG_LAYER in names, "has_classes": "KS-Test-Wand" in names and "KS-Test-Moebel" in names}
    layers = generic.vw_list_layers()
    r["layers"] = [l.get("name") for l in layers][:20]
    r["seconds"] = round(time.monotonic() - t0, 2)
    r["pass"] = (r["all_exact"] and r["move_revert"]["pass"] and r["class_revert"]["pass"]
                 and r["delete_revert"]["pass"] and r["structure"]["has_layer"] and r["structure"]["has_classes"])
    report["regression"] = r
    log(f"regression: pass={r['pass']} in {r['seconds']} s")


def tool_tests(report: dict) -> None:
    """The MCP tools in-process (same code Claude calls), read-only."""
    from vwmcp.tools import draw2d, generic

    out = {}
    t0 = time.monotonic()
    out["vw_ping"] = generic.vw_ping()
    out["vw_document_info"] = generic.vw_document_info()
    st = draw2d.vw2d_structure()
    out["vw2d_structure_keys"] = sorted(st)[:20]
    ex = generic.vw_exec_python("vs.Rect(0, 0, 1)", modifies_document=False)
    out["exec_rect"] = {"ok": ex["ok"], "error": ex["error"]}
    out["seconds"] = round(time.monotonic() - t0, 2)
    report["tools"] = out
    log(f"tools: {out['seconds']} s, ping {out['vw_ping'].get('bridge_version')}")


ASSET_LAYER = "KS-Test-Assets"
ASSET_FOLDER = "KS-Test Assets"

# Vectorworks-side probe: definition components of a symbol (class, by-class
# attributes) and whether a name exists as symbol / folder.
ASSET_PROBE = """
def _sym_info(name):
    sd = named(name, (16,))
    if sd is None:
        return None
    comps = []
    for k in children(sd):
        bc = {"fill": bool(vs.IsFPatByClass(k)), "pen": bool(vs.IsPenColorByClass(k)),
              "lw": bool(vs.IsLWByClass(k)), "ls": bool(vs.IsLSByClass(k))}
        comps.append({"t": vs.GetTypeN(k), "class": _str(vs.GetClass(k)), "by_class": bc})
    par = vs.GetParent(sd)
    return {"components": comps, "folder": _str(vs.GetName(par)) if vs.GetTypeN(par) == 92 else None,
            "instances": _symbol_instances(name)}
"""


def asset_tests(report: dict) -> None:
    """Every catalogue asset view placed as a symbol (exact read-back),
    reuse, mirror, parameters, rotation, groups, classes left alone, revert."""
    from vwmcp import app, catalog, calls
    from vwmcp.tools import assets, draw2d, generic

    b = app.get_bridge()

    def vwrun(body: str):
        res = calls._run_with_libs(b, [("vwlib", calls.VWLIB_SOURCE), ("vwlib2d", calls.VWLIB2D_SOURCE)],
                                   ASSET_PROBE + body, timeout=120)
        return calls.raise_for_result(res)["value"]

    r: dict = {}
    t0 = time.monotonic()
    # clean start: empty test layer, no definitions in the test folder
    cleaned = vwrun(
        "_k = []\n"
        "def _c(h):\n    _k.append(h)\n"
        "vs.ForEachObject(_c, \"(L='%s')\")\n"
        "for _h in _k:\n    vs.DelObject(_h)\n"
        "_f = named(%r, (92,))\n_d = 0\n"
        "if _f is not None:\n"
        "    _s = vs.FInFolder(_f)\n"
        "    _all = []\n"
        "    while not _is_nil(_s):\n        _all.append(_s)\n        _s = vs.NextObj(_s)\n"
        "    for _s in _all:\n        vs.DelObject(_s)\n        _d += 1\n"
        "    vs.DelObject(_f)\n"
        "result = [len(_k), _d]" % (ASSET_LAYER, ASSET_FOLDER))
    r["cleaned"] = cleaned
    # a user class with its own look must stay untouched
    draw2d.vw2d_setup(classes=[{"name": "Ausstattung-Möbel", "attributes": {"pen": "#123456", "fill": "#fafafa",
                                                                             "line_weight": 0.25}}],
                      layers=[{"name": ASSET_LAYER, "scale": 50}])
    # 1) every asset view, laid out in a grid (3 m apart; rows per category)
    rows: dict[str, int] = {}
    cols: dict[str, int] = {}
    placed, problems, times = [], [], []
    for a in catalog.CATALOG.values():
        row = rows.setdefault(a.category, len(rows))
        for v in a.views:
            t = time.monotonic()
            col = cols.get(a.category, 0)
            cols[a.category] = col + 1
            step = 9000 if a.category in ("plants", "vehicles") else 2500
            pos = [col * step, -row * 12000 if a.category in ("plants", "vehicles") else -row * 4000]
            try:
                o = assets.vw2d_place_asset(a.id, position=pos, view=v, unit="mm", layer=ASSET_LAYER,
                                            folder=ASSET_FOLDER)
            except Exception as e:  # noqa: BLE001
                problems.append({"asset": a.id, "view": v, "error": str(e)[:400]})
                continue
            times.append(time.monotonic() - t)
            placed.append([a.id, v, o["symbol"], o["symbol_created"], o["all_exact"], o.get("max_deviation_mm"),
                           o["placement_deviation_mm"]])
            if not (o["all_exact"] and o["symbol_created"] and o["placement_deviation_mm"] == 0.0):
                problems.append({"asset": a.id, "view": v, "out": o})
    r["placed"] = placed
    r["problems"] = problems
    r["place_seconds"] = {"n": len(times), "avg": round(sum(times) / max(1, len(times)), 3),
                          "max": round(max(times or [0]), 3)}
    # 2) reuse: three more WCs in one call
    o = assets.vw2d_place_asset("wc_wand", unit="mm", layer=ASSET_LAYER, folder=ASSET_FOLDER,
                                placements=[{"position": [0, 3000]}, {"position": [900, 3000]},
                                            {"position": [1800, 3000], "rotation": 180}])
    r["reuse"] = {"created": o["symbol_created"], "placed": o["placed"], "exact": o["all_exact"],
                  "pass": not o["symbol_created"] and o["placed"] == 3 and o["all_exact"]}
    # 3) mirror (asymmetric) and symmetric no-op, 4) parameters, 5) rotation 30
    m = assets.vw2d_place_asset("spuele", position=[0, 6000], unit="mm", mirror=True, layer=ASSET_LAYER,
                                folder=ASSET_FOLDER)
    ms = assets.vw2d_place_asset("wc_wand", position=[2000, 6000], unit="mm", mirror=True, layer=ASSET_LAYER,
                                 folder=ASSET_FOLDER)
    pr = assets.vw2d_place_asset("wc_wand", view="front", params={"h": 460}, position=[4000, 6000], unit="mm",
                                 layer=ASSET_LAYER, folder=ASSET_FOLDER)
    rt = assets.vw2d_place_asset("bett", position=[7000, 6000], rotation=30, unit="mm", layer=ASSET_LAYER,
                                 folder=ASSET_FOLDER)
    r["variants"] = {
        "mirror": [m["symbol"], m["mirrored"], m["all_exact"]],
        "mirror_symmetric": [ms["symbol"], ms["mirrored"], ms.get("note_mirror")],
        "params": [pr["symbol"], pr["symbol_created"], pr["all_exact"]],
        "rotation": [rt["instances"][0].get("rotation"), rt["instances"][0].get("rotation_deviation_deg"),
                     rt["placement_deviation_mm"]],
    }
    r["variants"]["pass"] = (m["mirrored"] and m["all_exact"] and m["symbol"].endswith("gespiegelt")
                             and not ms["mirrored"] and pr["symbol"].endswith("(h=460)") and pr["all_exact"]
                             and not rt["instances"][0].get("rotation_deviation_deg")
                             and rt["placement_deviation_mm"] == 0.0)
    # 6) groups instead of symbols
    g = assets.vw2d_place_asset("baum", position=[0, 10000], unit="mm", as_symbol=False, layer=ASSET_LAYER,
                                rotation=15)
    gr = draw2d.vw2d_read(uuids=[g["instances"][0]["uuid"]])
    r["group"] = {"kind": _first(gr).get("kind"), "class": _first(gr).get("class"), "exact": g["all_exact"],
                  "pass": _first(gr).get("kind") == "group" and g["all_exact"]}
    # 7) definition components: classes and by-class attributes; folder
    info = vwrun("result = {n: _sym_info(n) for n in %r}" % ([
        "WC wandhängend - Grundriss", "Baum - Grundriss", "Esstisch mit Stühlen - Grundriss"],))
    ok = True
    for n, inf in info.items():
        if not inf:
            ok = False
            continue
        base = {"WC wandhängend - Grundriss": "Ausstattung-Sanitär", "Baum - Grundriss": "Staffage-Vegetation",
                "Esstisch mit Stühlen - Grundriss": "Ausstattung-Möbel"}[n]
        ok = ok and inf["folder"] == ASSET_FOLDER and all(
            c["class"] in (base, base + "-Detail") and all(c["by_class"].values()) for c in inf["components"])
    r["definitions"] = {"info": info, "pass": ok}
    # 8) classes: created with attributes; the user's class unchanged
    st = draw2d.vw2d_structure()
    cls = {c["name"]: c.get("attributes") for c in st.get("classes", [])}
    moebel = cls.get("Ausstattung-Möbel") or {}
    r["classes"] = {"sanitaer": cls.get("Ausstattung-Sanitär"), "detail": cls.get("Ausstattung-Sanitär-Detail"),
                    "moebel": moebel,
                    "pass": moebel.get("pen") == "#123456" and "Ausstattung-Sanitär-Detail" in cls}
    # 9) revert: a new definition goes with its instance; a reused one stays
    t1 = assets.vw2d_place_asset("tisch", params={"chairs": 4}, position=[12000, 10000], unit="mm",
                                 layer=ASSET_LAYER, folder=ASSET_FOLDER)
    rv = generic.vw_revert_last()
    gone = vwrun("result = _sym_info(%r)" % t1["symbol"])
    k1 = assets.vw2d_place_asset("kochfeld", params={"b": 900}, position=[15000, 10000], unit="mm",
                                 layer=ASSET_LAYER, folder=ASSET_FOLDER)
    k2 = assets.vw2d_place_asset("kochfeld", params={"b": 900}, position=[16000, 10000], unit="mm",
                                 layer=ASSET_LAYER, folder=ASSET_FOLDER)
    rv2 = generic.vw_revert_last()
    still = vwrun("result = _sym_info(%r)" % k1["symbol"])
    rv3 = generic.vw_revert_last()
    gone2 = vwrun("result = _sym_info(%r)" % k1["symbol"])
    r["revert"] = {"new_def_removed": gone is None, "tool": rv.get("reverted_tool"),
                   "reused_def_kept": bool(still) and still["instances"] == 1 and not k2["symbol_created"],
                   "then_removed": gone2 is None,
                   "pass": gone is None and bool(still) and still["instances"] == 1 and gone2 is None}
    # 10) check on a layer full of symbols must work
    chk = draw2d.vw2d_check(layer=ASSET_LAYER, image=False)
    r["check"] = chk.get("summary") if isinstance(chk, dict) else str(chk)[:300]
    r["seconds"] = round(time.monotonic() - t0, 2)
    r["pass"] = (not problems and r["reuse"]["pass"] and r["variants"]["pass"] and r["group"]["pass"]
                 and r["definitions"]["pass"] and r["classes"]["pass"] and r["revert"]["pass"])
    report["assets"] = r
    log(f"assets: pass={r['pass']} ({len(placed)} placed, {len(problems)} problems) in {r['seconds']} s")


PERF_LAYER = "KS-Test-Perf"
PERF_SIZES = [int(x) for x in os.environ.get("VWMCP_PERF_SIZES", "2000,10000,25000").split(",") if x]

# A messy "DWG import" on PERF_LAYER: n objects on a 1 m grid in classes A-WALL,
# A-DOOR, A-ANNO and 0 - rectangles (every 7th 1 mm too wide: overlap with its
# neighbour), polygons, lines, texts and exact duplicates.
PERF_GEN = """
_mpu = units()["mm_per_unit"]
_mm = 1.0 / _mpu
_state = _begin(%(layer)r)
try:
    _cols = int(math.sqrt(%(n)d)) or 1
    _made = 0
    _cell = 0
    for _i in range(%(n)d):
        _k = _i %% 10
        if _k <= 5:  # a tile filling its cell (tiles share edges), every 7th 1 mm too wide
            _x, _y = (_cell %% _cols) * 1000 * _mm, -(_cell // _cols) * 1000 * _mm
            _cell += 1
            vs.NameClass("A-WALL")
            _w = (1001 if _cell %% 7 == 0 else 1000) * _mm
            vs.Rect((_x, _y), (_x + _w, _y - 1000 * _mm))
            _last = (_x, _y, _w)
        elif _k == 9:  # an exact duplicate of the last tile
            vs.NameClass("A-WALL")
            vs.Rect((_last[0], _last[1]), (_last[0] + _last[2], _last[1] - 1000 * _mm))
        elif _k == 6:
            vs.NameClass("0")
            vs.ClosePoly()
            vs.Poly((_x + 100 * _mm, _y - 100 * _mm), (_x + 600 * _mm, _y - 100 * _mm),
                    (_x + 600 * _mm, _y - 400 * _mm), (_x + 100 * _mm, _y - 400 * _mm))
        elif _k == 7:
            vs.NameClass("A-DOOR")
            vs.MoveTo((_x + 100 * _mm, _y - 900 * _mm))
            vs.LineTo((_x + 900 * _mm, _y - 500 * _mm))
        else:
            vs.NameClass("A-ANNO")
            vs.TextOrigin((_x + 500 * _mm, _y - 700 * _mm))
            vs.CreateText("Raum %%d" %% _i)
        _made += 1
finally:
    _end(_state)
result = _made
"""


def perf_tests(report: dict) -> None:
    """Large drawings: generate n objects, then time read, structure, check,
    fix duplicates (+ revert), organize (dry run, apply, revert)."""
    from vwmcp import app, calls
    from vwmcp.tools import draw2d, generic, organize

    b = app.get_bridge()

    def vwrun(body: str, timeout: float = 600):
        res = calls._run_with_libs(b, [("vwlib", calls.VWLIB_SOURCE), ("vwlib2d", calls.VWLIB2D_SOURCE)],
                                   body, timeout=timeout)
        return calls.raise_for_result(res)["value"]

    def clear():
        return vwrun("_k = []\ndef _c(h):\n    _k.append(h)\nvs.ForEachObject(_c, \"(L='%s')\")\n"
                     "for _h in _k:\n    vs.DelObject(_h)\nresult = len(_k)" % PERF_LAYER)

    out: dict = {}
    for n in PERF_SIZES:
        r: dict = {}
        draw2d.vw2d_setup(layers=[{"name": PERF_LAYER, "scale": 100}])
        clear()
        t = time.monotonic()
        r["generated"] = vwrun(PERF_GEN % {"layer": PERF_LAYER, "n": n})
        r["generate_s"] = round(time.monotonic() - t, 2)

        def timed(name, fn):
            t0 = time.monotonic()
            try:
                v = fn()
                r[name + "_s"] = round(time.monotonic() - t0, 2)
                return v
            except Exception as e:  # noqa: BLE001
                r[name + "_s"] = round(time.monotonic() - t0, 2)
                r[name + "_error"] = str(e)[:300]
                return None

        rd = timed("read", lambda: draw2d.vw2d_read(layer=PERF_LAYER, include_geometry=False, limit=n + 10))
        r["read_count"] = len((rd or {}).get("objects", [])) if isinstance(rd, dict) else None
        timed("read_geometry", lambda: draw2d.vw2d_read(layer=PERF_LAYER, include_geometry=True, limit=n + 10))
        timed("structure", draw2d.vw2d_structure)
        def as_dict(v):
            if isinstance(v, list):
                v = v[0]
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except ValueError:
                    return {"text": v[:300]}
            return v if isinstance(v, dict) else {}

        chk = as_dict(timed("check", lambda: draw2d.vw2d_check(layer=PERF_LAYER, image=False)))
        summ = chk.get("summary") or {}
        r["check_summary"] = {k: summ.get(k) for k in ("objects", "checked", "errors", "warnings", "by_kind",
                                                       "warning")}
        def fix_info(fx):
            ap = fx.get("applied") or {}
            return {"applied": {k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in ap.items()},
                    "errors_before": (fx.get("before") or {}).get("errors"),
                    "errors_after": (fx.get("after") or {}).get("errors"),
                    "by_kind_after": (fx.get("after") or {}).get("by_kind")}

        def changed(fx):
            ap = fx.get("applied") or {}
            return bool(ap.get("deleted") or ap.get("clipped") or ap.get("deleted_covered"))

        fx = as_dict(timed("fix_duplicates", lambda: draw2d.vw2d_fix("duplicates", layer=PERF_LAYER, image=False)))
        r["fix_duplicates"] = fix_info(fx)
        if changed(fx) and n <= 10000:  # clipping thousands of overlaps on bigger sets: minutes
            fo = as_dict(timed("fix_overlaps", lambda: draw2d.vw2d_fix("overlaps", layer=PERF_LAYER,
                                                                       image=False)))
            r["fix_overlaps"] = fix_info(fo)
            if changed(fo):
                rv = timed("revert_overlaps", generic.vw_revert_last)
                if isinstance(rv, dict):
                    r["revert_overlaps"] = {k: rv.get(k) for k in ("restored", "deleted", "reverted_tool")}
        if changed(fx):
            rv = timed("revert_duplicates", generic.vw_revert_last)
            if isinstance(rv, dict):
                r["revert_duplicates"] = {k: rv.get(k) for k in ("restored", "deleted", "reverted_tool")}
        cmap = {"A-WALL": "Wand-Tragend", "A-DOOR": "Öffnung-Tür", "A-ANNO": "Beschriftung", "0": "Hilfslinien"}
        dr = timed("organize_dry", lambda: organize.vw2d_organize(class_map=cmap, layer=PERF_LAYER))
        if isinstance(dr, dict):
            r["organize_dry"] = {k: dr.get(k) for k in ("objects", "to_change", "unmatched", "targets")}
        ap = timed("organize_apply", lambda: organize.vw2d_organize(class_map=cmap, layer=PERF_LAYER,
                                                                    dry_run=False))
        if isinstance(ap, dict):
            r["organize_changed"] = ap.get("changed")
        after = timed("organize_check", lambda: organize.vw2d_organize(class_map=cmap, layer=PERF_LAYER))
        if isinstance(after, dict):
            r["organize_after"] = {"to_change": after.get("to_change"), "unchanged": after.get("unchanged")}
        if r.get("organize_changed"):
            rv = timed("revert_organize", generic.vw_revert_last)
            if isinstance(rv, dict):
                r["revert_organize"] = {k: rv.get(k) for k in ("restored", "reverted_tool")}
        back = timed("organize_back", lambda: organize.vw2d_organize(class_map=cmap, layer=PERF_LAYER))
        if isinstance(back, dict):
            r["organize_back_to_change"] = back.get("to_change")
        r["cleared"] = clear()
        out[str(n)] = r
        log(f"perf {n}: " + ", ".join(f"{k}={v}" for k, v in r.items() if k.endswith("_s")))
    report["perf"] = out


async def _mcp_round_trip(server_dir: str) -> dict:
    """Start the packaged server like Claude Desktop does (stdio) and call tools."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = dict(os.environ)
    env.pop("VWMCP_APP", None)
    env.update({"VWMCP_AUTO_BRIDGE": "1", "PYTHONUNBUFFERED": "1"})
    params = StdioServerParameters(command=sys.executable, args=[os.path.join(server_dir, "main.py")],
                                   env=env, cwd=server_dir)
    out: dict = {}
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            t0 = time.monotonic()
            init = await session.initialize()
            out["server"] = f"{init.server_info.name} {init.server_info.version}"
            tools = await session.list_tools()
            out["tools"] = len(tools.tools)
            out["start_s"] = round(time.monotonic() - t0, 2)

            async def call(name, **kw):
                r = await session.call_tool(name, kw)
                text = "".join(getattr(c, "text", "") for c in r.content)
                return {"error": bool(r.is_error), "text": text[:600]}

            out["vw_ping"] = await call("vw_ping")
            out["vw_vs_lookup"] = await call("vw_vs_lookup", query="Rect", limit=2)
            out["vw_document_info"] = await call("vw_document_info")
            out["vw2d_read"] = await call("vw2d_read", layer=REG_LAYER, limit=2, include_geometry=False)
            out["exec_bad_rect"] = await call("vw_exec_python", code="vs.Rect(0, 0, 1)", modifies_document=False)
            out["seconds"] = round(time.monotonic() - t0, 2)
    return out


def mcp_tests(report: dict, server_dir: str | None) -> None:
    import asyncio

    if not server_dir:
        report["mcp"] = {"skipped": "no --server"}
        return
    r = asyncio.run(_mcp_round_trip(server_dir))
    r["pass"] = (not r["vw_ping"]["error"] and "0.2.1" in r["vw_ping"]["text"]
                 and "vs.Rect(p1: POINT, p2: POINT)" in r["vw_vs_lookup"]["text"]
                 and "VSArgumentError" in r["exec_bad_rect"]["text"] and not r["vw2d_read"]["error"])
    report["mcp"] = r
    log(f"mcp: pass={r['pass']} ({r.get('server')}, {r.get('tools')} tools, start {r.get('start_s')} s)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(Path(__file__).resolve().parents[1] / "src"))
    ap.add_argument("--only", default="protocol,tools,pitfalls,regression")
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--server", help="unpacked extension's server/ folder for the MCP round trip (--only mcp)")
    args = ap.parse_args()
    sys.path.insert(0, args.src)
    ROOT.mkdir(parents=True, exist_ok=True)
    time.sleep(args.delay)
    wait_idle()
    report = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "python": sys.version.split()[0]}
    log(f"live test start ({args.only})")
    sections = (("protocol", protocol_tests), ("tools", tool_tests), ("pitfalls", pitfall_tests),
                ("regression", regression_tests), ("assets", asset_tests), ("perf", perf_tests),
                ("mcp", lambda rep: mcp_tests(rep, args.server)))
    for name, fn in sections:
        if name not in args.only.split(","):
            continue
        try:
            fn(report)
        except Exception as e:  # noqa: BLE001
            report[f"{name}_error"] = traceback.format_exc()
            log(f"{name} failed: {e!r}")
    report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    REPORT.write_text(json.dumps(report, indent=1, default=repr), encoding="utf-8")
    log("live test done")


if __name__ == "__main__":
    main()
