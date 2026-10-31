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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(Path(__file__).resolve().parents[1] / "src"))
    ap.add_argument("--only", default="protocol,tools,pitfalls,regression")
    ap.add_argument("--delay", type=float, default=0.0)
    args = ap.parse_args()
    sys.path.insert(0, args.src)
    ROOT.mkdir(parents=True, exist_ok=True)
    time.sleep(args.delay)
    wait_idle()
    report = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "python": sys.version.split()[0]}
    log(f"live test start ({args.only})")
    sections = (("protocol", protocol_tests), ("tools", tool_tests), ("pitfalls", pitfall_tests),
                ("regression", regression_tests))
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
