"""Live end-to-end test of the tools against the running Vectorworks (a throwaway
document with the Rhino test exports imported, see docs: /tmp/keystone-test).

Runs the real tool functions of this checkout (run it with the extension's
Python, e.g. ~/.vwmcp/venv/bin/python scripts/e2e_live.py stock check ...). Journal and profile
are isolated in ROOT (the user's ~/.vwmcp journal/profile stay untouched; the
bridge job folder ~/.vwmcp/jobs is shared - that is how Vectorworks is reached).
Usage: python run_e2e.py <phase> ...   Results: ROOT/results.jsonl, images ROOT/img/
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(os.environ.get("VWMCP_E2E_OUT", "/tmp/keystone-test/e2e"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ["VWMCP_UPDATE_CHECK"] = "0"

from vwmcp import app, profile  # noqa: E402
from vwmcp.journal import Journal  # noqa: E402

J = Journal(ROOT / "journal.jsonl")
app.get_journal = lambda: J
profile._home = lambda: ROOT / "home"

from vwmcp.tools import cleanup, draw2d, generic, organize  # noqa: E402

IMG = ROOT / "img"
IMG.mkdir(parents=True, exist_ok=True)
OUT = ROOT / "results.jsonl"
T = "/tmp/keystone-test/"


def log(step, **kw):
    kw["step"] = step
    kw["t"] = time.strftime("%H:%M:%S")
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(kw, ensure_ascii=False, default=str) + "\n")


def res(r, name=None):
    """Tool result -> parsed JSON; images saved."""
    if isinstance(r, list):
        data = json.loads(r[0]) if isinstance(r[0], str) else r[0]
        for i, im in enumerate(r[1:]):
            if name and hasattr(im, "data"):
                (IMG / ("%s_%d.png" % (name, i))).write_bytes(im.data)
        return data
    if isinstance(r, str):
        return json.loads(r)
    return r


def count(layer):
    return len(draw2d._read(layer=layer, geometry=False, allow_temp=False, limit=200000)["objects"])


def run(step, fn):
    t0 = time.time()
    try:
        out = fn()
        log(step, ok=True, s=round(time.time() - t0, 1), **(out or {}))
    except Exception as e:  # noqa: BLE001
        log(step, ok=False, s=round(time.time() - t0, 1), error=repr(e)[:800], tb=traceback.format_exc()[-1500:])


def pick(d, keys):
    return {k: d.get(k) for k in keys if k in d}


STOCK = ("objects", "kinds", "classes_total", "size_m", "lines", "polylines", "joinable", "looks_generated",
         "source", "next_steps", "explicit_attributes")


# ---- phases --------------------------------------------------------------------------------

def phase_stock():
    for lay in ("testschnitt", "m2d_schnitt_polylinien", "m2d_schnitt_r12", "m2d_grundriss", "plan_bloecke",
                "m2d_iso_schnitt", "m2d_gross"):
        run("stock " + lay, lambda lay=lay: pick(cleanup.vw2d_import(layer=lay), STOCK))


def phase_import():
    for f in ("m2d_schnitt_2018.dxf", "m2d_schnitt_r12.dxf", "plan_bloecke.dwg", "m2d_schnitt_mm.dwg"):
        def one(f=f):
            out = cleanup.vw2d_import(path=T + f)
            new = out.get("imported", {}).get("new_layers")
            rv = generic.vw_revert_last()
            layers = [x["name"] for x in app.vw("list_layers")]
            return {"imported": out.get("imported"), "source": out.get("source"), "objects": out.get("objects"),
                    "next_steps": out.get("next_steps"), "revert": pick(rv, ("reverted_tool", "deleted", "missing")),
                    "layer_gone_after_revert": not any(n in layers for n in (new or []))}
        run("import " + f, one)


def phase_check():
    for lay in ("testschnitt", "m2d_grundriss", "m2d_gross"):
        def one(lay=lay):
            full = res(draw2d.vw2d_check(layer=lay, image=False, max_issues=1))["summary"]
            drw = res(draw2d.vw2d_check(layer=lay, image=False, max_issues=3, preset="drawing"))
            return {"full": pick(full, ("errors", "warnings", "infos")),
                    "drawing": pick(drw["summary"], ("errors", "warnings", "infos", "by_kind")),
                    "drawing_issues": [pick(i, ("kind", "message")) for i in drw.get("issues", [])]}
        run("check " + lay, one)
    run("preview marks", lambda: {
        "with": res(draw2d.vw2d_preview(layer="testschnitt", width_px=1200), "preview_testschnitt_marks")["summary"]["errors"],
        "without": res(draw2d.vw2d_preview(layer="testschnitt", width_px=1200, marks=False),
                       "preview_testschnitt_clean")["summary"]["errors"]})


def phase_join():
    lay = "testschnitt"
    n0 = count(lay)
    run("join dry " + lay, lambda: pick(res(cleanup.vw2d_join(layer=lay, dry_run=True), "join_dry_testschnitt"),
                                       ("pieces", "joined_pieces", "polylines", "closed", "objects_after",
                                        "not_joinable", "dry_run")))
    run("join apply " + lay, lambda: dict(pick(res(cleanup.vw2d_join(layer=lay, dry_run=False), "join_apply_testschnitt"),
                                               ("polylines", "closed", "objects_after", "applied")),
                                          count_now=count(lay)))
    run("check after join", lambda: pick(res(draw2d.vw2d_check(layer=lay, image=False, max_issues=1,
                                                               preset="drawing"))["summary"], ("errors", "warnings")))
    run("preview after join", lambda: {"ok": bool(res(draw2d.vw2d_preview(layer=lay, marks=False, width_px=1400),
                                                      "after_join_testschnitt"))})
    run("revert join", lambda: dict(pick(generic.vw_revert_last(), ("reverted_tool", "deleted", "restored", "missing")),
                                    count_now=count(lay), count_start=n0))


def phase_simplify():
    lay = "m2d_schnitt_polylinien"
    n0 = count(lay)
    side = [0.6, 1.2, 2.3, 6.0]
    run("simplify dry side curtain", lambda: pick(res(cleanup.vw2d_simplify(layer=lay, bbox=side), "simplify_dry_side"),
                                                  ("tolerance_mm", "objects", "vertices_before", "vertices_after",
                                                   "left_alone")))
    run("join side curtain", lambda: pick(res(cleanup.vw2d_join(layer=lay, bbox=side, dry_run=False), "join_side"),
                                          ("pieces", "joined_pieces", "polylines", "objects_after")))
    run("simplify apply side curtain", lambda: pick(res(cleanup.vw2d_simplify(layer=lay, bbox=side, dry_run=False),
                                                        "simplify_apply_side"),
                                                    ("tolerance_mm", "objects", "vertices_before", "vertices_after",
                                                     "applied")))
    run("simplify coarse dry", lambda: pick(res(cleanup.vw2d_simplify(layer=lay, bbox=side, tolerance_mm=50),
                                                "simplify_coarse_side"), ("objects", "vertices_before", "vertices_after")))
    run("revert simplify", lambda: pick(generic.vw_revert_last(), ("reverted_tool", "deleted", "restored", "missing")))
    run("revert join", lambda: dict(pick(generic.vw_revert_last(), ("reverted_tool", "deleted", "restored", "missing")),
                                    count_now=count(lay), count_start=n0))


def phase_organize_group():
    lay = "m2d_schnitt_polylinien"
    side, stage = [0.6, 1.2, 2.3, 6.0], [8.4, 1.15, 14.3, 5.9]
    rules = [{"match": {"bbox": side, "class": "Make2D$Visible$*", "not": {"class": "*Clipping*"}}, "class": "Vorhang-Seite",
              "by_class": True},
             {"match": {"bbox": stage, "class": "Make2D$Visible$*", "not": {"class": "*Clipping*"}}, "class": "Vorhang-Bühne",
              "by_class": True},
             {"match": {"class": "*Clipping*"}, "class": "Schnitt-Linie", "by_class": True},
             {"match": {"class": "*Fills"}, "class": "Schnitt-Füllung", "by_class": True},
             {"match": {"class": "Make2D$Visible$*"}, "class": "Ansicht", "by_class": True}]
    run("organize dry", lambda: pick(organize.vw2d_organize(rules=rules, layer=lay), ("to_change", "unmatched", "targets")))
    run("organize apply", lambda: pick(organize.vw2d_organize(rules=rules, layer=lay, dry_run=False),
                                       ("changed", "targets", "dry_run")))
    run("setup line weights", lambda: pick(draw2d.vw2d_setup(classes=[
        {"name": "Schnitt-Linie", "attributes": {"pen": "#000000", "line_weight": 0.5, "fill": "none"}},
        {"name": "Schnitt-Füllung", "attributes": {"pen": "#000000", "fill": "#404040", "line_weight": 0.05}},
        {"name": "Ansicht", "attributes": {"pen": "#000000", "line_weight": 0.18, "fill": "none"}},
        {"name": "Vorhang-Seite", "attributes": {"pen": "#555555", "line_weight": 0.1, "fill": "none"}},
        {"name": "Vorhang-Bühne", "attributes": {"pen": "#555555", "line_weight": 0.1, "fill": "none"}}]), ("document",)))
    run("group curtains", lambda: pick(cleanup.vw2d_group(layer=lay, class_name="Vorhang-Seite", name="Seitenvorhang"),
                                       ("groups", "objects")))
    run("preview organized", lambda: {"ok": bool(res(draw2d.vw2d_preview(layer=lay, marks=False, width_px=1400),
                                                     "organized"))})
    run("revert group", lambda: pick(generic.vw_revert_last(), ("reverted_tool", "restored", "missing")))
    run("revert organize", lambda: pick(generic.vw_revert_last(), ("reverted_tool", "restored", "missing")))


def phase_profile():
    lay = "testschnitt"
    run("profile set", lambda: {"rules": cleanup.vw2d_profile("set", values={"confirm_above": 50})["rules_now"][-200:]})
    run("join auto dry (confirm_above 50)", lambda: pick(res(cleanup.vw2d_join(layer=lay, image=False)),
                                                         ("dry_run", "applied", "joined_pieces")))
    run("profile 0", lambda: {"ok": bool(cleanup.vw2d_profile("set", values={"confirm_above": 0}))})
    run("join auto apply (confirm_above 0)", lambda: pick(res(cleanup.vw2d_join(layer=lay, image=False)),
                                                          ("dry_run", "applied", "joined_pieces")))
    run("revert", lambda: pick(generic.vw_revert_last(), ("reverted_tool", "restored", "missing")))
    run("profile reset", lambda: {"ok": bool(cleanup.vw2d_profile("reset"))})


def phase_big():
    lay = "m2d_gross"
    n0 = count(lay)
    run("big stock", lambda: pick(cleanup.vw2d_import(layer=lay), ("objects", "joinable", "lines")))
    run("big join apply", lambda: dict(pick(res(cleanup.vw2d_join(layer=lay, dry_run=False, image=False)),
                                            ("pieces", "joined_pieces", "polylines", "closed", "objects_after")),
                                       count_now=count(lay)))
    run("big check drawing", lambda: pick(res(draw2d.vw2d_check(layer=lay, image=False, max_issues=1, preset="drawing"))
                                          ["summary"], ("errors", "warnings", "infos")))
    run("big revert", lambda: dict(pick(generic.vw_revert_last(), ("reverted_tool", "deleted", "restored", "missing")),
                                   count_now=count(lay), count_start=n0))


def phase_workflow():
    """The student's request on a fresh import - kept as a result layer."""
    side, stage = [0.6, 1.2, 2.3, 6.0], [8.4, 1.15, 14.3, 5.9]
    state = {}

    def imp():
        out = cleanup.vw2d_import(path=T + "m2d_schnitt_polylinien.dwg")
        state["layer"] = out["imported"]["new_layers"][0]
        return pick(out, ("imported", "objects", "joinable", "next_steps"))
    run("wf import", imp)
    lay = state.get("layer")
    if not lay:
        return
    rules = [{"match": {"bbox": side, "class": "Make2D$Visible$*", "not": {"class": "*Clipping*"}}, "class": "Vorhang",
              "by_class": True},
             {"match": {"bbox": stage, "class": "Make2D$Visible$*", "not": {"class": "*Clipping*"}}, "class": "Vorhang",
              "by_class": True},
             {"match": {"class": "*Clipping*"}, "class": "Schnitt-Linie", "by_class": True},
             {"match": {"class": "*Fills"}, "class": "Schnitt-Füllung", "by_class": True},
             {"match": {"class": "Make2D$Visible$*"}, "class": "Ansicht", "by_class": True}]
    run("wf organize", lambda: pick(organize.vw2d_organize(rules=rules, layer=lay, dry_run=False), ("changed", "targets")))
    run("wf line weights", lambda: pick(draw2d.vw2d_setup(classes=[
        {"name": "Schnitt-Linie", "attributes": {"pen": "#000000", "line_weight": 0.5, "fill": "none"}},
        {"name": "Schnitt-Füllung", "attributes": {"pen": "#000000", "fill": "#404040", "line_weight": 0.05}},
        {"name": "Ansicht", "attributes": {"pen": "#000000", "line_weight": 0.18, "fill": "none"}},
        {"name": "Vorhang", "attributes": {"pen": "#555555", "line_weight": 0.1, "fill": "none"}}]), ("document",)))
    run("wf join", lambda: pick(res(cleanup.vw2d_join(layer=lay, dry_run=False), "wf_join"),
                                ("pieces", "joined_pieces", "polylines", "closed", "objects_after")))
    run("wf simplify curtains", lambda: pick(res(cleanup.vw2d_simplify(layer=lay, class_name="Vorhang", dry_run=False),
                                                 "wf_simplify"),
                                             ("tolerance_mm", "objects", "vertices_before", "vertices_after")))
    run("wf group side", lambda: pick(cleanup.vw2d_group(layer=lay, class_name="Vorhang", bbox=side, name="Seitenvorhang"),
                                      ("groups", "objects")))
    run("wf group stage", lambda: pick(cleanup.vw2d_group(layer=lay, class_name="Vorhang", bbox=stage,
                                                          name="Bühnenvorhang"), ("groups", "objects")))
    run("wf check", lambda: pick(res(draw2d.vw2d_check(layer=lay, image=False, max_issues=5, preset="drawing")),
                                 ("summary", "issues")))
    run("wf final stock", lambda: pick(cleanup.vw2d_import(layer=lay), ("objects", "kinds", "classes", "lines",
                                                                        "polylines", "joinable")))
    run("wf preview", lambda: {"ok": bool(res(draw2d.vw2d_preview(layer=lay, marks=False, width_px=1600), "wf_final"))})


PHASES = {"stock": phase_stock, "import": phase_import, "check": phase_check, "join": phase_join,
          "simplify": phase_simplify, "organize": phase_organize_group, "profile": phase_profile, "big": phase_big,
          "workflow": phase_workflow}

if __name__ == "__main__":
    log("start", phases=sys.argv[1:], version=__import__("vwmcp").__version__)
    for p in sys.argv[1:]:
        try:
            PHASES[p]()
        except Exception as e:  # noqa: BLE001
            log("phase " + p, ok=False, error=repr(e), tb=traceback.format_exc()[-1500:])
    log("done")
