#!/usr/bin/env python3
"""Build Keystone's planning knowledge base (src/vwmcp/planning/data/*.json)
from the extraction notes of the four Raumpilot books.

    python tools/build_planning_kb.py <notes dir with *.json>

The notes (one JSON per book section, facts in own words with PDF page
numbers, see the schema the extraction followed) are not part of the repo.
This script curates them: element kinds for drawing, VDI 6000 side
distances as a matrix, movement areas in Keystone's form, printed page
numbers, merged rules/rooms/layouts. It prints a report and checks every
standard layout with the planning engine (transcription errors show up as
problems).
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
OUT = os.path.join(HERE, "..", "src", "vwmcp", "planning", "data")

BOOK_OF = {"g": "G", "w": "W", "a": "A", "l": "L"}  # by agent id prefix
OFFSET = 2  # printed page = PDF page - 2 in all four volumes

# element id -> (kind, params); first matching regex wins
KINDS = [
    (r"^aufzug_\d|^aufzug_15|aufzugsschacht|fahrkorb|aufzug_kabine", "lift", {}),
    (r"abfallbehaelter|abfalleimer|papiereimer|muelltonne", "bin", {}),
    (r"wandtafel|^tafel|pinnwand", "board", {}),
    (r"matratze|liege", "bed", {}),
    (r"werkbank|arbeitsplatzinsel", "desk", {}),
    (r"spind|garderobe", "wardrobe", {}),
    (r"ausgussbecken|wasserbecken", "basin", {}),
    (r"^doppelwaschtisch$|^einbauwaschtisch_2$", "basin", {"bowls": 2}),
    (r"waschtisch|waschbecken|^handwaschbecken", "basin", {}),
    (r"^bidet$", "bidet", {}),
    (r"^wc_vorwand$", "wc", {"wall_hung": False}),
    (r"^wc", "wc", {}),
    (r"^urinal", "urinal", {}),
    (r"^duschplatz", "shower", {"drain": "center", "floor": True}),
    (r"dusch", "shower", {}),
    (r"badewanne", "bath", {}),
    (r"waschmaschine|trockner|waeschetrockner", "drum", {}),
    (r"doppelbeckenspuele", "sink", {"bowls": 2}),
    (r"spuele|spuelzentrum", "sink", {"bowls": 1}),
    (r"herd|kochfeld", "hob", {}),
    (r"kuehl|gefrier", "fridge", {}),
    (r"hochschrank|backofen_schrank|einbaubackofen", "tall", {}),
    (r"unterschrank|arbeitsflaeche|abstellflaeche|abtropfflaeche|geraetestell|geschirrspueler|essbar|theke|kuechenzeile", "counter", {}),
    (r"oberschrank|haengeschrank|mikrowelle", "box", {}),
    (r"schreibtisch|arbeitstisch|buerotisch|schuelertisch|lehrertisch", "desk", {}),
    (r"esstisch_rund|tisch_rund", "table", {"round": True}),
    (r"tisch", "table", {}),
    (r"stuhl|hocker", "chair", {}),
    (r"sitzbank|bank$|gedeck", "box", {}),
    (r"doppelbett|bett", "bed", {}),
    (r"nachttisch|nachtkonsole", "box", {}),
    (r"kleiderschrank|schrank", "wardrobe", {}),
    (r"kommode|sideboard|regal|container", "shelf", {}),
    (r"sofa|couch", "sofa", {}),
    (r"sessel", "armchair", {}),
    (r"fluegel", "piano", {"grand": True}),
    (r"klavier", "piano", {}),
    (r"rollstuhl_bemessung|wendekreis", "wheelchair", {}),
    (r"stellplatz|parkstand", "parking", {}),
    (r"pkw|transporter|fahrzeug|bus|lkw", "car", {}),
    (r"fahrrad|moped|kraftrad", "bicycle", {}),
    (r"person|rollstuhl|rollator|kinderwagen|buggy|krankentrage", "person", {}),
]
# elements that don't stand at a wall
FREE_KINDS = {"table", "chair", "wheelchair", "armchair", "piano"}
FREE_CATS = {"person", "verkehr"}
FREE = re.compile(r"rollator|kinderwagen|buggy|fahrrad|pkw|bus|fahrzeug|krankentrage|billard|kicker|insel")
# not placeable as an element (accessories, tables of sizes, places)
SKIP = re.compile(r"^stuetzklappgriff$|tabelle|^handlauf$")
# side-distance codes (matrix of VDI 6000 Blatt 1, Raumpilot Grundlagen S. 266)
SHORT = {"WB": "WB", "DWB": "DWB", "EWT": "EWT", "EDWT": "EDWT", "HWB": "HWB", "SWB": "SW", "SW": "SW",
         "WCa": "WC", "WCu": "WC", "UB": "UR", "UR": "UR", "DU": "DU", "BW": "BW", "WM": "WM/TR", "TR": "WM/TR"}
CODE_NORM = {"WCa/WCu": "WC", "WCa": "WC", "WCu": "WC", "WC": "WC", "WM/TR": "WM/TR", "WM": "WM/TR",
             "TR": "WM/TR", "SW": "SW", "SWB": "SW", "UR": "UR", "UB": "UR", "Wand": "Wand"}


def num(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.match(r"^\s*(\d+(?:[.,]\d+)?)", v)
        if m:
            return float(m.group(1).replace(",", "."))
    return None


def pages(v) -> list[int]:
    if isinstance(v, int):
        return [v]
    if isinstance(v, list):
        return [p for p in v if isinstance(p, int)]
    return []


def sources(book: str, page_v) -> list[dict]:
    return [{"book": book, "page": p - OFFSET} for p in sorted(set(pages(page_v)))]


def slug(s: str) -> str:
    s = s.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:40] or "v"


def kind_of(eid: str):
    for rx, kind, params in KINDS:
        if re.search(rx, eid):
            return kind, dict(params)
    return "box", {}


def areas_from(bf, w) -> list[dict]:
    if not isinstance(bf, dict):
        return []
    d = num(bf.get("d"))
    if d is None:
        return []
    pos = str(bf.get("position") or "front").lower()
    side = "front"
    for s in ("left", "right", "back"):
        if pos.startswith(s):
            side = s
    a = {"side": side, "d": d}
    bw = num(bf.get("w"))
    if bw is not None and side in ("front", "back"):
        a["w"] = bw
    x = num(bf.get("d_opposite"))
    if x is not None and x > d:
        a["d_extra"] = x
    if bf.get("notes"):
        a["note"] = str(bf["notes"])[:200]
    return [a]


def bf_areas(barrier_free, key) -> list[dict] | None:
    if not isinstance(barrier_free, dict):
        return None
    b = barrier_free.get(key)
    if not isinstance(b, dict):
        return None
    bf = b.get("bewegungsflaeche")
    if not isinstance(bf, dict):
        return None
    w, d = num(bf.get("w")), num(bf.get("d"))
    if w is None or d is None:
        return None
    return [{"side": "front", "w": w, "d": d, "note": str(b.get("notes", ""))[:200]}]


def load_notes(src: str) -> list[tuple[str, dict]]:
    out = []
    for f in sorted(glob.glob(os.path.join(src, "*.json"))):
        name = os.path.basename(f)[:-5]
        if not re.fullmatch(r"[a-z]+\d*b?", name):
            continue
        try:
            out.append((name, json.load(open(f, encoding="utf-8"))))
        except ValueError as e:
            print("skipping invalid", f, e)
    return out


def build(src: str) -> dict:
    notes = load_notes(src)
    elements, rules, rooms, layouts, matrix, style_notes = {}, [], {}, {}, {}, []
    report = {"files": [n for n, _ in notes]}
    for agent, d in notes:
        book = BOOK_OF[agent[0]]
        for e in d.get("elements", []):
            eid = e["id"]
            sf = e.get("stellflaeche") or {}
            w, dd = num(sf.get("w")), num(sf.get("d"))
            if SKIP.search(eid) or w is None or dd is None or w <= 0 or dd <= 0:
                continue
            kind, params = kind_of(eid)
            short = SHORT.get(e.get("short") or "")
            el = {"id": eid, "name": e.get("name", eid).split(" (")[0][:48], "full_name": e.get("name", eid),
                  "category": e.get("category") or "sonstiges", "context": e.get("context") or [],
                  "kind": kind, "w": w, "d": dd,
                  "wall": not (FREE.search(eid) or kind in FREE_KINDS or e.get("category") in FREE_CATS)}
            if sf.get("h") is not None:
                el["h"] = num(sf.get("h"))
            if short:
                el["short"] = short
            if params:
                el["params"] = params
            areas = areas_from(e.get("bewegungsflaeche"), w)
            if areas:
                el["areas"] = areas
            for key, field in (("barrierefrei", "areas_bf"), ("rollstuhlgerecht", "areas_rg")):
                a = bf_areas(e.get("barrier_free"), key)
                if a:
                    el[field] = a
            vs = []
            for v in e.get("stellflaeche_variants") or []:
                vw, vd = num(v.get("w")), num(v.get("d"))
                if vw is None or vd is None:
                    continue
                vid = "%gx%g" % (vw, vd)
                if any(x["id"] == vid for x in vs) or (vw == w and vd == dd):
                    continue
                vs.append({"id": vid, "label": str(v.get("label", ""))[:80], "w": vw, "d": vd})
            if vs:
                el["variants"] = vs
            if e.get("heights"):
                el["heights"] = e["heights"]
            if e.get("barrier_free"):
                el["barrier_free"] = e["barrier_free"]
            if e.get("side_clearance"):
                el["side_clearance"] = e["side_clearance"]
            if e.get("notes"):
                el["notes"] = str(e["notes"])[:400]
            el["sources"] = sources(book, e.get("page"))
            if kind in ("shower", "bath") or (short in ("EWT", "EDWT")):
                el["corner_ok"] = True
            if re.match(r"^duschplatz", eid):
                el["walkable"] = True  # floor-level: is itself movement area
                el.pop("areas", None)
            if el["category"] == "kueche" and kind in ("counter", "sink", "hob", "fridge", "tall", "box"):
                el["group"] = "kuechenzeile"
            if kind in ("table", "chair"):
                el.pop("areas", None)  # dining: the Essplatz (table + chairs) carries the areas
            if kind in ("parking", "car", "bicycle", "person", "wheelchair"):
                for k in ("areas", "areas_bf", "areas_rg"):  # traffic: aisles/turning are rules, not areas
                    el.pop(k, None)
            for a in el.get("areas_bf", []) + el.get("areas_rg", []):
                a["align"] = "any"  # DIN 18040: the area must be in front, not centred
            if kind == "bath":
                for a in el.get("areas", []):
                    a["align"] = "any"
                for a in el.get("areas_bf", []) + el.get("areas_rg", []):
                    if a["d"] > a["w"]:  # given as length x depth: along the tub x in front
                        a["w"], a["d"] = a["d"], a["w"]
            if kind == "bed" and el["category"] in ("schlafen", "kinder"):
                # Raumpilot Grundlagen S. 235/246: 70 (recommended 90) at the access sides and the foot;
                # double beds both long sides; S. 238/239: wheelchair 150 / 120, barrier-free 120 / 90
                el["overlap_ok"] = ["nachttisch", "nachtkonsole"]  # S. 235: nightstands stand in this zone
                double = w >= 140 and not eid.startswith(("kinder", "klein", "hoch", "alkoven"))
                note = "mind. 70, empfohlen 90"
                if double:
                    el["areas"] = [{"side": "left", "d": 70.0, "note": note}, {"side": "right", "d": 70.0, "note": note},
                                   {"side": "front", "d": 70.0, "note": note}]
                    el["areas_rg"] = [{"side": "left", "d": 120.0}, {"side": "right", "d": 120.0},
                                      {"side": "either", "d": 150.0, "note": "eine Längsseite 150, die andere 120"}]
                    el["areas_bf"] = [{"side": "left", "d": 90.0}, {"side": "right", "d": 90.0},
                                      {"side": "either", "d": 120.0, "note": "eine Längsseite 120, die andere 90"}]
                else:
                    el["areas"] = [{"side": "either", "d": 70.0, "note": note}, {"side": "front", "d": 70.0, "note": note}]
                    el["areas_rg"] = [{"side": "either", "d": 150.0}]
                    el["areas_bf"] = [{"side": "either", "d": 120.0}]
            if kind in ("hob", "sink"):
                el["overlap_ok"] = ["arbeitsinsel", "kueche_unterschrank", "arbeitsflaeche_klein",
                                    "arbeitsflaeche_gross"]
            if kind == "shower" and el.get("areas"):
                # Raumpilot S. 266: in front of the shower at least 75 x 90, entry >= 70
                el["areas"] = [{"side": "front", "w": 90.0, "d": 75.0, "align": "any",
                                "note": "Bewegungsraum vor der Dusche mind. 75 × 90 cm, Einstieg ≥ 70 cm"}]
            if eid in elements:
                # keep the first, merge sources
                elements[eid]["sources"] += el["sources"]
            else:
                elements[eid] = el
        for c in d.get("clearances", []):
            if c.get("kind") not in ("side", "wall") or "VDI 6000" not in str(c.get("condition", "")):
                continue
            a = CODE_NORM.get(c["a"], c["a"])
            b = CODE_NORM.get(c["b"], c["b"])
            v = num(c.get("distance"))
            if v is None:
                continue
            matrix.setdefault(a, {})[b] = v
        for r in d.get("rules", []):
            rr = {"id": "%s.%s" % (agent, r.get("id") or slug(r.get("rule", ""))), "topic": r.get("topic", "allgemein"),
                  "title": r.get("rule", ""), "sources": sources(book, r.get("page"))}
            for k in ("value", "table", "condition", "norm", "notes"):
                if r.get(k) not in (None, "", [], {}):
                    rr["values" if k == "value" else k] = r[k]
            rules.append(rr)
        for c in d.get("clearances", []):  # non-matrix distances become rules
            if c.get("kind") in ("side", "wall") and "VDI 6000" in str(c.get("condition", "")):
                continue
            rules.append({"id": "%s.abstand.%s_%s" % (agent, slug(c["a"]), slug(c["b"])), "topic": "abstand",
                          "title": "Abstand %s – %s (%s)" % (c["a"], c["b"], c.get("kind", "")),
                          "values": {"min": num(c.get("distance")), "unit": "cm"} if num(c.get("distance")) is not None
                          else c.get("distance"), "condition": c.get("condition"), "notes": c.get("notes"),
                          "sources": sources(book, c.get("page"))})
        for r in d.get("rooms", []):
            rid = r["id"]
            ro = {"id": rid, "name": r.get("name", rid), "context": r.get("context") or [],
                  "min": r.get("min"), "typical": r.get("typical"), "requires": r.get("requires") or [],
                  "rules": r.get("rules") or [], "sources": sources(book, r.get("page"))}
            if r.get("barrier_free"):
                ro["barrier_free"] = r["barrier_free"]
            if rid in rooms:
                rooms[rid]["rules"] += ro["rules"]
                rooms[rid]["sources"] += ro["sources"]
            else:
                rooms[rid] = ro
        for lay in d.get("layouts", []):
            W, D = num(lay.get("W")), num(lay.get("D"))
            els = []
            ok = W is not None and D is not None
            for x in lay.get("elements", []):
                vals = [num(x.get(k)) for k in ("x", "y", "w", "d")]
                if any(v is None for v in vals):
                    ok = False
                    break
                against = x.get("against") if x.get("against") in ("N", "E", "S", "W") else None
                if against is None and x.get("faces") in ("N", "E", "S", "W") and W is not None and D is not None:
                    # back to the wall it touches (the book draws tubs/showers at walls without saying so)
                    back = {"N": "S", "S": "N", "E": "W", "W": "E"}[x["faces"]]
                    touch = {"S": vals[1], "N": D - (vals[1] + vals[3]), "W": vals[0], "E": W - (vals[0] + vals[2])}[back]
                    if abs(touch) < 0.5:
                        against = back
                els.append({"element": x["element"], "x": vals[0], "y": vals[1], "w": vals[2], "d": vals[3],
                            "against": against, "faces": x.get("faces")})
            low = (lay["id"] + " " + lay.get("name", "")).lower()
            mode = ("rollstuhlgerecht" if re.search(r"rollstuhl|^rf_", low) else
                    "barrierefrei" if re.search(r"barrierefrei|^bf_", low) else "standard")
            lo = {"id": lay["id"], "room": lay.get("room"), "name": lay.get("name", lay["id"]), "mode": mode,
                  "W": W, "D": D, "barrier_free": bool(lay.get("barrier_free")), "elements": els,
                  "movement": lay.get("bewegungsflaechen") or [], "doors": lay.get("doors") or [],
                  "dims": lay.get("dims"), "notes": lay.get("notes"), "sources": sources(book, lay.get("page"))}
            if not ok or not els:
                lo["reference_only"] = True  # geometry not complete enough to place
            if agent.startswith("l2"):
                # measured from real school projects (no dimensioned drawings in that part)
                lo["reference_only"], lo["measured"] = True, True
                if lay.get("metrics"):
                    lo["metrics"] = lay["metrics"]
            layouts[lay["id"]] = lo
        for s in d.get("style", []):
            style_notes.append({"agent": agent, "note": s})
    # room requirements -> element ids (the notes use short names)
    def match(name: str):
        n = slug(name)
        if n in elements:
            return n
        cands = [i for i in elements if i.startswith(n) or n in i]
        return sorted(cands, key=len)[0] if cands else None
    for r in rooms.values():
        req, unknown = [], []
        for x in r.get("requires") or []:
            m = match(str(x))
            (req if m else unknown).append(m or x)
        r["requires"] = req
        if unknown:
            r["requires_text"] = unknown
    # dining places (Raumpilot Grundlagen S. 215): table + chairs as one element per number of persons
    src_e = next((e for _, d in notes for e in d.get("elements", []) if e["id"] == "essplatz_stellflaeche_tabelle"), None)
    if src_e:
        for v in src_e.get("stellflaeche_variants", []):
            n = int(re.match(r"(\d+)", v["label"]).group(1))
            eid = "essplatz_%dp" % n
            elements[eid] = {
                "id": eid, "name": "Essplatz %d Personen" % n, "category": "essen", "context": ["wohnen"],
                "kind": "dining", "w": float(v["w"]), "d": float(v["d"]), "wall": False,
                "params": {"persons": n, "table_d": 80.0},
                "areas": [{"side": "front", "d": 30.0, "d_extra": 70.0,
                           "note": "hinter der Stuhlzone 30 cm zur Wand, 70 cm zu Möbeln"},
                          {"side": "back", "d": 30.0, "d_extra": 70.0,
                           "note": "hinter der Stuhlzone 30 cm zur Wand, 70 cm zu Möbeln"}],
                "notes": "Tisch 80 tief, Stuhlzone je Seite 50 (Stell- und Bewegungsfläche der Stühle); "
                         "Gedeck 60 × 40, Ansitzbreite 65-70 je Person; Tischhöhe 70-75, Sitzhöhe 42-47",
                "sources": sources("G", src_e.get("page"))}
    # elements only known from layouts (island, sideboard, lift car ...): minimal entries
    for lay in layouts.values():
        for x in lay["elements"]:
            if x["element"] in elements or SKIP.search(x["element"]):
                continue
            kind, params = kind_of(x["element"])
            lw, ld = (x["w"], x["d"]) if x.get("against") in (None, "N", "S") else (x["d"], x["w"])
            elements[x["element"]] = {"id": x["element"], "name": x["element"].replace("_", " ").capitalize(),
                                      "category": "kueche" if "kueche" in (lay.get("room") or "") else "sonstiges",
                                      "kind": kind, "w": lw, "d": ld, "wall": x.get("against") is not None,
                                      "derived": True, "catalog": False, "sources": list(lay["sources"]),
                                      "notes": "nur aus einem Standardgrundriss bekannt (%s)" % lay["id"]}
            if params:
                elements[x["element"]]["params"] = params
    report.update({"elements": len(elements), "rules": len(rules), "rooms": len(rooms), "layouts": len(layouts),
                   "matrix_rows": sorted(matrix)})
    return {"elements": list(elements.values()), "rules": rules, "rooms": list(rooms.values()),
            "layouts": list(layouts.values()), "clearances": {"sanitary_sides": matrix, "door_reveal": 10.0,
                                                               "note": "VDI 6000 Blatt 1, Raumpilot Grundlagen S. 266; "
                                                                       "Abstand zu Türlaibungen ≥ 10 cm"},
            "style_notes": style_notes, "report": report}


def write(kb: dict) -> None:
    os.makedirs(OUT, exist_ok=True)
    for name in ("elements", "rules", "rooms", "layouts", "clearances"):
        with open(os.path.join(OUT, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(kb[name], f, ensure_ascii=False, indent=0, sort_keys=False)
            f.write("\n")


def check_layouts() -> None:
    from vwmcp.planning import engine as E
    from vwmcp.planning import kb

    for name in ("elements", "clearances", "rooms", "layouts", "rules"):
        getattr(kb, name).cache_clear()
    bad, ref = 0, []
    for lay in kb.layouts().values():
        if lay.get("reference_only"):
            continue
        mode = lay.get("mode") or "standard"
        f = kb.fit(lay, lay["W"], lay["D"], mode=mode)
        if f is None:
            print("  cannot fit", lay["id"])
            continue
        iss = E.check(f.placed, f.W, f.D, f.doors, kb.sides(mode), kb.door_reveal())
        errs = [i for i in iss if i.severity == "error"]
        if errs:
            bad += 1
            print("  %-34s %s" % (lay["id"], "; ".join(i.message for i in errs[:4])))
            ref.append(lay["id"])  # kept as reference data; planning uses only layouts that pass
    print("layouts with problems:", bad, "- marked reference only:", len(ref))
    if ref:
        path = os.path.join(OUT, "layouts.json")
        data = json.load(open(path, encoding="utf-8"))
        for lay in data:
            if lay["id"] in ref:
                lay["reference_only"] = True
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=0)
            fh.write("\n")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "."
    kb_ = build(src)
    write(kb_)
    print(json.dumps(kb_["report"], ensure_ascii=False))
    check_layouts()
