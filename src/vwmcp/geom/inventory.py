"""Stock-taking of an imported or existing drawing (server side, pure):
what is there, how fragmented, and what to do next."""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from vwmcp.geom.join import plan_join

GENERATED_HINTS = ("make2d", "vectorview", "clipping", "silhouette", "hidden", "visible", "draftboard")


def _length(rec: dict) -> float | None:
    if rec.get("kind") == "line" and rec.get("p1"):
        return math.hypot(rec["p2"][0] - rec["p1"][0], rec["p2"][1] - rec["p1"][1])
    return None


def take_stock(records: list[dict], mpu: float, join_tol: float, source: dict | None = None,
               arrived_by_layer: dict | None = None) -> dict[str, Any]:
    """records: read_objects records (geometry on). mpu = mm per document unit."""
    kinds = Counter(r["kind"] for r in records)
    classes = Counter(r.get("class") for r in records if r["kind"] != "group")
    lines = [r for r in records if r["kind"] == "line"]
    short1 = sum(1 for r in lines if (_length(r) or 1) * mpu < 1.0)
    short5 = sum(1 for r in lines if (_length(r) or 1) * mpu < 5.0)
    polys = [r for r in records if r["kind"] in ("polygon", "polyline")]
    open_polys = [r for r in polys if not r.get("closed", True)]
    curve_polys = [r for r in polys if any(int(v[2]) in (1, 2, 4) for v in r.get("vertices") or [])]
    verts = sum(len(r.get("vertices") or []) for r in polys)
    explicit = sum(1 for r in records if r.get("attrs") and not all((r["attrs"].get("by_class") or {}).values()))
    xs = [b for r in records if r.get("bbox") for b in (r["bbox"][0], r["bbox"][2])]
    ys = [b for r in records if r.get("bbox") for b in (r["bbox"][1], r["bbox"][3])]
    size_m = [round((max(xs) - min(xs)) * mpu / 1000.0, 3), round((max(ys) - min(ys)) * mpu / 1000.0, 3)] if xs else None
    jp = plan_join(records, join_tol)
    generated = any(any(h in (c or "").lower() for h in GENERATED_HINTS) for c in classes) or (
        len(lines) > 200 and len(lines) > 0.6 * max(1, len(records)))
    out: dict[str, Any] = {
        "objects": len(records),
        "kinds": dict(kinds.most_common()),
        "classes": dict(classes.most_common(10)),
        "classes_total": len(classes),
        "size_m": size_m,
        "lines": {"count": len(lines), "shorter_than_1mm": short1, "shorter_than_5mm": short5},
        "polylines": {"count": len(polys), "open": len(open_polys), "vertices": verts,
                      "with_curve_vertices": len(curve_polys)},
        "explicit_attributes": explicit,
        "joinable": {"pieces": jp["pieces_joined"], "into_polylines": jp["chains"],
                     "objects_after": len(records) - jp["pieces_joined"] + jp["chains"]},
        "looks_generated": bool(generated),
    }
    tips = []
    if source and source.get("countable"):
        exp = source.get("total", 0)
        got = sum((arrived_by_layer or {}).values()) if arrived_by_layer else len([r for r in records
                                                                                     if not r.get("parent")])
        out["source"] = {"entities": exp, "arrived": got, "missing": max(0, exp - got),
                         "rational_splines": source.get("rational_splines", 0),
                         "splines": source.get("splines", 0), "version": source.get("version")}
        if source.get("version") == "R12":
            tips.append("R12-DXF: Geschwungene Kurven (Splines) fehlen schon in der Datei – Rhino lässt sie beim "
                        "R12-Export weg. In Rhino als DWG 2018 mit „Splines und Polykurven als Polylinien“ neu "
                        "exportieren.")
        if exp > got:
            if source.get("rational_splines"):
                tips.append("Es fehlen %d Objekte, darunter %d gewichtete Splines (Kreise, Ellipsen, Ringe). In "
                            "Rhino mit „Splines als Polylinien“ neu exportieren, dann kommt alles an."
                            % (exp - got, source["rational_splines"]))
            else:
                tips.append("Es fehlen %d Objekte gegenüber der Datei." % (exp - got))
    elif source and source.get("format") == "DWG":
        tips.append("DWG lässt sich nicht nachzählen. Fehlen Kreise, Ringe oder Ellipsen aus Rhino: dort mit "
                    "„Splines als Polylinien“ neu exportieren (Vectorworks übernimmt gewichtete Splines nicht).")
    if generated:
        tips.append("Erzeugte Zeichnung (Make2D/VectorView/Export): lose Einzelstücke, Umrisse auf Füllflächen "
                    "und Fugen zwischen Stücken sind normal – mit vw2d_check(preset=\"drawing\") prüfen.")
    if jp["chains"]:
        tips.append("%d Einzelstücke lassen sich zu %d Linienzügen verbinden (vw2d_join)."
                    % (jp["pieces_joined"], jp["chains"]))
    if verts > 2000:
        tips.append("%d Punkte in Polylinien – organische Formen lassen sich mit vw2d_simplify ausdünnen."
                    % verts)
    if explicit:
        tips.append("%d Objekte haben eigene Attribute statt „nach Klasse“ – Linienstärken lassen sich erst "
                    "über Klassen steuern, wenn sie auf „nach Klasse“ stehen (vw2d_organize by_class)." % explicit)
    out["next_steps"] = tips
    return out
