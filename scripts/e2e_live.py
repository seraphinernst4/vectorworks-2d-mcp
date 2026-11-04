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
        out = dict(fn() or {})
        for k in ("ok", "s", "step", "t"):  # a tool result's own keys must not clash with the log's
            if k in out:
                out["result_" + k] = out.pop(k)
        log(step, ok=True, s=round(time.time() - t0, 1), **out)
    except Exception as e:  # noqa: BLE001
        log(step, ok=False, s=round(time.time() - t0, 1), error=repr(e)[:800], tb=traceback.format_exc()[-1500:])


def expect_error(fn, *words):
    """Run fn, expecting a ToolError whose message has all words."""
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        return {"raised": type(e).__name__, "message": msg[:300], "as_expected": all(w in msg for w in words)}
    return {"raised": None, "as_expected": False}


def _round(v):
    if isinstance(v, float):
        return round(v, 6) + 0.0
    if isinstance(v, list):
        return [_round(x) for x in v]
    if isinstance(v, dict):
        return {k: _round(x) for k, x in sorted(v.items())}
    return v


def _canon_ring(vs_):
    """Same ring, same start and direction (rounded vertices)."""
    r = [tuple(_round(v)) for v in vs_]
    if len(r) < 3:
        return [list(v) for v in r]
    cands = []
    for seq in (r, r[::-1]):
        k = min(range(len(seq)), key=lambda i: seq[i][:2])
        cands.append(seq[k:] + seq[:k])
    return [list(v) for v in min(cands)]


def fingerprint(layer):
    """Everything a revert must bring back, without uuids: geometry with
    vertex types, class, attributes, group membership, stacking order."""
    recs = draw2d._read(layer=layer, geometry=True, allow_temp=False, limit=200000)["objects"]
    idx = {r["uuid"]: i for i, r in enumerate(recs)}
    out = []
    for r in recs:
        d = {k: v for k, v in r.items() if k not in ("uuid", "parent", "approx_polygon", "name")}
        d["parent"] = idx.get(r.get("parent")) if r.get("parent") else None
        if d.get("closed") and d.get("vertices"):  # a ring may come back starting elsewhere (AddHole)
            d["vertices"] = _canon_ring(d["vertices"])
        if d.get("holes"):
            d["holes"] = [_canon_ring(h) for h in d["holes"]]
        if d.get("holes"):  # Vectorworks may hand holes back in another order
            d["holes"] = sorted(d["holes"], key=lambda h: json.dumps(_round(h)))
        for k in ("area", "perimeter", "length"):  # derived; float noise of the user origin shift
            if isinstance(d.get(k), float):
                d[k] = round(d[k], 9)
        out.append(json.dumps(_round(d), sort_keys=True, ensure_ascii=False))
    return out


def _close(a, b, tol):
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) <= tol
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k], tol) for k in a)
    return a == b


def looks(layer):
    """What each object shows (attributes without the by-class flags, class left out)."""
    recs = draw2d._read(layer=layer, geometry=True, allow_temp=False, limit=200000)["objects"]
    out = []
    for r in recs:
        a = dict(r.get("attrs") or {})
        a.pop("by_class", None)
        if r.get("kind") == "line":  # a line shows no fill
            for k in ("fill", "fill_color", "hatch"):
                a.pop(k, None)
        geo = r.get("vertices") or [r.get("p1"), r.get("p2")]
        out.append(json.dumps(_round({"kind": r.get("kind"), "attrs": a, "geo": geo}), sort_keys=True))
    return out


def same(a, b):
    """Ordered and unordered comparison of two fingerprints."""
    from collections import Counter
    ca, cb = Counter(a), Counter(b)
    out = {"objects": [len(a), len(b)], "exact_order": a == b, "same_set": ca == cb,
           "missing": sum((ca - cb).values()), "extra": sum((cb - ca).values())}
    if ca != cb:  # float noise only? (the user origin shift costs a few ULP on a round trip; the fingerprint rounds to 1e-6)
        miss, extra = list((ca - cb).elements()), list((cb - ca).elements())
        left = [json.loads(e) for e in extra]
        matched = 0
        for m in miss:
            dm = json.loads(m)
            k = next((i for i, de in enumerate(left) if _close(dm, de, 1.01e-6)), None)
            if k is not None:
                left.pop(k)
                matched += 1
        out["same_within_1um"] = matched == len(miss) and not left
    if ca != cb and not out.get("same_within_1um"):  # what differs: the first missing/extra records, key by key
        miss, extra = list((ca - cb).elements())[:3], list((cb - ca).elements())[:3]
        diffs = []
        for m, e in zip(miss, extra):
            dm, de = json.loads(m), json.loads(e)
            diffs.append({k: [dm.get(k), de.get(k)] for k in sorted(set(dm) | set(de)) if dm.get(k) != de.get(k)})
        out["diffs"] = json.loads(json.dumps(diffs)[:3000]) if len(json.dumps(diffs)) <= 3000 else json.dumps(diffs)[:3000]
    return out


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
                       "preview_testschnitt_clean")})


def phase_join():
    lay = "testschnitt"
    n0 = count(lay)
    run("join dry " + lay, lambda: pick(res(cleanup.vw2d_join(layer=lay, dry_run=True), "join_dry_testschnitt"),
                                       ("pieces", "joined_pieces", "polylines", "closed", "objects_after",
                                        "not_joinable", "dry_run")))
    run("join apply " + lay, lambda: dict(pick(res(cleanup.vw2d_join(layer=lay, dry_run=False), "join_apply_testschnitt"),
                                               ("polylines", "closed", "objects_after", "applied")),
                                          count_now=count(lay)))
    run("check after join", lambda: (lambda d: dict(pick(d["summary"], ("errors", "warnings", "by_kind")),
                                                    issues=[pick(i, ("kind", "message")) for i in d["issues"]]))(
        res(draw2d.vw2d_check(layer=lay, image=False, max_issues=6, preset="drawing"))))
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
    def org():
        lk0 = looks(lay)
        out = pick(organize.vw2d_organize(rules=rules, layer=lay, dry_run=False),
                   ("changed", "targets", "dry_run", "by_class_looks"))
        out["look_kept"] = same(lk0, looks(lay))
        return out
    run("organize apply", org)
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


def phase_exact():
    """Revert must give back exactly the drawing it started from - geometry
    (Bezier vertices of imported splines too), attributes, groups, order."""
    for lay, steps in (("testschnitt", ("join",)), ("m2d_schnitt_polylinien", ("join", "simplify", "by_class")),
                       ("m2d_grundriss", ("join", "by_class")), ("plan_bloecke", ("join",)),
                       ("m2d_iso_schnitt", ("join", "simplify"))):
        def one(lay=lay, steps=steps):
            fp0 = fingerprint(lay)
            done, info = 0, {}
            for st in steps:
                try:
                    if st == "join":
                        r = res(cleanup.vw2d_join(layer=lay, dry_run=False, image=False))
                        info["join"] = pick(r, ("joined_pieces", "polylines", "objects_after", "applied"))
                        if not r.get("applied"):
                            continue
                    elif st == "simplify":
                        r = res(cleanup.vw2d_simplify(layer=lay, dry_run=False, image=False))
                        info["simplify"] = pick(r, ("objects", "vertices_before", "vertices_after", "applied"))
                        if not r.get("applied"):
                            continue
                    elif st == "by_class":
                        r = res(draw2d.vw2d_fix("by_class", layer=lay, image=False))
                        info["by_class"] = pick(r, ("changed", "fixed", "summary"))
                    done += 1
                except Exception as e:  # noqa: BLE001
                    info[st + "_error"] = repr(e)[:300]
            fp1 = fingerprint(lay)
            reverts = [pick(generic.vw_revert_last(), ("reverted_tool", "deleted", "restored", "missing"))
                       for _ in range(done)]
            fp2 = fingerprint(lay)
            return dict(info, changed=fp0 != fp1, reverts=reverts, compare=same(fp0, fp2))
        run("exact " + lay, one)


def phase_scenarios():
    """User wishes and edge cases."""
    lay, side = "testschnitt", [0.6, 1.2, 2.3, 6.0]
    # the profile drives the defaults ...
    for tol in (0.05, 1.0, 5.0):
        run("join tol %g mm" % tol, lambda tol=tol: pick(res(cleanup.vw2d_join(layer=lay, tolerance_mm=tol,
                                                                              dry_run=True, image=False)),
                                                         ("joined_pieces", "polylines", "objects_after")))
    run("join no curves", lambda: pick(res(cleanup.vw2d_join(layer=lay, include_curves=False, dry_run=True, image=False)),
                                       ("joined_pieces", "polylines", "objects_after", "not_joinable")))
    run("join through junctions", lambda: pick(res(cleanup.vw2d_join(layer=lay, through_junctions=True, dry_run=True,
                                                                     image=False)),
                                               ("joined_pieces", "polylines", "objects_after")))
    run("profile tolerance 3", lambda: {"rules": cleanup.vw2d_profile("set", values={"tolerance_mm": 3})["rules_now"]})
    run("join with profile tol 3", lambda: pick(res(cleanup.vw2d_join(layer=lay, dry_run=True, image=False)),
                                                ("tolerance_mm", "joined_pieces", "objects_after")))
    for paper in (0.05, 0.1, 0.5):
        def simp(paper=paper):
            cleanup.vw2d_profile("set", values={"simplify_paper_mm": paper})
            return pick(res(cleanup.vw2d_simplify(layer="m2d_schnitt_polylinien", bbox=side, image=False)),
                        ("tolerance_mm", "objects", "vertices_before", "vertices_after"))
        run("simplify paper %g mm" % paper, simp)
    # ... and an explicit request wins over the profile
    run("profile confirm_above 10", lambda: {"set": bool(cleanup.vw2d_profile("set", values={"confirm_above": 10}))})
    run("join default -> dry (profile)", lambda: pick(res(cleanup.vw2d_join(layer=lay, image=False)),
                                                     ("dry_run", "applied", "note")))
    run("join dry_run=False wins", lambda: pick(res(cleanup.vw2d_join(layer=lay, dry_run=False, image=False)),
                                               ("dry_run", "applied", "objects_after")))
    run("revert explicit join", lambda: dict(pick(generic.vw_revert_last(), ("reverted_tool", "restored", "missing")),
                                             count_now=count(lay)))
    # own rules land in the instructions every chat starts with
    def rules_of(out):
        return next((row["value"] for row in out["settings"] if row["key"] == "rules"), None)
    run("rule add", lambda: {"rules": rules_of(cleanup.vw2d_profile("add_rule", rule="Bestand und Ruinen organisch "
                                                                                     "lassen, nur vereinfachen"))})
    run("rule in instructions", lambda: {"in_instructions": "Ruinen organisch" in app.build_instructions(profile.load()),
                                         "tolerance_text": [ln for ln in app.build_instructions(profile.load())
                                                            .splitlines() if "tolerance_mm=3" in ln][:2]})
    run("rule remove", lambda: {"rules": rules_of(cleanup.vw2d_profile("remove_rule", index=1))})
    run("profile wrong value", lambda: expect_error(lambda: cleanup.vw2d_profile("set", values={"detail": "egal"}),
                                                    "detail"))
    run("profile negative", lambda: expect_error(lambda: cleanup.vw2d_profile("set", values={"confirm_above": -5}),
                                                 "confirm_above"))
    run("profile reset", lambda: {"settings": cleanup.vw2d_profile("reset")["settings"]})
    # wrong input says what is wrong
    run("join unknown layer", lambda: expect_error(lambda: cleanup.vw2d_join(layer="gibts_nicht", dry_run=True), ))
    run("import missing file", lambda: expect_error(lambda: cleanup.vw2d_import(path=T + "fehlt.dwg"), "not found"))
    run("import 3dm", lambda: expect_error(lambda: cleanup.vw2d_import(path=T + "testschnitt.3dm")))
    run("simplify tolerance 0", lambda: expect_error(lambda: cleanup.vw2d_simplify(layer=lay, tolerance_mm=0)))
    run("group one object", lambda: expect_error(lambda: cleanup.vw2d_group(layer=lay, bbox=[0, 0, 0.0001, 0.0001]),
                                                 "at least two"))
    # group class: the members' class, not the active one
    def grp():
        r = cleanup.vw2d_group(layer="m2d_schnitt_polylinien", bbox=side, per_class=True, name="Seite")
        out = {"groups": [pick(g, ("name", "class")) for g in r["groups"]][:6], "objects": r["objects"]}
        out["revert"] = pick(generic.vw_revert_last(), ("reverted_tool", "restored", "missing"))
        return out
    run("group per class", grp)
    def grp2():
        r = cleanup.vw2d_group(layer="m2d_schnitt_polylinien", bbox=side, name="Seite", group_class="Ansicht")
        out = {"groups": [pick(g, ("name", "class")) for g in r["groups"]]}
        out["revert"] = pick(generic.vw_revert_last(), ("reverted_tool", "restored", "missing"))
        return out
    run("group own class", grp2)
    # organize by touching an area (a curtain crossing the frame)
    run("organize touches dry", lambda: pick(organize.vw2d_organize(
        rules=[{"match": {"touches": side}, "class": "Vorhang"}], layer="m2d_schnitt_polylinien"),
        ("to_change", "unmatched", "targets")))
    # pictures for the user
    run("preview bbox", lambda: {"objects": res(draw2d.vw2d_preview(layer="m2d_schnitt_polylinien", bbox=side,
                                                                    marks=False, width_px=1200),
                                                "preview_side_bbox").get("objects")})
    run("preview drawing marks", lambda: res(draw2d.vw2d_preview(layer=lay, preset="drawing", width_px=1400),
                                             "preview_drawing_marks")["summary"])


def phase_vectorview():
    """VectorView export (Keystone for Rhino) into Vectorworks."""
    f = T + "vv_schnitt.dwg"
    if not os.path.exists(f):
        log("vectorview", ok=False, error="no vv_schnitt.dwg")
        return
    state = {}

    def imp():
        out = cleanup.vw2d_import(path=f)
        state["layer"] = (out.get("imported") or {}).get("new_layers", [None])[0]
        return pick(out, ("imported", "source", "objects", "kinds", "classes_total", "lines", "polylines",
                          "joinable", "looks_generated", "next_steps"))
    run("vv import", imp)
    lay = state.get("layer")
    if not lay:
        return
    run("vv check full", lambda: pick(res(draw2d.vw2d_check(layer=lay, image=False, max_issues=1))["summary"],
                                      ("errors", "warnings", "infos")))
    run("vv check drawing", lambda: (lambda d: dict(pick(d["summary"], ("errors", "warnings", "by_kind")),
                                                    issues=[pick(i, ("kind", "message")) for i in d["issues"]]))(
        res(draw2d.vw2d_check(layer=lay, image=False, max_issues=5, preset="drawing"))))
    run("vv preview", lambda: {"objects": res(draw2d.vw2d_preview(layer=lay, marks=False, width_px=1600),
                                              "vv_import").get("objects")})
    fp0 = fingerprint(lay)
    run("vv join", lambda: pick(res(cleanup.vw2d_join(layer=lay, dry_run=False), "vv_join"),
                                ("joined_pieces", "polylines", "closed", "objects_after", "applied")))
    run("vv simplify dry", lambda: pick(res(cleanup.vw2d_simplify(layer=lay), "vv_simplify"),
                                        ("tolerance_mm", "objects", "vertices_before", "vertices_after")))
    def simp():
        out = pick(res(cleanup.vw2d_simplify(layer=lay, dry_run=False, image=False)),
                   ("tolerance_mm", "objects", "vertices_before", "vertices_after", "applied"))
        state["applied"] = state.get("applied", 1) + (1 if out.get("applied") else 0)
        return out
    state["applied"] = 1  # the join
    run("vv simplify apply", simp)
    run("vv check after simplify", lambda: pick(res(draw2d.vw2d_check(layer=lay, image=False, max_issues=1,
                                                                      preset="drawing"))["summary"],
                                                ("errors", "warnings", "by_kind")))

    def bycls():
        lk0 = looks(lay)
        n0 = len(J.path.read_text(encoding="utf-8").splitlines()) if J.path.exists() else 0
        r = res(draw2d.vw2d_fix("by_class", layer=lay, image=False))
        n1 = len(J.path.read_text(encoding="utf-8").splitlines()) if J.path.exists() else 0
        state["applied"] += 1 if n1 > n0 else 0
        return {"journaled": n1 > n0, "by_class_looks": (r.get("applied") or {}).get("by_class_looks"),
                "look_kept": same(lk0, looks(lay))}
    run("vv by_class", bycls)
    run("vv check after cleanup", lambda: pick(res(draw2d.vw2d_check(layer=lay, image=False, max_issues=1,
                                                                     preset="drawing"))["summary"],
                                               ("errors", "warnings", "by_kind")))
    run("vv preview after cleanup", lambda: {"objects": res(draw2d.vw2d_preview(layer=lay, marks=False, width_px=1600),
                                                            "vv_cleaned").get("objects")})
    run("vv revert cleanup", lambda: dict(
        steps=[pick(generic.vw_revert_last(), ("reverted_tool", "deleted", "restored"))
               for _ in range(state.get("applied", 1))],
        compare=same(fp0, fingerprint(lay))))
    run("vv revert import", lambda: pick(generic.vw_revert_last(), ("reverted_tool", "deleted", "missing")))


def phase_diffs():
    """Where an inexact revert differs (one tool at a time)."""
    def cycle(lay, step, fn):
        def one():
            fp0 = fingerprint(lay)
            r = fn()
            rv = pick(generic.vw_revert_last(), ("reverted_tool", "deleted", "restored", "missing"))
            return {"result": r, "revert": rv, "compare": same(fp0, fingerprint(lay))}
        run("diff %s %s" % (step, lay), one)
    join = lambda lay: lambda: pick(res(cleanup.vw2d_join(layer=lay, dry_run=False, image=False)),  # noqa: E731
                                    ("joined_pieces", "applied"))
    for lay in ("m2d_iso_schnitt", "m2d_grundriss", "testschnitt"):
        cycle(lay, "join", join(lay))
    cycle("m2d_grundriss", "by_class",
          lambda: pick(res(draw2d.vw2d_fix("by_class", layer="m2d_grundriss", image=False)), ("summary",)))
    out = cleanup.vw2d_import(path=T + "vv_schnitt.dwg")
    lay = out["imported"]["new_layers"][0]
    cycle(lay, "join", join(lay))
    run("diff vv revert import", lambda: pick(generic.vw_revert_last(), ("reverted_tool", "deleted")))


def phase_simpdiag():
    """Seams between neighbouring fills after simplify (VectorView), server side only."""
    from vwmcp.geom import checks as K
    from vwmcp.geom.shapes import units_tolerances
    from vwmcp.geom.simplify import plan_simplify
    out = cleanup.vw2d_import(path=T + "vv_schnitt.dwg")
    lay = out["imported"]["new_layers"][0]
    data = draw2d._read(layer=lay, geometry=True, allow_temp=True, limit=200000)
    units = data["units"]
    tol = units_tolerances(units, 10.0)["tol"]

    def score(plan):
        changes = [{"uuid": c["uuid"], "verts": c["verts"], "closed": c["closed"], "holes": []} for c in plan["changes"]]
        after = cleanup._after_records(data["objects"], changes, [])
        r = K.run_checks(after, units, 1.0, checks=["overlap"], none_class=data["none_class"], compare="class",
                         overlap_fills_only=True)
        worst = sorted(r["issues"], key=lambda i: -(i.get("thickness_mm") or 0))[:3]
        return {"summary": pick(r["summary"], ("errors", "warnings")), "vertices": [plan["vertices_before"],
                plan["vertices_after"]], "objects": plan["objects"],
                "worst": [pick(i, ("thickness_mm", "area", "at", "message")) for i in worst]}
    run("diag base", lambda: score({"changes": [], "vertices_before": 0, "vertices_after": 0, "objects": 0}))
    t0 = time.time()
    seam = plan_simplify(data["objects"], tol, min_vertices=8)
    run("diag seamless", lambda: dict(score(seam), seconds=round(time.time() - t0, 2)))
    ind = {"changes": [], "vertices_before": 0, "vertices_after": 0, "objects": 0}
    for r in data["objects"]:
        p = plan_simplify([r], tol, min_vertices=8)
        ind["changes"] += p["changes"]
        ind["vertices_before"] += p["vertices_before"]
        ind["vertices_after"] += p["vertices_after"]
        ind["objects"] += p["objects"]
    run("diag independent", lambda: score(ind))
    run("diag revert import", lambda: pick(generic.vw_revert_last(), ("reverted_tool", "deleted")))


PHASES = {"stock": phase_stock, "import": phase_import, "check": phase_check, "join": phase_join,
          "simplify": phase_simplify, "organize": phase_organize_group, "profile": phase_profile, "big": phase_big,
          "workflow": phase_workflow, "exact": phase_exact, "scenarios": phase_scenarios,
          "vectorview": phase_vectorview, "diffs": phase_diffs,
          "simpdiag": phase_simpdiag}

if __name__ == "__main__":
    log("start", phases=sys.argv[1:], version=__import__("vwmcp").__version__)
    for p in sys.argv[1:]:
        try:
            PHASES[p]()
        except Exception as e:  # noqa: BLE001
            log("phase " + p, ok=False, error=repr(e), tb=traceback.format_exc()[-1500:])
    log("done")
