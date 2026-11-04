"""Imports, clean-up and the user's profile (0.7).

vw2d_profile  how Keystone works for this user (settings + own rules)
vw2d_import   DWG/DXF import without dialog, or stock-taking of a layer
vw2d_join     loose pieces into polylines
vw2d_simplify fewer points within a tolerance (before/after first)
vw2d_group    objects into groups
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal

from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from vwmcp import app, dxfscan, profile
from vwmcp.app import WRITE, mcp
from vwmcp.geom import render as R
from vwmcp.geom.inventory import take_stock
from vwmcp.geom.join import plan_join
from vwmcp.geom.shapes import from_records, units_tolerances
from vwmcp.geom.simplify import plan_simplify
from vwmcp.tools.draw2d import READ_TIMEOUT, WRITE_TIMEOUT, _compact_map, _journal, _read, _scope, _uuid_map

PROFILE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
IMPORT_TIMEOUT = 900.0


def _profile_out(prof: dict) -> dict:
    return {"settings": profile.describe(prof), "rules_now": profile.instructions_block(prof),
            "file": str(profile.path())}


@mcp.tool(annotations=PROFILE)
def vw2d_profile(
    action: Literal["show", "set", "reset", "add_rule", "remove_rule"] = "show",
    values: dict[str, Any] | None = None,
    rule: str | None = None,
    index: int | None = None,
    keys: list[str] | None = None,
) -> dict:
    """The user's profile: how Keystone works for this user. Settings:
    detail (keep | simplify_ok), merge_parts (always | ask | never), areas
    (seamless | loose), curves (true_arcs | polylines_ok), classes (always |
    keep_import), attributes (by_class | explicit_ok), check_mode (auto |
    strict | report), confirm_above (objects; 0 = never ask), tolerance_mm,
    simplify_paper_mm (deviation on paper, times the layer scale), rules
    (the user's own rules in their words).

    show: every setting with value, default and choices. set: {"key":
    value, ...}. add_rule: rule="text"; remove_rule: index (1-based).
    reset: keys or everything. Stored in ~/.vwmcp/profile.json; the result
    holds the rules as they apply now - follow them from here on. Change the
    profile only when the user asks for it or agrees when you offer it (e.g.
    after they corrected how you work)."""
    try:
        if action == "show":
            prof = profile.load()
        elif action == "set":
            if not values:
                raise ToolError("give values, e.g. {\"detail\": \"simplify_ok\"}")
            prof = profile.save(values)
        elif action == "add_rule":
            prof = profile.add_rule(rule or "")
        elif action == "remove_rule":
            if index is None:
                raise ToolError("give the rule's index (1-based, see show)")
            prof = profile.remove_rule(int(index))
        else:
            prof = profile.reset(keys)
    except profile.ProfileError as e:
        raise ToolError(str(e)) from e
    return _profile_out(prof)


# ---- import / stock-taking -------------------------------------------------------------------

@mcp.tool(annotations=WRITE)
def vw2d_import(path: str | None = None, layer: str | None = None) -> dict:
    """Import a DWG or DXF file into the open document without any dialog
    (Vectorworks' current import settings; usually one new design layer named
    after the file), or - with layer and no path - take stock of a layer
    that is already there.

    Reports what arrived (kinds, classes, size), what is missing compared
    with the file (DXF: counted; DWG can't be counted), how fragmented it is
    (loose lines, joinable pieces, points), and the next steps. Tell the user
    when objects are missing: Vectorworks leaves out rational splines
    (circles, ellipses, arcs Rhino writes as NURBS); Rhino's R12 DXF holds no
    splines at all - exporting from Rhino again (DWG 2018) with
    splines/polycurves as polylines fixes both.
    vw_revert_last after an import deletes the imported layers."""
    if not path and not layer:
        raise ToolError("give the path of a .dwg/.dxf file, or a layer to take stock of")
    source = None
    layers: list[str] = []
    imported: dict[str, Any] = {}
    doc = None
    if path:
        path = os.path.expanduser(path)
        if not os.path.isfile(path):
            raise ToolError("file not found: %s" % path)
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".dwg", ".dxf"):
            raise ToolError("vw2d_import takes .dwg or .dxf files, not %s%s" % (
                ext or "files without extension",
                " - in Rhino export the view as DWG 2018 (splines and polycurves as polylines) or use "
                "Make2D/VectorView and export that" if ext == ".3dm" else ""))
        try:
            source = dxfscan.scan(path)
        except OSError as e:
            raise ToolError("can't read %s: %s" % (path, e)) from e
        res = app.vw2d("import_dwg", timeout=IMPORT_TIMEOUT, path=path)
        doc = res["document"]
        _journal(doc, "vw2d_import", res.get("ops") or [])
        layers = res["new_layers"] or list(res.get("grown_layers") or {})
        imported = {"file": os.path.basename(path), "seconds": res["seconds"], "new_layers": res["new_layers"]}
        if res.get("grown_layers"):
            imported["into_existing_layers"] = res["grown_layers"]
        if not layers:
            return {"document": doc, "imported": imported, "warning": "Vectorworks imported nothing "
                    "(file empty, or the import settings filter everything)."}
    else:
        layers = [layer]
    stock: dict[str, Any] = {}
    allrecs: list[dict] = []
    units = None
    for lay in layers:
        data = _read(layer=lay, geometry=True, allow_temp=False, limit=200000)
        doc = doc or data["document"]
        units = data["units"]
        recs = data["objects"]
        allrecs += recs
        if len(layers) > 1:
            stock[lay] = len(recs)
    if units is None:
        units = app.vw2d("units")
    t = units_tolerances(units, profile.load()["tolerance_mm"])
    top = {lay: sum(1 for r in allrecs if r.get("layer") == lay and not r.get("parent")) for lay in layers}
    summary = take_stock(allrecs, t["mpu"], t["tol"], source=source, arrived_by_layer=top if path else None)
    out: dict[str, Any] = {"document": doc}
    if imported:
        out["imported"] = imported
    if source and not source.get("countable"):
        out["source"] = {"format": source.get("format"), "version": source.get("version")}
    if len(layers) > 1:
        out["objects_per_layer"] = stock
    out["layers"] = layers
    out.update(summary)
    return out


# ---- join / simplify / group -----------------------------------------------------------------

def _confirm(n: int, dry_run: bool | None) -> bool:
    """Is this a dry run? None = the profile decides (big changes are tried as a dry run first)."""
    if dry_run is not None:
        return bool(dry_run)
    lim = profile.load()["confirm_above"]
    return bool(lim) and n > lim


def _compare_image(before_recs, after_recs, units, bbox=None, title=""):
    t = units_tolerances(units, 1.0)
    b = from_records(before_recs, t["chord"])
    a = from_records(after_recs, t["chord"])
    png = R.render_compare(b, a, t["chord"], focus=tuple(bbox) if bbox else None, title=title, mpu=t["mpu"])
    return Image(data=png, format="png")


def _after_records(before: list[dict], changes: list[dict], drop: list[str]) -> list[dict]:
    """What the scope would look like after the changes (for the compare image)."""
    by = {c["uuid"]: c for c in changes}
    gone = set(drop)
    out = []
    for r in before:
        if r["uuid"] in gone:
            continue
        c = by.get(r["uuid"])
        if c is None:
            out.append(r)
            continue
        n = dict(r, kind="polyline" if not c["closed"] else "polygon", vertices=c["verts"], closed=c["closed"],
                 holes=[])
        n.pop("p1", None)
        n.pop("p2", None)
        n.pop("approx_polygon", None)
        out.append(n)
    return out


@mcp.tool(annotations=WRITE, structured_output=False)
def vw2d_join(
    uuids: list[str] | None = None,
    layer: str | None = None,
    class_name: str | None = None,
    selection: bool = False,
    bbox: list[float] | None = None,
    tolerance_mm: float | None = None,
    include_curves: bool = True,
    through_junctions: bool = False,
    dry_run: bool | None = None,
    image: bool = True,
):
    """Join loose pieces (lines and open polylines whose ends meet within
    tolerance_mm, default from the profile) into polylines - e.g. the
    thousands of fragments of a Make2D/VectorView export. Only pieces of the
    same layer, class, group and look (pen, line weight, line style) join;
    a chain stops where three or more pieces meet, unless through_junctions
    (then it takes the straightest way on). Closed chains become polygons
    without fill. include_curves: pieces with curve vertices (imported
    splines) join as Vectorworks' polygonization of them.

    dry_run: True shows what would happen (before red / after black), False
    applies it (one undo step), left out = the profile decides (dry run first
    above its confirm_above). Scope: uuids, selection, layer, class_name,
    bbox."""
    if tolerance_mm is not None and not tolerance_mm > 0:
        raise ToolError("tolerance_mm must be more than 0 (how far apart piece ends may be, real size)")
    scope = _scope(uuids, layer, class_name, selection, bbox)
    data = _read(**scope, geometry=True, allow_temp=include_curves, limit=200000)
    units = data["units"]
    tol_mm = profile.load()["tolerance_mm"] if tolerance_mm is None else tolerance_mm
    t = units_tolerances(units, tol_mm)
    plan = plan_join(data["objects"], t["tol"], include_curves=include_curves, through_junctions=through_junctions)
    touched = plan["pieces_joined"]
    summary = {"document": data["document"], "pieces": plan["pieces"], "joined_pieces": touched,
               "polylines": plan["chains"], "closed": sum(1 for c in plan["changes"] if c["closed"]),
               "objects_before": len(data["objects"]),
               "objects_after": len(data["objects"]) - touched + plan["chains"], "tolerance_mm": tol_mm}
    if plan["skipped"]:
        summary["not_joinable"] = plan["skipped"]
    if not plan["changes"]:
        summary["note"] = "nothing to join (no pieces meet within the tolerance)"
        return json.dumps(summary, ensure_ascii=False, indent=1)
    if _confirm(touched, dry_run):
        summary["dry_run"] = True
        summary["next"] = ("show the user the image; call again with dry_run=false to apply (one undo step)"
                           + (", or first on a part with bbox" if touched > 1000 else ""))
        text = json.dumps(summary, ensure_ascii=False, indent=1)
        if not image:
            return text
        after = _after_records(data["objects"], plan["changes"], plan["drop"])
        return [text, _compare_image(data["objects"], after, units, bbox, "vw2d_join (Vorschau)")]
    res = app.vw2d("join_pieces", timeout=WRITE_TIMEOUT, changes=plan["changes"], drop=plan["drop"])
    _journal(res["document"], "vw2d_join", res["ops"])
    m = _uuid_map(res["ops"])
    summary["applied"] = True
    summary["new_uuids"] = _compact_map(m)
    text = json.dumps(summary, ensure_ascii=False, indent=1)
    if not image:
        return text
    after = _read(uuids=[o["uuid"] for o in res["objects"]], geometry=True, allow_temp=False)["objects"]
    return [text, _compare_image(data["objects"], after, units, bbox, "vw2d_join")]


def _layer_scale(layer_names: set) -> float:
    try:
        st = app.vw2d("structure", timeout=READ_TIMEOUT)
    except ToolError:
        return 100.0
    scales = [float(lay.get("scale") or 1.0) for lay in st.get("layers", []) if lay.get("name") in layer_names]
    return max(scales) if scales else 100.0


@mcp.tool(annotations=WRITE, structured_output=False)
def vw2d_simplify(
    uuids: list[str] | None = None,
    layer: str | None = None,
    class_name: str | None = None,
    selection: bool = False,
    bbox: list[float] | None = None,
    tolerance_mm: float | None = None,
    min_vertices: int = 8,
    dry_run: bool = True,
    image: bool = True,
):
    """Thin out polylines/polygons with many points (organic curtains,
    plants, terrain, traced or imported curves): keeps a subset of the
    original points so the outline never deviates by more than tolerance_mm
    (real size). Default tolerance: the profile's simplify_paper_mm times the
    layer scale (0.1 mm on paper at 1:100 = 10 mm). Corners stay exactly
    where they are; outlines with true arcs are left alone; areas stay valid.
    Only objects with at least min_vertices points.

    dry_run=True (default) reports the reduction and shows before (red) /
    after (black); call again with dry_run=False to apply (one undo step).
    Run vw2d_join first when the shape is made of many separate pieces."""
    if tolerance_mm is not None and not tolerance_mm > 0:
        raise ToolError("tolerance_mm must be more than 0 (how far the thinned outline may deviate, real size)")
    scope = _scope(uuids, layer, class_name, selection, bbox)
    data = _read(**scope, geometry=True, allow_temp=True, limit=200000)
    units = data["units"]
    if tolerance_mm is None:
        scale = _layer_scale({r.get("layer") for r in data["objects"]})
        tolerance_mm = profile.load()["simplify_paper_mm"] * scale
    t = units_tolerances(units, tolerance_mm)
    plan = plan_simplify(data["objects"], t["tol"], min_vertices=min_vertices)
    summary = {"document": data["document"], "tolerance_mm": round(tolerance_mm, 4), "objects": plan["objects"],
               "vertices_before": plan["vertices_before"], "vertices_after": plan["vertices_after"]}
    if plan["skipped"]:
        summary["left_alone"] = plan["skipped"]
    if not plan["changes"]:
        summary["note"] = "nothing to simplify within this tolerance"
        return json.dumps(summary, ensure_ascii=False, indent=1)
    changes = [{"uuid": c["uuid"], "verts": c["verts"], "closed": c["closed"], "holes": []} for c in plan["changes"]]
    if dry_run:
        summary["dry_run"] = True
        summary["next"] = "show the user the image; call again with dry_run=false to apply (one undo step)"
        text = json.dumps(summary, ensure_ascii=False, indent=1)
        if not image:
            return text
        after = _after_records(data["objects"], changes, [])
        return [text, _compare_image(data["objects"], after, units, bbox, "vw2d_simplify (Vorschau)")]
    res = app.vw2d("replace_geometry", timeout=WRITE_TIMEOUT, changes=changes)
    _journal(res["document"], "vw2d_simplify", res["ops"])
    summary["applied"] = True
    summary["new_uuids"] = _compact_map(_uuid_map(res["ops"]))
    text = json.dumps(summary, ensure_ascii=False, indent=1)
    if not image:
        return text
    return [text, _compare_image(data["objects"], res["objects"], units, bbox, "vw2d_simplify")]


@mcp.tool(annotations=WRITE)
def vw2d_group(
    uuids: list[str] | None = None,
    layer: str | None = None,
    class_name: str | None = None,
    selection: bool = False,
    bbox: list[float] | None = None,
    name: str | None = None,
    per_class: bool = False,
    group_class: str | None = None,
) -> dict:
    """Group objects (top-level ones; one group per layer, or per layer and
    class with per_class). Scope: uuids, selection, layer, class_name, bbox
    (objects inside the area). The group gets its members' class when they
    share one, else the none class; group_class sets it (existing class).
    The objects keep their uuids; vw_revert_last ungroups again."""
    scope = _scope(uuids, layer, class_name, selection, bbox)
    data = _read(**scope, geometry=False, allow_temp=False, limit=200000)
    recs = [r for r in data["objects"] if not r.get("parent")]
    if bbox:
        x1, y1, x2, y2 = min(bbox[0], bbox[2]), min(bbox[1], bbox[3]), max(bbox[0], bbox[2]), max(bbox[1], bbox[3])
        recs = [r for r in recs if r.get("bbox") and r["bbox"][0] >= x1 and r["bbox"][1] >= y1
                and r["bbox"][2] <= x2 and r["bbox"][3] <= y2]
    if len(recs) < 2:
        raise ToolError("need at least two top-level objects to group (found %d)" % len(recs))
    buckets: dict[tuple, list[str]] = {}
    for r in recs:
        key = (r.get("layer"), r.get("class") if per_class else None)
        buckets.setdefault(key, []).append(r["uuid"])
    groups = []
    for (lay, cls), us in sorted(buckets.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        if len(us) < 2:
            continue
        nm = name if name and len(buckets) == 1 else ((name + " " if name else "") + (cls or lay or "")).strip() or None
        groups.append({"uuids": us, "name": nm, "class": group_class})
    if not groups:
        raise ToolError("nothing to group: every layer/class has only one object")
    res = app.vw2d("group_objects", timeout=WRITE_TIMEOUT, groups=groups)
    _journal(res["document"], "vw2d_group", res["ops"])
    return {"document": res["document"], "groups": [{"uuid": g["uuid"], "name": g.get("name"),
                                                      "layer": g.get("layer"), "class": g.get("class")}
                                                     for g in res["groups"]],
            "objects": sum(len(g["uuids"]) for g in groups)}
