"""Exact 2D drawing tools: read, draw, edit, check, fix, organise.

Geometry is computed on the server (pure Python + shapely); Vectorworks only
reads and writes objects. Every write is journaled for vw_revert_last.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from typing import Any, Literal

from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError

from vwmcp import app
from vwmcp.app import DELETE, READ, WRITE, mcp
from vwmcp.geom import checks as K
from vwmcp.geom import fixes as F
from vwmcp.geom import render as R
from vwmcp.geom import specs as S
from vwmcp.geom.shapes import units_tolerances

READ_TIMEOUT = 180.0
WRITE_TIMEOUT = 120.0

# A starting point for classes in architecture plans (German names; the
# user's own standard always wins). Attributes are used "nach Klasse".
TEMPLATES: dict[str, dict] = {
    "architektur": {
        "classes": [
            {"name": "Wand-Tragend", "attributes": {"pen": "#000000", "fill": "#000000", "line_weight": 0.5}},
            {"name": "Wand-Nichttragend", "attributes": {"pen": "#000000", "fill": "#7f7f7f", "line_weight": 0.35}},
            {"name": "Wand-Bestand", "attributes": {"pen": "#000000", "fill": "#bfbfbf", "line_weight": 0.35}},
            {"name": "Wand-Abbruch", "attributes": {"pen": "#c00000", "fill": "none", "line_weight": 0.25,
                                                    "line_style": "ISO-02 Strichlinie"}},
            {"name": "Stütze", "attributes": {"pen": "#000000", "fill": "#000000", "line_weight": 0.5}},
            {"name": "Decke-Schnitt", "attributes": {"pen": "#000000", "fill": "#000000", "line_weight": 0.5}},
            {"name": "Öffnung-Fenster", "attributes": {"pen": "#000000", "fill": "#ffffff", "line_weight": 0.25}},
            {"name": "Öffnung-Tür", "attributes": {"pen": "#000000", "fill": "#ffffff", "line_weight": 0.25}},
            {"name": "Treppe", "attributes": {"pen": "#000000", "fill": "#ffffff", "line_weight": 0.25}},
            {"name": "Boden-Belag", "attributes": {"pen": "#7f7f7f", "fill": "#ffffff", "line_weight": 0.13}},
            {"name": "Ausstattung-Möbel", "attributes": {"pen": "#404040", "fill": "#ffffff", "line_weight": 0.18}},
            {"name": "Ausstattung-Sanitär", "attributes": {"pen": "#404040", "fill": "#ffffff", "line_weight": 0.18}},
            {"name": "Gelände", "attributes": {"pen": "#7f7f7f", "fill": "none", "line_weight": 0.18}},
            {"name": "Umgebung-Bestand", "attributes": {"pen": "#7f7f7f", "fill": "#e6e6e6", "line_weight": 0.18}},
            {"name": "Achsen", "attributes": {"pen": "#7f7f7f", "fill": "none", "line_weight": 0.13,
                                              "line_style": "ISO-10 Strichpunktlinie"}},
            {"name": "Hilfslinien", "attributes": {"pen": "#00a0ff", "fill": "none", "line_weight": 0.1}},
            {"name": "Schraffur", "attributes": {"pen": "#000000", "fill": "none", "line_weight": 0.1}},
            {"name": "Beschriftung", "attributes": {"pen": "#000000", "fill": "none", "line_weight": 0.18}},
            {"name": "Bemaßung", "attributes": {"pen": "#000000", "fill": "none", "line_weight": 0.13}},
            {"name": "Plangrafik", "attributes": {"pen": "#000000", "fill": "none", "line_weight": 0.25}},
        ],
        "layers": [],
    },
}


# ---- helpers ------------------------------------------------------------------

def _read(uuids=None, layer=None, class_name=None, selection=False, geometry=True, allow_temp=True,
          limit=20000, bbox=None):
    return app.vw2d("read_objects", timeout=READ_TIMEOUT, uuids=uuids, layer=layer, cls=class_name,
                    selection=selection, geometry=geometry, allow_temp=allow_temp, limit=limit,
                    bbox=list(bbox) if bbox else None)


def _journal(document: str, tool: str, ops: list[dict]) -> None:
    if ops:
        app.get_journal().record(document, tool, ops)


def _units() -> dict:
    return app.vw2d("units")


def _mm(units: dict, v: float) -> float:
    return v * float(f"{units['mm_per_unit']:.12g}")


def _slim(rec: dict) -> dict:
    """Records without bulky internals for responses."""
    out = {k: v for k, v in rec.items() if k not in ("approx_polygon",)}
    if "vertices" in out and len(out["vertices"]) > 400:
        out["vertices"] = out["vertices"][:400]
        out["vertices_truncated"] = True
    return out


def _issue_out(it: dict, max_moves: int = 4) -> dict:
    o = {k: v for k, v in it.items() if k not in ("region", "moves")}
    if it.get("moves"):
        o["moves"] = it["moves"][:max_moves]
        if len(it["moves"]) > max_moves:
            o["moves_more"] = len(it["moves"]) - max_moves
    if len(o.get("uuids", [])) > 12:
        o["uuids_more"] = len(o["uuids"]) - 12
        o["uuids"] = o["uuids"][:12]
    return o


def _check(scope: dict, tolerance_mm: float, checks=None, compare="layer", ignore_classes=None):
    data = _read(**scope)
    res = K.run_checks(data["objects"], data["units"], tolerance_mm, checks=checks,
                       none_class=data["none_class"], compare=compare, ignore_classes=ignore_classes)
    return data, res


def _image(data, res, focus=None, title="", width_px=1600):
    t = units_tolerances(data["units"], 1.0)
    png = R.render(data["objects"], res["_shapes"], res["_all_issues"], t["chord"], focus=focus,
                   width_px=width_px, title=title or data.get("document", ""), mpu=t["mpu"])
    return Image(data=png, format="png")


def _scope(uuids, layer, class_name, selection, bbox=None) -> dict:
    return {"uuids": uuids or None, "layer": layer, "class_name": class_name, "selection": bool(selection),
            "bbox": list(bbox) if bbox else None}


def _new_issues(res, uuids) -> list[dict]:
    want = set(uuids)
    return [_issue_out(i) for i in res["_all_issues"] if want & set(i.get("uuids", []))
            and i["kind"] not in ("attributes", "approx")]


# ---- reading ----------------------------------------------------------------------

@mcp.tool(annotations=READ)
def vw2d_read(
    uuids: list[str] | None = None,
    layer: str | None = None,
    class_name: str | None = None,
    selection: bool = False,
    include_geometry: bool = True,
    limit: int = 2000,
    bbox: list[float] | None = None,
) -> dict:
    """Read 2D objects with their exact geometry and attributes.

    Scope: uuids, or selection=True, or layer ("*" = all design layers),
    optionally filtered by class_name. Default: the active layer. Geometry is
    exact, in document units: polygons/polylines give vertices
    [x, y, type, radius] (type 0 corner, 3 arc control vertex) plus holes,
    rects give corners/angle, arcs center/radius/start_angle/sweep, lines
    p1/p2, texts origin/size/font. Objects inside groups are listed after
    their group with "parent". bbox [x1, y1, x2, y2] limits the read to an
    area."""
    data = _read(uuids, layer, class_name, selection, include_geometry, False, limit, bbox)
    kinds: dict[str, int] = {}
    for r in data["objects"]:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    data["counts"] = kinds
    data["objects"] = [_slim(r) for r in data["objects"]]
    return data


@mcp.tool(annotations=READ)
def vw2d_structure() -> dict:
    """Layers and classes of the document and how they are used: objects per
    layer and class, class attributes, and organisation problems (objects in
    the none class, unused classes, empty layers, objects with their own
    attributes instead of 'by class', dimensions/texts outside their classes,
    inconsistent design layer scales). Read this before drawing so new
    objects follow the document's structure."""
    st = app.vw2d("structure", timeout=READ_TIMEOUT)
    nc = st["none_class"]
    problems = []
    by_layer_none: dict[str, int] = {}
    for row in st["layer_class_counts"]:
        if row["class"] == nc:
            by_layer_none[row["layer"]] = by_layer_none.get(row["layer"], 0) + row["objects"]
    for layer, n in by_layer_none.items():
        problems.append({"severity": "warning", "message": "%d Objekte auf Ebene '%s' ohne Klasse ('%s')"
                         % (n, layer, nc), "layer": layer, "count": n})
    unused = [c["name"] for c in st["classes"] if c["objects"] == 0 and c["name"] != nc]
    if unused:
        problems.append({"severity": "info", "message": "Unbenutzte Klassen: " + ", ".join(unused[:40]),
                         "classes": unused})
    empty = [l["name"] for l in st["layers"] if l["kind"] == "design" and l["objects"] == 0]
    if empty:
        problems.append({"severity": "info", "message": "Leere Konstruktionsebenen: " + ", ".join(empty),
                         "layers": empty})
    scales = sorted({l["scale"] for l in st["layers"] if l["kind"] == "design" and l["objects"] > 0})
    if len(scales) > 1:
        problems.append({"severity": "info", "message": "Konstruktionsebenen mit verschiedenen Maßstäben: "
                         + ", ".join("1:%g" % s for s in scales), "scales": scales})
    for c in st["classes"]:
        if c["explicit_attributes"]:
            problems.append({"severity": "info", "message": "%d Objekte in Klasse '%s' mit eigenen Attributen "
                             "statt 'nach Klasse'" % (c["explicit_attributes"], c["name"]),
                             "class": c["name"], "count": c["explicit_attributes"]})
    for row in st["class_kind_counts"]:
        cl = row["class"].lower()
        if row["kind"] == "dimension" and not any(h in cl for h in K.DIM_CLASS_HINTS):
            problems.append({"severity": "info", "message": "%d Bemaßungen in Klasse '%s'"
                             % (row["objects"], row["class"]), "class": row["class"]})
        if row["kind"] == "text" and row["class"] == nc:
            problems.append({"severity": "info", "message": "%d Texte ohne Klasse" % row["objects"]})
    st["problems"] = problems
    return st


@mcp.tool(annotations=READ)
def vw2d_resources(
    kind: Literal["hatch", "line_type", "class", "layer"],
    query: str | None = None,
    include_defaults: bool = True,
    limit: int = 200,
) -> list[dict]:
    """List hatches or line types (document + Vectorworks default content,
    imported automatically when used), or classes / layers of the document.
    query filters by substring, e.g. "Beton", "ISO-02"."""
    if kind in ("hatch", "line_type"):
        return app.vw2d("list_resources", kind=kind, query=query, include_defaults=include_defaults,
                        limit=limit)
    st = app.vw2d("structure", timeout=READ_TIMEOUT)
    rows = st["classes"] if kind == "class" else st["layers"]
    q = (query or "").lower()
    return [r for r in rows if q in r["name"].lower()][:limit]


# ---- organisation -------------------------------------------------------------------

@mcp.tool(annotations=WRITE)
def vw2d_setup(
    classes: list[dict[str, Any]] | None = None,
    layers: list[dict[str, Any]] | None = None,
    template: Literal["architektur"] | None = None,
    active_layer: str | None = None,
    active_class: str | None = None,
) -> dict:
    """Create or update classes and design layers so drawings are organised.

    classes: [{"name": "Wand-Tragend", "attributes": {"pen": "#000000",
    "fill": "#000000" | "none", "hatch": "Beton  SRF", "line_weight": 0.5 (mm),
    "line_style": "solid" | "ISO-02 Strichlinie", "opacity": 100}}]. Class
    attributes are what objects use 'by class' (the default for new objects).
    Use "-" for class hierarchy ("Wand-Tragend").
    layers: [{"name": "EG", "scale": 100}] (design layers, scale 1:100).
    template "architektur": a starting set of German architecture classes
    (walls, columns, openings, stairs, furniture, axes, dimensions, text...);
    only use it if the document has no class standard yet and the user
    agrees. Existing classes keep their name; given attributes are applied."""
    cl = list(classes or [])
    la = list(layers or [])
    if template:
        tpl = TEMPLATES[template]
        have = {c["name"] for c in cl}
        cl = [c for c in tpl["classes"] if c["name"] not in have] + cl
        la = list(tpl["layers"]) + la
    for c in cl:
        if not c.get("name"):
            raise ToolError("every class needs a name")
    out = app.vw2d("setup_structure", timeout=WRITE_TIMEOUT, classes=cl, layers=la,
                   active_layer=active_layer, active_class=active_class)
    out["note"] = "Neue Klassen/Ebenen werden von vw_revert_last nicht entfernt."
    return out


# ---- drawing ------------------------------------------------------------------------

@mcp.tool(annotations=WRITE)
def vw2d_draw(
    items: list[dict[str, Any]],
    unit: Literal["document", "mm", "cm", "m"] = "document",
    layer: str | None = None,
    class_name: str | None = None,
    attributes: dict[str, Any] | None = None,
    allow_no_class: bool = False,
    check: bool = True,
    tolerance_mm: float = 1.0,
) -> dict:
    """Draw exact 2D objects (all or nothing, one undo step).

    Each item has a "type" plus geometry; optional "class", "layer", "name",
    "attributes", "unit" override the call-level defaults:
    - polygon / polyline: {"points": [[x, y], ...], "relative": false,
      "holes": [[[x, y], ...]]}. relative=true: points after the first are
      offsets (exact decimal sums, like a Maßkette).
    - path (lines + true arcs): {"start": [x, y], "segments": [
      {"line_to": [x, y]}, {"arc_to": [x, y], "center": [cx, cy],
      "direction": "ccw"|"cw"} | {"arc_to": [x, y], "through": [x, y]} |
      {"arc_to": [x, y], "radius": r, "direction": "ccw", "large": false}],
      "closed": true, "holes": [path or points]}
    - rect: {"corner": [x, y], "width": w, "height": h, "angle": 0} or
      {"p1": [x, y], "p2": [x, y]}
    - circle {"center", "radius"}; arc {"center", "radius", "start_angle",
      "sweep"} or {"start", "through", "end"}; oval {"center", "width",
      "height", "angle"}; line {"p1", "p2"}; locus {"point"}
    - text {"text", "position", "size" (pt), "font", "align"
      left|center|right, "valign" top|center|baseline|bottom, "angle"}
    - dimension {"p1", "p2", "offset", "orientation"
      horizontal|vertical|aligned}; chain_dimension {"points": [...], ...}
    attributes: pen, fill ("none" or "#rrggbb"), hatch (name), line_style,
    line_weight (mm), opacity; without any, everything is 'by class'.
    Every object needs a class (class_name or per item) unless
    allow_no_class=True. Returns each object's uuid and its exactness
    (deviation between request and what Vectorworks stored), plus any check
    issues involving the new objects."""
    if not items:
        raise ToolError("items is empty")
    units = _units()
    defaults = {"unit": unit, "layer": layer, "class": class_name, "attributes": attributes}
    norm = []
    for i, it in enumerate(items):
        try:
            n, exp = S.normalize(it, units["mm_per_unit"], defaults)
        except (S.SpecError, KeyError, TypeError, ValueError) as e:
            raise ToolError("item %d (%s): %s" % (i, it.get("type"), e)) from e
        if not n.get("class") and not (n.get("attributes") or {}).get("class") and not allow_no_class:
            raise ToolError("item %d has no class. Every object belongs in a class (see vw2d_structure; "
                            "create one with vw2d_setup) - or pass allow_no_class=True." % i)
        norm.append((n, exp))
    payload = [{k: v for k, v in n.items() if not k.startswith("_")} for n, _ in norm]
    res = app.vw2d("draw_items", timeout=WRITE_TIMEOUT, items=payload)
    recs = res["objects"]
    _journal(res["document"], "vw2d_draw", [{"op": "created", "uuid": r["uuid"]} for r in recs])
    t = units_tolerances(units, tolerance_mm)
    out_objs, worst = [], 0.0
    for (n, exp), rec in zip(norm, recs):
        chk = S.check_created(n, exp, rec, t["eps"])
        dev_mm = _mm(units, chk["deviation"]) if math.isfinite(chk["deviation"]) else None
        worst = max(worst, dev_mm or 0.0)
        out_objs.append({"uuid": rec["uuid"], "kind": rec["kind"], "class": rec["class"],
                         "layer": rec["layer"], "exact": chk["exact"], "deviation_mm": dev_mm,
                         "area": rec.get("area")})
    out = {"document": res["document"], "created": out_objs, "max_deviation_mm": worst,
           "all_exact": all(o["exact"] for o in out_objs)}
    if check:
        layers = sorted({o["layer"] for o in out_objs})
        boxes = [r["bbox"] for r in recs if r.get("bbox")]
        area = None
        if boxes:
            x1, y1 = min(b[0] for b in boxes), min(b[1] for b in boxes)
            x2, y2 = max(b[2] for b in boxes), max(b[3] for b in boxes)
            pad = max(t["tol"] * 10, 0.01 * max(x2 - x1, y2 - y1))
            area = [x1 - pad, y1 - pad, x2 + pad, y2 + pad]
        issues = []
        for lay in layers:
            _, cres = _check({"layer": lay, "bbox": area}, tolerance_mm)
            issues += _new_issues(cres, [o["uuid"] for o in out_objs])
        out["issues"] = issues
        out["clean"] = not any(i["severity"] == "error" for i in issues)
    return out


@mcp.tool(annotations=WRITE)
def vw2d_edit(
    uuid: str,
    points: list[list[float]] | None = None,
    path: dict[str, Any] | None = None,
    vertices: list[list[float]] | None = None,
    closed: bool | None = None,
    holes: list[Any] | None = None,
    relative: bool = False,
    unit: Literal["document", "mm", "cm", "m"] = "document",
) -> dict:
    """Replace the outline of a polygon/polyline/rect exactly.

    Give one of: points [[x, y], ...] (corners), path (same format as
    vw2d_draw paths, lines + true arcs) or raw vertices [[x, y, type,
    radius]] as returned by vw2d_read. holes: list of point lists or paths.
    Same vertex count and no holes: edited in place (uuid stays), otherwise
    rebuilt with the same class, attributes, name, group and stacking
    position (new uuid returned). One undo step."""
    units = _units()
    cur = _read(uuids=[uuid], allow_temp=False)["objects"][0]
    if cur["kind"] not in ("polygon", "polyline", "rect"):
        raise ToolError("vw2d_edit changes polygons, polylines and rects; this is a %s" % cur["kind"])
    is_closed = cur.get("closed", True) if closed is None else closed
    if sum(x is not None for x in (points, path, vertices)) != 1:
        raise ToolError("give exactly one of points, path or vertices")
    cv = S.Converter(units["mm_per_unit"], unit)
    exp = None
    try:
        if vertices is not None:
            verts = [[cv.f(v[0]), cv.f(v[1]), int(v[2]) if len(v) > 2 else 0,
                      cv.f(v[3]) if len(v) > 3 else 0.0] for v in vertices]
        elif points is not None:
            n, exp = S.normalize({"type": "polygon", "points": points, "relative": relative,
                                  "closed": is_closed}, units["mm_per_unit"], {"unit": unit})
            verts = n["verts"]
        else:
            n, exp = S.normalize(dict(path, type="path", closed=is_closed), units["mm_per_unit"],
                                 {"unit": unit})
            verts = n["verts"]
        holes_n = []
        for h in holes or []:
            if isinstance(h, dict):
                holes_n.append(S._path(cv, dict(h, closed=True))[0])
            else:
                holes_n.append([[x, y, 0, 0.0] for x, y in cv.pts(h, relative)])
    except (S.SpecError, KeyError, TypeError, ValueError) as e:
        raise ToolError(str(e)) from e
    res = app.vw2d("replace_geometry", timeout=WRITE_TIMEOUT,
                   changes=[{"uuid": uuid, "verts": verts, "closed": is_closed, "holes": holes_n}])
    _journal(res["document"], "vw2d_edit", res["ops"])
    rec = res["objects"][0]
    t = units_tolerances(units, 1.0)
    dev = None
    if exp:
        chk = S.check_created({"kind": "poly"}, exp, rec, t["eps"])
        dev = _mm(units, chk["deviation"]) if math.isfinite(chk["deviation"]) else None
    return {"document": res["document"], "uuid": rec["uuid"], "uuid_changed": rec["uuid"] != uuid,
            "kind": rec["kind"], "deviation_mm": dev, "exact": dev is None or dev <= 1e-6,
            "object": _slim(rec)}


@mcp.tool(annotations=WRITE)
def vw2d_transform(
    uuids: list[str],
    operation: Literal["move", "rotate", "mirror", "scale"],
    dx: float | None = None,
    dy: float | None = None,
    from_point: list[float] | None = None,
    to_point: list[float] | None = None,
    center: list[float] | None = None,
    angle: float | None = None,
    axis: list[list[float]] | None = None,
    sx: float | None = None,
    sy: float | None = None,
    copy: bool = False,
    unit: Literal["document", "mm", "cm", "m"] = "document",
) -> dict:
    """Move, rotate, mirror or scale objects (copy=True keeps the originals).

    move: dx/dy, or from_point -> to_point (exact difference, e.g. a corner
    onto another object's corner); rotate: center + angle (degrees, ccw);
    mirror: axis [[x1, y1], [x2, y2]]; scale: center + sx (+ sy). One undo
    step."""
    units = _units()
    cv = S.Converter(units["mm_per_unit"], unit)
    try:
        if operation == "move":
            if from_point is not None and to_point is not None:
                p = {"dx": float(cv.d(to_point[0]) - cv.d(from_point[0])),
                     "dy": float(cv.d(to_point[1]) - cv.d(from_point[1]))}
            else:
                p = {"dx": cv.f(dx or 0), "dy": cv.f(dy or 0)}
        elif operation == "rotate":
            p = {"center": cv.pt(center), "angle": float(angle)}
        elif operation == "mirror":
            p = {"p1": cv.pt(axis[0]), "p2": cv.pt(axis[1])}
        else:
            p = {"center": cv.pt(center), "sx": float(sx), "sy": float(sy if sy is not None else sx)}
    except (TypeError, IndexError, S.SpecError) as e:
        raise ToolError("missing or invalid parameters for %s: %s" % (operation, e)) from e
    res = app.vw2d("transform", timeout=WRITE_TIMEOUT, uuids=uuids, op=operation, params=p, copy=copy)
    _journal(res["document"], "vw2d_transform", res["ops"])
    out = {"document": res["document"], "objects": [{"uuid": r["uuid"], "kind": r["kind"], "bbox": r["bbox"]}
                                                    for r in res["objects"]]}
    if operation == "move" and to_point is not None:
        tp = cv.pt(to_point)
        best = math.inf
        for r in res["objects"]:
            for q in _points_of(r):
                best = min(best, math.hypot(q[0] - tp[0], q[1] - tp[1]))
        out["to_point_deviation_mm"] = _mm(units, best) if math.isfinite(best) else None
    return out


def _points_of(rec):
    if rec.get("vertices"):
        return [v[:2] for v in rec["vertices"] if v[2] == 0]
    if rec.get("corners"):
        return rec["corners"]
    pts = [rec[k] for k in ("p1", "p2", "center", "start_point", "end_point", "origin", "point")
           if rec.get(k)]
    return pts


@mcp.tool(annotations=WRITE)
def vw2d_boolean(
    operation: Literal["union", "subtract", "intersect", "split"],
    targets: list[str],
    tools: list[str] | None = None,
    keep_tools: bool = True,
    split_line: list[list[float]] | None = None,
    unit: Literal["document", "mm", "cm", "m"] = "document",
) -> dict:
    """Exact area operations (Vectorworks/Parasolid, arcs stay arcs):
    union (targets + tools become ONE area, e.g. a ring from its pieces),
    subtract (tools cut out of each target, tools stay unless
    keep_tools=False), intersect, split (each target cut by the line
    split_line [[x1, y1], [x2, y2]]). Results keep the first target's class
    and attributes; floating-point noise is snapped back to the exact input
    coordinates. One undo step."""
    line = None
    if operation == "split":
        if not split_line or len(split_line) != 2:
            raise ToolError("split needs split_line [[x1, y1], [x2, y2]]")
        cv = S.Converter(_units()["mm_per_unit"], unit)
        line = [cv.pt(split_line[0]), cv.pt(split_line[1])]
    elif operation != "union" and not tools:
        raise ToolError("%s needs tools" % operation)
    elif operation == "union" and len(targets) + len(tools or []) < 2:
        raise ToolError("union needs at least two objects")
    res = app.vw2d("boolean", timeout=WRITE_TIMEOUT, operation=operation, targets=targets, tools=tools or [],
                   keep_tools=keep_tools, line=line, eps=units_tolerances(_units(), 1.0)["eps"])
    _journal(res["document"], "vw2d_boolean", res["ops"])
    return {"document": res["document"], "noise_fixed": res["noise_fixed"],
            "results": [{"uuid": r["uuid"], "kind": r["kind"], "area": r.get("area"),
                         "holes": len(r.get("holes") or [])} for r in res["objects"]]}


@mcp.tool(annotations=WRITE)
def vw2d_offset(
    uuid: str,
    distance: float,
    smooth_corners: bool = False,
    replace: bool = False,
    unit: Literal["document", "mm", "cm", "m"] = "document",
) -> dict:
    """Offset a polygon/polyline outline by distance (positive = outwards
    for counter-clockwise outlines), e.g. wall thickness or a plaster
    layer. replace=True swaps the original for the result."""
    cv = S.Converter(_units()["mm_per_unit"], unit)
    res = app.vw2d("offset", timeout=WRITE_TIMEOUT, uuid=uuid, distance=cv.f(distance),
                   smooth=smooth_corners, replace=replace)
    _journal(res["document"], "vw2d_offset", res["ops"])
    return {"document": res["document"], "results": [_slim(r) for r in res["objects"]]}


@mcp.tool(annotations=WRITE)
def vw2d_set_attributes(
    uuids: list[str],
    attributes: dict[str, Any] | None = None,
    class_name: str | None = None,
    layer: str | None = None,
) -> dict:
    """Change class, layer or graphic attributes of objects.

    class_name: put the objects into this class (created if missing).
    layer: move them to this design layer. attributes: pen, fill ("none" |
    "#rrggbb"), hatch, line_style, line_weight (mm), opacity, name, or
    {"by_class": true} to make every graphic attribute follow the class
    (recommended). Any single value "class" = that attribute by class.
    One undo step."""
    a = dict(attributes or {})
    if class_name:
        a["class"] = class_name
    if not a and not layer:
        raise ToolError("nothing to change")
    res = app.vw2d("set_attributes", timeout=WRITE_TIMEOUT, uuids=uuids, attributes=a, layer=layer)
    _journal(res["document"], "vw2d_set_attributes", res["ops"])
    return {"document": res["document"], "objects": [{"uuid": r["uuid"], "class": r["class"],
                                                      "layer": r["layer"], "attrs": r.get("attrs")}
                                                     for r in res["objects"]]}


@mcp.tool(annotations=DELETE)
def vw2d_delete(uuids: list[str]) -> dict:
    """Delete objects (restorable with vw_revert_last)."""
    res = app.vw2d("delete_objects", timeout=WRITE_TIMEOUT, uuids=uuids)
    _journal(res["document"], "vw2d_delete", res["ops"])
    return {"document": res["document"], "deleted": len(uuids)}


# ---- checking and fixing -----------------------------------------------------------

@mcp.tool(annotations=READ, structured_output=False)
def vw2d_check(
    uuids: list[str] | None = None,
    layer: str | None = None,
    class_name: str | None = None,
    selection: bool = False,
    tolerance_mm: float = 1.0,
    compare: Literal["layer", "class", "all"] = "layer",
    ignore_classes: list[str] | None = None,
    checks: list[str] | None = None,
    image: bool = True,
    max_issues: int = 60,
    bbox: list[float] | None = None,
):
    """Check drawing quality on the exact geometry and return a report (and a
    check image with numbered issues).

    Finds: overlaps and stacked fills, gaps and slivers between neighbours,
    points that miss each other or an edge by up to tolerance_mm, duplicates,
    double lines, open or self-intersecting outlines, duplicate/redundant
    vertices, slivers and tiny objects, objects without class, attributes
    not 'by class'. Overlaps/gaps are compared between objects on the same
    layer (compare="layer"), the same class ("class") or everything ("all");
    ignore_classes (e.g. ["Ausstattung*", "Schraffur"]) leaves out classes
    that overlap on purpose. checks: subset of overlap, duplicate, near,
    gap, self_intersection, open, double_line, vertices, tiny, classes,
    attributes, approx. Each issue suggests a vw2d_fix action.
    bbox [x1, y1, x2, y2] checks only objects touching that area (fast on
    big drawings). Precision: 0.005 mm; distances are measured on true
    arcs."""
    scope = _scope(uuids, layer, class_name, selection, bbox)
    data, res = _check(scope, tolerance_mm, checks, compare, ignore_classes)
    out = {"document": data["document"], "units": data["units"]["name"], "summary": res["summary"],
           "issues": [_issue_out(i) for i in res["issues"][:max_issues]],
           "truncated": len(res["_all_issues"]) > max_issues}
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if not image:
        return text
    return [text, _image(data, res)]


@mcp.tool(annotations=READ, structured_output=False)
def vw2d_preview(
    uuids: list[str] | None = None,
    layer: str | None = None,
    class_name: str | None = None,
    selection: bool = False,
    focus_issue: int | None = None,
    bbox: list[float] | None = None,
    tolerance_mm: float = 1.0,
    compare: Literal["layer", "class", "all"] = "layer",
    ignore_classes: list[str] | None = None,
    width_px: int = 1600,
):
    """Check image of a scope, rendered from the exact geometry with issues
    marked (red errors, orange warnings, arrows = where vw2d_fix snap would
    move a point). focus_issue zooms to one issue id from vw2d_check;
    bbox [x1, y1, x2, y2] shows (and checks) only that area (document
    units)."""
    scope = _scope(uuids, layer, class_name, selection, bbox)
    data, res = _check(scope, tolerance_mm, None, compare, ignore_classes)
    focus = tuple(bbox) if bbox else None
    if focus_issue is not None:
        it = next((i for i in res["_all_issues"] if i["id"] == focus_issue), None)
        if it is None:
            raise ToolError("no issue %d (ids come from vw2d_check)" % focus_issue)
        size = max(units_tolerances(data["units"], tolerance_mm)["tol"] * 40,
                   (it.get("distance_mm") or it.get("thickness_mm") or 1.0) * 30
                   / units_tolerances(data["units"], 1.0)["mpu"])
        focus = R.focus_box(it, size)
    summary = json.dumps({"document": data["document"], "summary": res["summary"]}, ensure_ascii=False)
    return [summary, _image(data, res, focus=focus, width_px=width_px)]


@mcp.tool(annotations=WRITE, structured_output=False)
def vw2d_fix(
    action: Literal["snap", "close", "clean", "duplicates", "overlaps", "merge", "by_class"],
    uuids: list[str] | None = None,
    layer: str | None = None,
    class_name: str | None = None,
    selection: bool = False,
    tolerance_mm: float = 1.0,
    fixed: list[str] | None = None,
    priority: list[str] | None = None,
    share_vertices: bool = True,
    merge_short: bool = False,
    issue_ids: list[int] | None = None,
    compare: Literal["layer", "class", "all"] = "layer",
    ignore_classes: list[str] | None = None,
    image: bool = True,
):
    """Fix check issues exactly, then check again (one undo step).

    snap: points that miss a vertex/edge by <= tolerance_mm move exactly onto
      it (gaps and small overlaps disappear); objects in `fixed` never move;
      share_vertices inserts the point into the neighbour's edge so both
      share the node. Arcs keep their circle.
    close: close open outlines (end point merged with the start if within
      the tolerance).
    clean: remove duplicate and collinear vertices (merge_short=True also
      merges vertices closer than the tolerance).
    duplicates: delete duplicate areas/lines (the top copy stays).
    overlaps: clip overlapping areas: `priority` (uuids or class names,
      first wins) keeps its area; without priority the object on top wins,
      so the drawing looks the same but has no stacked fills.
    merge: union all objects in scope (or uuids) into one area.
    by_class: set every graphic attribute of the scoped objects to 'by
      class'.
    issue_ids limits the fix to issues from the last vw2d_check with the
    same scope/settings."""
    neighbours = action in ("snap", "overlaps", "duplicates")
    if uuids and neighbours:
        # neighbours matter: check the whole layer(s) of these objects
        lays = sorted({r["layer"] for r in _read(uuids=uuids, geometry=False)["objects"]})
        scope = _scope(None, layer or (lays[0] if len(lays) == 1 else "*"), class_name, False)
    else:
        scope = _scope(uuids, layer, class_name, selection)
    data, res = _check(scope, tolerance_mm, None, compare, ignore_classes)
    t = units_tolerances(data["units"], tolerance_mm)
    issues = res["_all_issues"]
    if issue_ids:
        want = set(issue_ids)
        issues = [i for i in issues if i["id"] in want]
    if uuids and neighbours:
        sel = set(uuids)
        issues = [i for i in issues if sel & set(i.get("uuids", []))]
    before = res["summary"]
    shapes = res["_shapes"]
    applied: dict[str, Any] = {"action": action}
    ops: list[dict] = []
    doc = data["document"]

    def apply_polys(polys, lines=()):
        nonlocal ops
        if polys:
            r = app.vw2d("replace_geometry", timeout=WRITE_TIMEOUT, changes=polys)
            ops += r["ops"]
        if lines:
            r = app.vw2d("set_line_points", timeout=WRITE_TIMEOUT, changes=list(lines))
            ops += r["ops"]

    if action == "snap":
        plan = F.plan_snap(issues, shapes, t, fixed or (), share_vertices)
        apply_polys(plan["polys"], plan["lines"])
        applied.update(moved=plan["moved"], inserted=plan["inserted"], skipped=plan["skipped"][:20],
                       objects_changed=len(plan["polys"]) + len(plan["lines"]))
    elif action == "close":
        plan = F.plan_close(issues, shapes, t)
        apply_polys(plan["polys"])
        applied["closed"] = len(plan["polys"])
    elif action == "clean":
        plan = F.plan_clean(shapes, t, merge_short, set(uuids) if uuids else None)
        apply_polys(plan["polys"])
        applied.update(vertices_removed=plan["removed"], objects_changed=len(plan["polys"]))
    elif action == "duplicates":
        plan = F.plan_duplicates(issues, shapes)
        if plan["delete"]:
            r = app.vw2d("delete_objects", timeout=WRITE_TIMEOUT, uuids=plan["delete"])
            ops += r["ops"]
        applied["deleted"] = plan["delete"]
    elif action == "overlaps":
        plan = F.plan_overlaps(issues, shapes, priority)
        pairs = []
        geo = {s.uuid: s.geometry(t["chord"]) for s in shapes if s.is_area}
        for p in plan["subtract"]:
            tg = geo.get(p["target"])
            cover = None
            for u in p["tools"]:
                g = geo.get(u)
                if g is not None:
                    cover = g if cover is None else cover.union(g)
            covered = bool(tg is not None and cover is not None and cover.buffer(t["min_thick"]).contains(tg))
            pairs.append({"target": p["target"], "tools": p["tools"], "covered": covered})
        if pairs:
            r = app.vw2d("subtract_many", timeout=WRITE_TIMEOUT, pairs=pairs, eps=t["eps"])
            ops += r["ops"]
            applied["noise_fixed"] = r["noise_fixed"]
        applied["clipped"] = [p["target"] for p in pairs if not p["covered"]]
        applied["deleted_covered"] = [p["target"] for p in pairs if p["covered"]]
    elif action == "merge":
        targets = uuids or [s.uuid for s in shapes if s.is_area and not s.rec.get("parent")]
        if len(targets) < 2:
            raise ToolError("merge needs at least two areas")
        r = app.vw2d("boolean", timeout=WRITE_TIMEOUT, operation="union", targets=targets, tools=[],
                     keep_tools=True, line=None, eps=t["eps"])
        ops += r["ops"]
        applied["result"] = [o["uuid"] for o in r["objects"]]
    elif action == "by_class":
        targets = uuids or [r["uuid"] for r in data["objects"] if r["kind"] != "group"]
        r = app.vw2d("set_attributes", timeout=WRITE_TIMEOUT, uuids=targets,
                     attributes={"by_class": True}, layer=None)
        ops += r["ops"]
        applied["objects"] = len(targets)
    _journal(doc, "vw2d_fix", ops)
    if action in ("merge",) or (uuids and not neighbours and action != "by_class"):
        # rebuilt objects have new uuids: re-check the layer(s) instead
        lays = sorted({r["layer"] for r in data["objects"]})
        scope = _scope(None, layer or (lays[0] if len(lays) == 1 else "*"), class_name, False)
    data2, res2 = _check(scope, tolerance_mm, None, compare, ignore_classes)
    out = {"document": doc, "applied": applied, "before": before, "after": res2["summary"],
           "remaining": [_issue_out(i) for i in res2["issues"][:30]]}
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if not image:
        return text
    return [text, _image(data2, res2)]


@mcp.tool(annotations=READ)
def vw2d_select(uuids: list[str], zoom: bool = True) -> dict:
    """Select objects in Vectorworks (and zoom to them) so the user sees
    what was drawn or changed."""
    return app.vw2d("select_objects", uuids=uuids, zoom=zoom)


# ---- prompt ------------------------------------------------------------------------

@mcp.prompt()
def sauber_zeichnen(aufgabe: str = "") -> str:
    """Exakt und sauber organisiert in Vectorworks zeichnen (2D)."""
    return (
        "Zeichne in der geöffneten Vectorworks-Datei exakt und sauber organisiert (nur 2D). "
        "Lies zuerst die Struktur (vw2d_structure) und halte dich an vorhandene Ebenen und Klassen; "
        "fehlende legst du mit vw2d_setup an. Jedes Objekt in eine passende Klasse, Attribute nach "
        "Klasse. Flächen schließen lückenlos und ohne Überlappung aneinander, zusammengehörige "
        "Bauteile sind eine Fläche, keine Doppellinien, keine Kanten verlieren, Bögen als echte Bögen. "
        "Nach dem Zeichnen vw2d_check, Fehler mit vw2d_fix beheben, erneut prüfen und das Prüfbild "
        "zeigen.\n\nAufgabe: " + (aufgabe or "(folgt)")
    )
