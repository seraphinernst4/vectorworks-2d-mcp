"""Asset tools: a catalogue of exact 2D symbols (sanitary, kitchen,
furniture, trees, people, cars) in plan and elevation.

Assets are drawn from code (catalog/), in mm, with true arcs, and go
through the same exact pipeline as vw2d_draw (Decimal unit conversion,
read-back check). In Vectorworks each asset view + parameter set becomes
one symbol definition (in a "Keystone <Kategorie>" folder of the resource
manager), components in the classes "<Kategorie-Klasse>" (outline) and
"<Kategorie-Klasse>-Detail" (inner lines), all attributes by class.
"""

from __future__ import annotations

import json
import math
from typing import Any, Literal

from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError

from vwmcp import app, catalog
from vwmcp.app import READ, WRITE, mcp
from vwmcp.catalog import preview as P
from vwmcp.catalog import shapes as SH
from vwmcp.geom import specs as S
from vwmcp.geom.shapes import units_tolerances
from vwmcp.tools.draw2d import WRITE_TIMEOUT, _journal, _mm, _units

MAX_PREVIEW_PANELS = 24
MAX_PLACEMENTS = 500


def _items(asset_id: str, view: str, params: dict | None, mirror: bool) -> tuple[list[dict], bool]:
    """Catalogue items (mm) of one view; mirrored if asked and not symmetric.
    Returns (items, mirrored)."""
    try:
        items = catalog.build(asset_id, view, params)
    except KeyError as e:
        raise ToolError(str(e.args[0]) if e.args else str(e)) from e
    except (ValueError, ArithmeticError) as e:
        raise ToolError("asset %r: %s" % (asset_id, e)) from e
    if not mirror:
        return items, False
    m = SH.mirror_items(items)
    return (items, False) if _same_shape(items, m) else (m, True)


def _shape_key(it: dict) -> frozenset:
    pts, _ = P.item_points(it)
    return frozenset((round(x, 3) + 0.0, round(y, 3) + 0.0) for x, y in pts)


def _same_shape(a: list[dict], b: list[dict]) -> bool:
    """True if two item lists draw the same picture (mirror of a symmetric asset)."""
    from collections import Counter

    return Counter((it["type"], it.get("role"), _shape_key(it)) for it in a) == \
        Counter((it["type"], it.get("role"), _shape_key(it)) for it in b)


def _classed(items: list[dict], category: str) -> list[dict]:
    base = catalog.CATEGORY_CLASS[category]
    out = []
    for it in items:
        it = dict(it)
        role = it.pop("role", "outline")
        it["class"] = base + catalog.DETAIL_SUFFIX if role == "detail" else base
        it["unit"] = "mm"
        out.append(it)
    return out


def _assets_brief(found: list) -> list[dict]:
    return [{"id": a.id, "name": a.name, "category": a.category, "views": list(a.views),
             "params": {k: v[0] for k, v in a.params.items()}} for a in found]


@mcp.tool(annotations=READ)
def vw2d_assets(
    query: str | None = None,
    category: Literal["sanitary", "kitchen", "furniture", "plants", "people", "vehicles"] | None = None,
    asset: str | None = None,
    view: Literal["plan", "front", "side"] | None = None,
    params: dict[str, float] | None = None,
    preview: bool = True,
) -> list[Any]:
    """The Keystone asset catalogue: exact 2D symbols for architecture plans
    (WC, urinal, wash basin, shower, bath, kitchen sink/hob/fridge, chair,
    table with chairs, bed, sofa, wardrobe, desk, tree, shrub, person, car)
    in plan ("Grundriss") and, where available, front and side elevation.

    query: words to search (German or English, e.g. "toilette", "baum",
    "tisch"); category filters. asset: one asset id for its full description
    (parameters with meaning and default, insertion point) - with view and
    params, the preview shows exactly that variant. preview=True adds a
    picture (outline dark, detail lines grey). Nothing is drawn in
    Vectorworks; place assets with vw2d_place_asset."""
    if asset:
        a = catalog.CATALOG.get(asset)
        if a is None:
            raise ToolError("unknown asset %r. Known: %s" % (asset, ", ".join(catalog.CATALOG)))
        found = [a]
        views = [view] if view else list(a.views)
        if view and view not in a.views:
            raise ToolError("asset %r has no view %r (views: %s)" % (asset, view, ", ".join(a.views)))
        info: dict[str, Any] = {"asset": catalog.describe(a)}
        try:
            info["symbol_names"] = {v: catalog.symbol_name(a.id, v, params) for v in views}
        except KeyError as e:
            raise ToolError(str(e)) from e
    else:
        found = catalog.find(query, category)
        if not found and query:  # fall back to any of the words
            hits = {a.id for w in query.split() for a in catalog.find(w, category)}
            found = [a for a in catalog.CATALOG.values() if a.id in hits]
        info = {"count": len(found), "assets": _assets_brief(found)}
        if not found:
            info["hint"] = "nothing found; categories: %s" % ", ".join(catalog.CATEGORY_CLASS)
        views = None
    out: list[Any] = [json.dumps(info, ensure_ascii=False)]
    if preview and found:
        groups = []
        for a in found:
            for v in (views if views is not None else a.views):
                if v not in a.views:
                    continue
                its, _ = _items(a.id, v, params if asset else None, False)
                groups.append(("%s · %s" % (a.name, catalog.VIEW_DE.get(v, v)), its))
                if len(groups) >= MAX_PREVIEW_PANELS:
                    break
            if len(groups) >= MAX_PREVIEW_PANELS:
                break
        cols = min(4, len(groups)) or 1
        out.append(Image(data=P.render(groups, cols=cols), format="png"))
    return out


# Assets reach from the wall towards local -y. Rotation (counter-clockwise)
# that turns them away from a wall on that side of the object:
WALL_ROTATION = {"above": 0.0, "left": 90.0, "below": 180.0, "right": 270.0}


def _rotation(value, wall, where: str) -> float:
    if wall is not None:
        if wall not in WALL_ROTATION:
            raise ToolError("%s: wall must be one of %s" % (where, ", ".join(WALL_ROTATION)))
        return WALL_ROTATION[wall]
    try:
        r = float(value)
    except (TypeError, ValueError):
        raise ToolError("%s: rotation must be a number (degrees), got %r" % (where, value)) from None
    if not math.isfinite(r):
        raise ToolError("%s: rotation must be a finite number" % where)
    return r


def _placements(position, rotation, wall, placements) -> list[dict]:
    if placements:
        if len(placements) > MAX_PLACEMENTS:
            raise ToolError("at most %d placements per call" % MAX_PLACEMENTS)
        out = []
        for i, pl in enumerate(placements):
            if not isinstance(pl, dict) or "position" not in pl:
                raise ToolError("placement %d needs a position [x, y]" % i)
            out.append({"position": pl["position"],
                        "rotation": _rotation(pl.get("rotation", 0.0), pl.get("wall"), "placement %d" % i)})
        return out
    return [{"position": list(position), "rotation": _rotation(rotation, wall, "rotation")}]


@mcp.tool(annotations=WRITE)
def vw2d_place_asset(
    asset: str,
    position: list[float] = [0.0, 0.0],  # noqa: B006  (JSON default, never mutated)
    view: Literal["plan", "front", "side"] = "plan",
    rotation: float = 0.0,
    wall: Literal["above", "left", "below", "right"] | None = None,
    mirror: bool = False,
    params: dict[str, float] | None = None,
    placements: list[dict[str, Any]] | None = None,
    unit: Literal["document", "mm", "cm", "m"] = "document",
    layer: str | None = None,
    class_name: str | None = None,
    as_symbol: bool = True,
    folder: str | None = None,
) -> dict:
    """Place a catalogue asset (see vw2d_assets) exactly, as a symbol.

    position: insertion point in `unit` (see the asset's "insertion": e.g.
    WC/wash basin = middle of the wall side; unrotated the object reaches
    from the wall towards -y, i.e. the wall is above it in the plan).
    wall: where the wall is, seen from the object - "above" (rotation 0),
    "left" (90), "below" (180), "right" (270); the object then faces away
    from that wall into the room. Or give rotation in degrees
    (counter-clockwise) directly. mirror: left/right variant (a separate
    mirrored symbol definition, exact; ignored for symmetric assets).
    params: dimensions in mm (e.g. {"b": 400, "h": 460}); each parameter set
    is its own symbol, named like "WC wandhängend - Grundriss (h=460)".
    placements: [{"position": [x, y], "rotation": deg | "wall": "left"}, ...]
    to place several instances in one call (overrides position/rotation).
    The symbol definition is created once (folder "Keystone <Kategorie>" or
    `folder`, "" = none) and reused if it exists - if you edited it in
    Vectorworks, your version stays. Components are in the classes
    "<Klasse>" and "<Klasse>-Detail" (created with sensible attributes if
    missing; existing classes are never changed); instances go into
    class_name or the asset's class. as_symbol=False draws editable groups
    instead (exactness is then checked on the parts before they are moved
    into place). A missing layer is created. vw_revert_last removes the
    instances (and the definition and folder if this call created them and
    nothing else uses them); classes it created stay (empty)."""
    a = catalog.CATALOG.get(asset)
    if a is None:
        raise ToolError("unknown asset %r. Known: %s" % (asset, ", ".join(catalog.CATALOG)))
    if view not in a.views:
        raise ToolError("asset %r has no view %r (views: %s)" % (asset, view, ", ".join(a.views)))
    raw, mirrored = _items(asset, view, params, mirror)
    items = _classed(raw, a.category)
    units = _units()
    mpu = units["mm_per_unit"]
    norm = []
    for i, it in enumerate(items):
        try:
            n, exp = S.normalize(it, mpu, {})
        except (S.SpecError, KeyError, TypeError, ValueError, ArithmeticError) as e:
            raise ToolError("asset item %d (%s): %s - try other dimensions" % (i, it.get("type"), e)) from e
        norm.append((n, exp))
    pls = []
    for i, pl in enumerate(_placements(position, rotation, wall, placements)):
        try:
            loc, _ = S.normalize({"type": "locus", "point": pl["position"], "unit": unit}, mpu, {})
        except (S.SpecError, KeyError, TypeError, ValueError) as e:
            raise ToolError("placement %d: %s" % (i, e)) from e
        pls.append({"position": list(loc["point"]), "rotation": pl["rotation"] % 360.0})
    base = catalog.CATEGORY_CLASS[a.category]
    new_classes = {base: catalog.CLASS_ATTRS[base],
                   base + catalog.DETAIL_SUFFIX: catalog.DETAIL_ATTRS}
    name = catalog.symbol_name(asset, view, params, mirrored)
    used = catalog.resolve_params(asset, view, params)
    desc = "Keystone-Asset %s, %s%s. %s. Maße (mm): %s" % (
        a.id, catalog.VIEW_DE.get(view, view), ", gespiegelt" if mirrored else "", a.insertion,
        ", ".join("%s=%g" % kv for kv in used.items()) or "-")
    payload = [{k: v for k, v in n.items() if not k.startswith("_")} for n, _ in norm]
    res = app.vw2d("place_asset", timeout=WRITE_TIMEOUT, symbol=name, items=payload, placements=pls,
                   folder=(catalog.folder_name(asset) if folder is None else folder) or None,
                   layer=layer, cls=class_name or base, as_symbol=as_symbol, new_classes=new_classes,
                   description=desc)
    ops: list[dict] = []
    if as_symbol and res.get("folder_created"):
        ops.append({"op": "symfolder", "name": catalog.folder_name(asset) if folder is None else folder})
    if as_symbol and res.get("symbol_created"):
        ops.append({"op": "symdef", "name": name})
    ops += [{"op": "created", "uuid": r["uuid"]} for r in res["instances"]]
    _journal(res["document"], "vw2d_place_asset", ops)

    # exactness: the definition (or first group) against the catalogue
    t = units_tolerances(units, 1.0)
    comp = res.get("definition") or []
    exact_info: dict[str, Any] = {}
    if len(comp) != len(norm):
        exact_info = {"all_exact": False, "note": "the existing symbol has %d components, the catalogue "
                      "version %d - the document's own version was kept" % (len(comp), len(norm))}
    else:
        worst, all_exact = 0.0, True
        for (n, exp), rec in zip(norm, comp):
            chk = S.check_created(n, exp, rec, t["eps"])
            all_exact = all_exact and chk["exact"]
            if math.isfinite(chk["deviation"]):
                worst = max(worst, _mm(units, chk["deviation"]))
            else:
                worst = math.inf
        exact_info = {"all_exact": all_exact, "max_deviation_mm": worst if math.isfinite(worst) else None}
        if not all_exact and not res.get("symbol_created") and as_symbol:
            exact_info["note"] = "the existing symbol differs from the catalogue - the document's version was kept"
    # instance placement check
    inst_out, place_dev = [], 0.0
    for pl, rec in zip(pls, res["instances"]):
        o = {"uuid": rec["uuid"], "kind": rec["kind"], "class": rec["class"], "layer": rec["layer"]}
        if rec.get("location") is not None:
            d = math.hypot(rec["location"][0] - pl["position"][0], rec["location"][1] - pl["position"][1])
            dr = abs((rec.get("rotation", 0.0) - pl["rotation"] + 180.0) % 360.0 - 180.0)
            place_dev = max(place_dev, _mm(units, d))
            o["location"], o["rotation"] = rec["location"], round(rec.get("rotation", 0.0), 9)
            if dr > 1e-6:
                o["rotation_deviation_deg"] = dr
        inst_out.append(o)
    out = {"document": res["document"], "asset": asset, "view": view,
           "symbol": res.get("symbol", {}).get("name") if as_symbol else None,
           "symbol_created": bool(res.get("symbol_created")), "mirrored": mirrored,
           "placed": len(inst_out), "instances": inst_out[:50],
           "placement_deviation_mm": place_dev if as_symbol else None, **exact_info}
    if len(inst_out) > 50:
        out["instances_more"] = len(inst_out) - 50
    if mirror and not mirrored:
        out["note_mirror"] = "asset is symmetric in this view - mirroring changes nothing"
    if res.get("classes_created"):
        out["classes_created"] = res["classes_created"]
    if res.get("folder_created"):
        out["folder_created"] = catalog.folder_name(asset) if folder is None else folder
    return out
