"""Planning tools: the knowledge of the Raumpilot books (what space every
element needs, distances, room sizes, standard layouts, rules) and drawing
it in a style the user controls.

  vw2d_knowledge      ask the knowledge base (elements, rooms, standard
                      layouts, rules with sources); previews
  vw2d_style          the drawing style: Keystone's Raumpilot look as the
                      first layer, the office's own classes/colours/symbols on top
  vw2d_place_element  place elements with their movement areas, in the style
  vw2d_plan_room      layout variants for a room (standard situations from
                      the book fitted to the size + generated ones), checked;
                      draws the chosen one
  vw2d_check_room     check a drawn room (Keystone elements and the
                      office's mapped symbols) against the rules
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Literal

from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError

from vwmcp import app
from vwmcp.app import READ, WRITE, mcp
from vwmcp.geom import specs as S
from vwmcp.planning import engine as E
from vwmcp.planning import kb, render, style, symbols
from vwmcp.tools.draw2d import WRITE_TIMEOUT, _journal, _units

MAX_VARIANTS = 12
MAX_PLACEMENTS = 300
CAT_DE = {"sanitaer": "Sanitär", "kueche": "Küche", "essen": "Essen", "wohnen": "Wohnen", "schlafen": "Schlafen",
          "kinder": "Kinder", "arbeiten": "Arbeiten", "lernen": "Lernen", "wirtschaften": "Wirtschaften",
          "aufbewahren": "Aufbewahren", "entsorgen": "Entsorgen", "verkehr": "Verkehr", "freiraum": "Freiraum",
          "person": "Personen", "sonstiges": "Sonstiges"}
DESC_RE = re.compile(r"Keystone-Element (\S+) ([\d.]+)x([\d.]+)")


# ---- helpers ------------------------------------------------------------------------

def _st(name: str | None) -> dict:
    try:
        return style.resolve(name)
    except style.StyleError as e:
        raise ToolError(str(e)) from e


def _el(eid: str) -> dict:
    try:
        return kb.element(eid)
    except KeyError as e:
        raise ToolError(str(e.args[0])) from e


def _cm_factor(unit: str, units: dict | None = None) -> float:
    """cm per given unit."""
    if unit == "cm":
        return 1.0
    if unit == "mm":
        return 0.1
    if unit == "m":
        return 100.0
    units = units or _units()
    return float(f"{units['mm_per_unit']:.12g}") / 10.0


def _fmt(v: float) -> str:
    return ("%.1f" % v).rstrip("0").rstrip(".")


def symbol_name(e: dict, w: float, d: float, st: dict) -> str:
    name = "%s %s×%s" % (e["name"], _fmt(w), _fmt(d))
    if st.get("geometry") == "outline":
        name += " (Umriss)"
    return name[:63]


def element_items(e: dict, w: float, d: float, params: dict, st: dict) -> list[dict]:
    """Keystone's symbol for the element (mm, local frame, with classes)."""
    raw = symbols.draw(e.get("kind", "generic"), w, d, st.get("geometry", "raumpilot"), **params)
    obj, _ = style.class_for(st, "object", e.get("category"))
    det, _ = style.class_for(st, "detail", e.get("category"))
    out = []
    for it in raw:
        it = dict(it)
        role = it.pop("role", "outline")
        it["class"] = det if role == "detail" else obj
        it["unit"] = "mm"
        out.append(it)
    return out


def _description(e: dict, w: float, d: float, mode: str) -> str:
    areas = kb.areas_of(e, mode)
    a = "; ".join("%s %s×%s%s" % ({"front": "vorne", "left": "links", "right": "rechts", "back": "hinten"}.get(x.side, x.side),
                                   _fmt(x.w if x.w is not None else w), _fmt(x.d),
                                   " (%s bei Gegenüber)" % _fmt(x.d_extra) if x.d_extra else "") for x in areas)
    return ("Keystone-Element %s %sx%s cm. Stellfläche %s × %s cm. Bewegungsfläche (%s): %s. Einsetzpunkt: Mitte "
            "der Wandseite. Quelle: %s" % (e["id"], _fmt(w), _fmt(d), _fmt(w), _fmt(d), mode, a or "-",
                                           kb.source_text(e.get("sources")) or "-"))


def _norm_items(items: list[dict], mpu: float) -> list[dict]:
    out = []
    for i, it in enumerate(items):
        try:
            n, _ = S.normalize(it, mpu, {})
        except (S.SpecError, KeyError, TypeError, ValueError, ArithmeticError) as ex:
            raise ToolError("item %d (%s): %s" % (i, it.get("type"), ex)) from ex
        out.append({k: v for k, v in n.items() if not k.startswith("_")})
    return out


def _area_items(placed: list[E.Placed], st: dict, origin_mm: tuple[float, float],
                room: E.Rect | None = None) -> list[dict]:
    """Movement areas as filled rectangles (mm, world), extra depth first,
    clipped to the room when there is one."""
    show = st.get("show") or {}
    if not show.get("movement", True):
        return []
    mov, _ = style.class_for(st, "movement")
    extra, _ = style.class_for(st, "movement_extra")
    parts = []
    for p in placed:
        for r, role in render.drawn_areas(p, room):
            if role == "movement_extra" and not show.get("movement_extra", True):
                continue
            parts.append((role != "movement_extra", r, extra if role == "movement_extra" else mov))
    parts.sort(key=lambda x: x[0])
    ox, oy = origin_mm
    return [{"type": "rect", "p1": [ox + r.x1 * 10, oy + r.y2 * 10], "p2": [ox + r.x2 * 10, oy + r.y1 * 10],
             "class": cls, "unit": "mm"} for _, r, cls in parts]


def _groups(placed: list[E.Placed], params_of: dict, st: dict, mode: str, origin_mm, mpu) -> tuple[list, dict]:
    """Symbol groups for place_elements: one per element+size, instances
    at their insertion points. Returns (groups, new_classes)."""
    by: dict[tuple, dict] = {}
    new_classes: dict[str, dict] = {}
    ox, oy = origin_mm
    for p in placed:
        e = _el(p.spec.id)
        w, d = p.spec.w, p.spec.d
        own = (st.get("symbols") or {}).get(e["id"])
        if own:
            key = ("own", own["symbol"])
            dx, dy = E.rot(float(own.get("dx", 0.0)), float(own.get("dy", 0.0)), p.deg)
            pos = (ox + (p.ix + dx) * 10, oy + (p.iy + dy) * 10)
            rotation = (p.deg + float(own.get("rotation", 0.0))) % 360.0
            if key not in by:
                obj, attrs = style.class_for(st, "object", e.get("category"))
                new_classes[obj] = attrs
                by[key] = {"symbol": own["symbol"], "items": [], "library": own.get("library"),
                           "cls": obj, "placements": [], "element": e["id"], "own": True}
        else:
            key = ("ks", e["id"], w, d)
            pos = (ox + p.ix * 10, oy + p.iy * 10)
            rotation = p.deg % 360.0
            if key not in by:
                its = element_items(e, w, d, params_of.get(p.tag, {}), st)
                obj, oattrs = style.class_for(st, "object", e.get("category"))
                det, dattrs = style.class_for(st, "detail", e.get("category"))
                new_classes.setdefault(obj, oattrs)
                new_classes.setdefault(det, dattrs)
                by[key] = {"symbol": symbol_name(e, w, d, st), "items": _norm_items(its, mpu),
                           "folder": "Keystone " + CAT_DE.get(e.get("category"), "Elemente"), "cls": obj,
                           "description": _description(e, w, d, mode), "placements": [], "element": e["id"]}
        loc, _ = S.normalize({"type": "locus", "point": [pos[0], pos[1]], "unit": "mm"}, mpu, {})
        by[key]["placements"].append({"position": list(loc["point"]), "rotation": rotation})
    for role in ("movement", "movement_extra"):
        n, a = style.class_for(st, role)
        new_classes.setdefault(n, a)
    return list(by.values()), new_classes


def _draw(placed: list[E.Placed], st: dict, mode: str, origin_mm, layer: str | None, tool: str,
          params_of: dict | None = None, extra_under: list[dict] | None = None,
          overlay: list[dict] | None = None, room: E.Rect | None = None) -> dict:
    units = _units()
    mpu = units["mm_per_unit"]
    if room is not None:
        E.resolve_shifts(placed, room.x2, room.y2)
    under = _norm_items((extra_under or [])[:1] + _area_items(placed, st, origin_mm, room)
                        + (extra_under or [])[1:], mpu)
    groups, new_classes = _groups(placed, params_of or {}, st, mode, origin_mm, mpu)
    for role in ("room", "door", "dimension", "label"):
        n, a = style.class_for(st, role)
        new_classes.setdefault(n, a)
    payload = [{k: v for k, v in g.items() if k not in ("element", "own")} for g in groups]
    res = app.vw2d("place_elements", timeout=WRITE_TIMEOUT, underlay=under, groups=payload,
                   overlay=_norm_items(overlay or [], mpu), layer=layer, new_classes=new_classes)
    ops: list[dict] = [{"op": "symfolder", "name": f} for f in res.get("folders_created", [])]
    ops += [{"op": "symdef", "name": n} for n in res.get("symbols_created", []) + res.get("symbols_imported", [])]
    ops += [{"op": "created", "uuid": r["uuid"]} for r in res.get("underlay", [])]
    for g in res.get("groups", []):
        ops += [{"op": "created", "uuid": r["uuid"]} for r in g["instances"]]
    ops += [{"op": "created", "uuid": r["uuid"]} for r in res.get("overlay", [])]
    _journal(res["document"], tool, ops)
    out = {"document": res["document"], "style": st["name"],
           "symbols": [{"symbol": g["symbol"], "element": src["element"], "own_symbol": bool(src.get("own")),
                        "instances": [r["uuid"] for r in g["instances"]]}
                       for g, src in zip(res.get("groups", []), groups)],
           "movement_areas": [r["uuid"] for r in res.get("underlay", [])]}
    for k in ("symbols_created", "symbols_imported", "folders_created", "classes_created"):
        if res.get(k):
            out[k] = res[k]
    return out


def _panel_symbols(lay: E.Layout, st: dict) -> list[list[dict]]:
    out = []
    for p in lay.placed:
        e = kb.elements().get(p.spec.id)
        kind = e.get("kind", "generic") if e else "generic"
        params = (e or {}).get("params") or {}
        out.append(symbols.draw(kind, p.spec.w, p.spec.d, st.get("geometry", "raumpilot"), **dict(params)))
    return out


def _preview(layouts: list[E.Layout], st: dict, titles: list[str] | None = None, bare: bool = False,
             caption: str | None = None) -> Image:
    panels = [{"layout": lay, "symbols": _panel_symbols(lay, st), "title": (titles or [None] * len(layouts))[i],
               "bare": bare, "caption": caption}
              for i, lay in enumerate(layouts)]
    return Image(data=render.render(panels, st, cols=min(3, len(panels)) or 1), format="png")


# ---- knowledge ------------------------------------------------------------------------

def _element_brief(e: dict) -> dict:
    return {"id": e["id"], "name": e["name"], "category": e.get("category"), "size_cm": [e["w"], e["d"]],
            "variants": [v.get("id") or v.get("label") for v in e.get("variants", [])]}


def _element_full(e: dict) -> dict:
    out = {k: v for k, v in e.items() if k not in ("sources", "keywords")}
    out["source"] = kb.source_text(e.get("sources"))
    sides = kb.sides()
    if e.get("short") and sides:
        out["side_distances_cm"] = {b: v for b, v in (sides.get(e["short"]) or {}).items()}
    return out


def _room_full(r: dict) -> dict:
    out = {k: v for k, v in r.items() if k not in ("sources", "keywords")}
    out["source"] = kb.source_text(r.get("sources"))
    out["standard_layouts"] = [{"id": lay["id"], "name": lay["name"], "size_cm": [lay["W"], lay["D"]],
                                "barrier_free": lay.get("barrier_free", False)}
                               for lay in kb.layouts().values() if lay.get("room") == r["id"]]
    return out


def _rule_out(r: dict) -> dict:
    out = {k: v for k, v in r.items() if k not in ("sources", "keywords")}
    out["source"] = kb.source_text(r.get("sources"))
    return out


@mcp.tool(annotations=READ, structured_output=False)
def vw2d_knowledge(
    query: str | None = None,
    topic: str | None = None,
    element: str | None = None,
    room: str | None = None,
    layout: str | None = None,
    mode: Literal["standard", "barrierefrei", "rollstuhlgerecht"] = "standard",
    preview: bool = True,
) -> list[Any]:
    """Planning knowledge from the Raumpilot books (Grundlagen, Wohnen,
    Arbeiten, Lernen; Wüstenrot Stiftung) and the norms they cite: what
    space every element needs (Stellfläche, Bewegungsfläche in front/beside,
    larger depth when objects face each other), side distances (VDI 6000),
    heights, barrier-free values (DIN 18040), room sizes, standard layouts
    ("Standardsituationen": WC-Raum, Duschbad, Bad, Küchenformen,
    Schlafzimmer, Essplatz, Büro-Arbeitsplatz, Klassenzimmer ...) and rules
    (stairs, doors, corridors, lifts, parking, escape routes, workplaces,
    schools, areas ...). Every value carries its source page.

    No arguments: overview (topics, rooms, element categories). query:
    words to search (German, e.g. "dusche", "treppe steigung", "flur
    schule"). topic: all rules of a topic. element / room / layout: full
    detail of one (layouts and elements with a preview in the active
    style; mode picks the movement areas: standard, barrierefrei,
    rollstuhlgerecht). Nothing is drawn; use vw2d_plan_room /
    vw2d_place_element for that."""
    st = _st(None)
    out: list[Any] = []
    if element:
        e = _el(element)
        info = {"element": _element_full(e)}
        out.append(json.dumps(info, ensure_ascii=False))
        if preview:
            w, d, params = kb.size_of(e)
            sp = kb.spec(element, mode)
            p = E.at_wall(sp, "N", 0.0, 0.0, 0.0)
            pad = max([a.d_extra or a.d for a in sp.areas] + [0.0]) + 20
            Wr = max(sp.w + 2 * max([((a.w or sp.w) - sp.w) / 2 for a in sp.areas] + [0.0]) + 40, sp.w + 40)
            Dr = sp.d + pad
            p = E.at_wall(sp, "N", (Wr - sp.w) / 2, Wr, Dr)
            lay = E.Layout(Wr, Dr, [p], name="%s (%s)" % (e["name"], mode))
            cap = "Stellfläche %s × %s cm; %s" % (_fmt(sp.w), _fmt(sp.d), "; ".join(
                "Bewegungsfläche %s %s × %s%s" % ({"front": "vorne", "left": "links", "right": "rechts"}.get(a.side, a.side),
                                                 _fmt(a.w if a.w is not None else sp.w), _fmt(a.d),
                                                 " (%s gegenüber)" % _fmt(a.d_extra) if a.d_extra else "")
                for a in sp.areas) or "keine Bewegungsfläche")
            out.append(_preview([lay], st, bare=True, caption=cap))
        return out
    if room:
        r = kb.rooms().get(room)
        if r is None:
            raise ToolError("unknown room %r (rooms: %s)" % (room, ", ".join(kb.rooms())))
        info = {"room": _room_full(r)}
        out.append(json.dumps(info, ensure_ascii=False))
        if preview:
            lays = [lay for lay in kb.layouts().values() if lay.get("room") == room][:9]
            fitted = [kb.fit(lay, float(lay["W"]), float(lay["D"])) for lay in lays]
            fitted = [f for f in fitted if f is not None]
            for f in fitted:
                f.issues = E.check(f.placed, f.W, f.D, f.doors, kb.sides(), kb.door_reveal())
            if fitted:
                out.append(_preview(fitted, st))
        return out
    if layout:
        lay = kb.layouts().get(layout)
        if lay is None:
            raise ToolError("unknown layout %r" % layout)
        info = {"layout": {k: v for k, v in lay.items() if k != "sources"}, "source": kb.source_text(lay.get("sources"))}
        out.append(json.dumps(info, ensure_ascii=False))
        f = kb.fit(lay, float(lay["W"]), float(lay["D"]), mode=mode)
        if preview and f is not None:
            f.issues = E.check(f.placed, f.W, f.D, f.doors, kb.sides(mode), kb.door_reveal())
            out.append(_preview([f], st))
        return out
    if topic:
        rs = [_rule_out(r) for r in kb.rules() if r.get("topic") == topic]
        if not rs:
            raise ToolError("no rules for topic %r (topics: %s)" % (topic, ", ".join(kb.topics())))
        out.append(json.dumps({"topic": topic, "count": len(rs), "rules": rs}, ensure_ascii=False))
        return out
    if query:
        hits = kb.search(query)
        info = {"query": query,
                "elements": [_element_brief(kb.elements()[i]) for i in hits["elements"]],
                "rooms": [{"id": i, "name": kb.rooms()[i]["name"]} for i in hits["rooms"]],
                "layouts": [{"id": i, "name": kb.layouts()[i]["name"], "room": kb.layouts()[i].get("room"),
                             "size_cm": [kb.layouts()[i]["W"], kb.layouts()[i]["D"]]} for i in hits["layouts"]],
                "rules": [_rule_out(r) for r in hits["rules"]]}
        if not any(info[k] for k in ("elements", "rooms", "layouts", "rules")):
            info["hint"] = "nothing found; topics: %s" % ", ".join(kb.topics())
        out.append(json.dumps(info, ensure_ascii=False))
        return out
    cats: dict[str, list[str]] = {}
    for e in kb.catalog().values():
        cats.setdefault(e.get("category", "sonstiges"), []).append(e["id"])
    info = {"sources": list(kb.BOOKS.values()),
            "elements_by_category": cats,
            "rooms": {i: r["name"] for i, r in kb.rooms().items()},
            "standard_layouts": len(kb.layouts()),
            "rule_topics": kb.topics(),
            "active_style": st["name"],
            "hint": "query=..., topic=..., element=..., room=..., layout=... for details"}
    out.append(json.dumps(info, ensure_ascii=False))
    return out


# ---- style -------------------------------------------------------------------------------

@mcp.tool(annotations=WRITE)
def vw2d_style(
    action: Literal["show", "list", "use", "save", "delete"] = "show",
    name: str | None = None,
    settings: dict[str, Any] | None = None,
    activate: bool = True,
) -> dict:
    """How planning elements are drawn. Keystone's own style "raumpilot"
    (the look of the Raumpilot books: plain white outlines with rounded
    corners, light-blue movement areas, cm dimensions) is the first layer;
    a user style overrides parts of it and is kept on this Mac
    (~/.vwmcp/styles.json) for every document.

    action: show (the active or named style, fully resolved), list, use
    (make a style active), save (create/update a user style from
    `settings`; activate=True makes it active), delete.
    settings (all optional): {"base": "raumpilot" | other style,
    "classes": {role: {"name", "pen", "fill", "line_weight", "line_style"}}
    with roles object, detail ("{category}" in a name = the category class),
    movement, movement_extra, movement_bf, room, door, dimension, label;
    "category_classes": {"sanitaer": "A-Sanitär", ...};
    "show": {"movement": true, "movement_extra": true, "dimensions": false,
    "labels": false, "room": true, "doors": true}; "geometry": "raumpilot" |
    "outline"; "symbols": {element id: {"symbol": "<the office's symbol
    name>", "library": "/path/Bibliothek.vwx" (optional, imported when
    missing), "dx": cm, "dy": cm, "rotation": deg} | null to remove} - dx/dy
    and rotation map Keystone's insertion point (middle of the wall side,
    object towards -y) to the symbol's own insertion point."""
    try:
        if action == "list":
            return {"active": style.active_name(), "styles": style.names(), "file": str(style.store_path())}
        if action == "use":
            if not name:
                raise ToolError("name is required")
            style.activate(name)
            return {"active": name}
        if action == "save":
            if not name or settings is None:
                raise ToolError("name and settings are required")
            saved = style.save(name, settings, activate)
            return {"saved": name, "active": style.active_name(), "overrides": saved, "resolved": style.resolve(name)}
        if action == "delete":
            if not name:
                raise ToolError("name is required")
            style.delete(name)
            return {"deleted": name, "active": style.active_name()}
        st = style.resolve(name)
        return {"active": style.active_name(), "style": st}
    except style.StyleError as e:
        raise ToolError(str(e)) from e


# ---- placing elements ----------------------------------------------------------------------

WALL_DE = {"above": "N", "left": "W", "below": "S", "right": "E", "N": "N", "W": "W", "S": "S", "E": "E",
           "oben": "N", "links": "W", "unten": "S", "rechts": "E", "nord": "N", "west": "W", "süd": "S",
           "sued": "S", "ost": "E", "O": "E"}


def _deg(rotation, wall, where: str) -> float:
    if wall is not None:
        w = WALL_DE.get(wall)
        if w is None:
            raise ToolError("%s: wall must be above/left/below/right (or N/W/S/E)" % where)
        return E.WALL_ROT[w]
    try:
        r = float(rotation)
    except (TypeError, ValueError):
        raise ToolError("%s: rotation must be a number (degrees)" % where) from None
    if not math.isfinite(r):
        raise ToolError("%s: rotation must be finite" % where)
    return r % 360.0


@mcp.tool(annotations=WRITE)
def vw2d_place_element(
    element: str,
    position: list[float] = [0.0, 0.0],  # noqa: B006  (JSON default, never mutated)
    wall: Literal["above", "left", "below", "right"] | None = None,
    rotation: float = 0.0,
    variant: str | None = None,
    width: float | None = None,
    depth: float | None = None,
    mode: Literal["standard", "barrierefrei", "rollstuhlgerecht"] = "standard",
    movement: bool | None = None,
    placements: list[dict[str, Any]] | None = None,
    unit: Literal["document", "mm", "cm", "m"] = "document",
    layer: str | None = None,
    style_name: str | None = None,
) -> dict:
    """Place planning elements (see vw2d_knowledge) with their movement
    areas, in the active drawing style - Keystone's Raumpilot symbols or
    the office's own symbols if the style maps this element.

    position: insertion point in `unit` = the middle of the element's wall
    side; unrotated the element reaches from the wall towards -y (wall
    above it). wall: where the wall is, seen from the element ("above" 0°,
    "left" 90°, "below" 180°, "right" 270°) - it then faces into the room;
    or rotation in degrees (ccw). variant / width / depth (cm): another
    size from the element's variants or a free size. mode: which movement
    areas (standard, barrierefrei = DIN 18040 barrier-free, rollstuhlgerecht
    = wheelchair). movement: draw the movement areas (default: the style's
    show.movement) - light blue from the wall to the required depth, the
    extra depth for opposite arrangement lighter. placements: [{"position",
    "wall" | "rotation"}] for several at once. One undo step;
    vw_revert_last removes it (and symbol definitions this call made)."""
    e = _el(element)
    st = _st(style_name)
    if movement is not None:
        st = dict(st)
        st["show"] = dict(st.get("show") or {}, movement=bool(movement))
    try:
        sp = kb.spec(element, mode, variant, width, depth)
        _, _, params = kb.size_of(e, variant, width, depth)
    except KeyError as ex:
        raise ToolError(str(ex.args[0])) from ex
    if sp.w <= 0 or sp.d <= 0 or sp.w > 10000 or sp.d > 10000:
        raise ToolError("width/depth must be > 0 and at most 100 m")
    units = _units()
    f = _cm_factor(unit, units)
    pls = placements or [{"position": position, "wall": wall, "rotation": rotation}]
    if len(pls) > MAX_PLACEMENTS:
        raise ToolError("at most %d placements per call" % MAX_PLACEMENTS)
    placed = []
    for i, pl in enumerate(pls):
        try:
            x, y = (float(v) * f for v in pl["position"])
        except (KeyError, TypeError, ValueError):
            raise ToolError("placement %d needs a position [x, y]" % i) from None
        deg = _deg(pl.get("rotation", 0.0), pl.get("wall"), "placement %d" % i)
        placed.append(E.Placed(sp, x, y, deg, E.ROT_WALL.get(deg), tag="%s#%d" % (element, i + 1)))
    out = _draw(placed, st, mode, (0.0, 0.0), layer, "vw2d_place_element",
                params_of={p.tag: params for p in placed})
    out.update({"element": element, "size_cm": [sp.w, sp.d], "mode": mode, "placed": len(placed)})
    return out


# ---- planning a room -------------------------------------------------------------------------

def _elements_arg(elements, room: dict | None) -> list[dict]:
    raw = elements if elements else (room or {}).get("requires") or []
    out = []
    for i, x in enumerate(raw):
        if isinstance(x, str):
            x = {"element": x}
        if not isinstance(x, dict) or "element" not in x:
            raise ToolError("elements[%d]: an element id or {'element': id, 'variant', 'width', 'depth', 'count'}" % i)
        n = int(x.get("count", 1))
        if not 1 <= n <= 30:
            raise ToolError("elements[%d]: count 1..30" % i)
        for _ in range(n):
            out.append(x)
    if not out:
        raise ToolError("which elements? give elements=[...] or a room with requirements")
    if len(out) > 12:
        raise ToolError("at most 12 elements per room (for bigger rooms place groups with vw2d_place_element)")
    return out


def _door_arg(door: dict | None, f: float) -> list[E.Door]:
    if not door:
        return []
    try:
        wall = WALL_DE.get(door.get("wall"), door.get("wall"))
        if wall not in ("N", "E", "S", "W"):
            raise ValueError("wall N/E/S/W (or above/right/below/left)")
        return [E.Door(wall, float(door["offset"]) * f, float(door.get("width", 80.0 / f)) * f,
                       door.get("swing", "in"), door.get("hinge", "left"))]
    except (KeyError, TypeError, ValueError) as ex:
        raise ToolError("door: {'wall': 'S', 'offset': in plan from the room's bottom-left corner (x on N/S, y on "
                        "W/E walls), "
                        "'width', 'swing': 'in'|'out'|null, 'hinge': 'left'|'right'} (%s)" % ex) from None


def _signature(lay: E.Layout) -> tuple:
    return tuple(sorted((p.spec.id, round(p.rect.x1, 1), round(p.rect.y1, 1), round(p.rect.w, 1)) for p in lay.placed))


def plan_variants(elements: list[dict], W: float, D: float, mode: str, doors: list[E.Door], room_id: str | None,
                  limit: int) -> list[E.Layout]:
    """Book layouts fitted to the room first, then generated variants."""
    sides = kb.sides(mode)
    want = sorted(x["element"] for x in elements)
    cands: list[E.Layout] = []
    for lay in kb.layouts().values():
        if room_id and lay.get("room") != room_id and sorted(e["element"] for e in lay["elements"]) != want:
            continue
        if not room_id and sorted(e["element"] for e in lay["elements"]) != want:
            continue
        if bool(lay.get("barrier_free")) != (mode != "standard") and lay.get("barrier_free") is not None:
            continue
        for turn in range(4):
            for mirror in (False, True):
                f = kb.fit(lay, W, D, turn, mirror, mode)
                if f is None:
                    continue
                if doors:
                    f.doors = list(doors)
                f.issues = E.check(f.placed, W, D, f.doors, sides, kb.door_reveal())
                f.score = E._score(f) - 5  # the book's solutions first among equals
                f.name = "%s (Raumpilot)" % lay["name"]
                cands.append(f)
    specs = []
    for i, x in enumerate(elements):
        try:
            specs.append(kb.spec(x["element"], mode, x.get("variant"), x.get("width"), x.get("depth"),
                                 tag="%s#%d" % (x["element"], i + 1)))
        except KeyError as ex:
            raise ToolError(str(ex.args[0])) from ex
    wall_specs = [s for s in specs if s.wall]
    free = [s for s in specs if not s.wall]
    if len(wall_specs) > 6:
        # too many for free combination (kitchen rows ...): the book's situations only
        gen = []
    else:
        gen = E.generate(wall_specs, W, D, doors, sides, limit=max(limit * 3, 12), door_reveal=kb.door_reveal()) \
            if wall_specs else [E.Layout(W, D, [], list(doors), name="Variante")]
    for g in gen:
        for s in free:  # free-standing: in the middle of the free floor
            g.placed.append(E.Placed(s, W / 2, D / 2 + s.d / 2, 0.0, None, s.name))
        if free:
            g.issues = E.check(g.placed, W, D, g.doors, sides, kb.door_reveal())
            g.score = E._score(g)
        g.source = "von Keystone erzeugt nach den Raumpilot-Regeln"
    cands += gen
    cands.sort(key=lambda lay: lay.score)
    out, seen = [], set()
    for c in cands:
        sig = _signature(c)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(c)
    good = [c for c in out if not any(i.severity == "error" for i in c.issues)]
    pick = good[:limit] if good else out[:limit]
    for i, c in enumerate(pick):
        c.name = "%d · %s" % (i + 1, c.name.split(" · ")[-1])
    return pick


def _room_overlay(lay: E.Layout, st: dict, origin_mm, outline: bool, dims: bool) -> tuple[list, list]:
    ox, oy = origin_mm
    under, over = [], []
    room_cls, _ = style.class_for(st, "room")
    door_cls, _ = style.class_for(st, "door")
    dim_cls, _ = style.class_for(st, "dimension")
    W, D = lay.W * 10, lay.D * 10
    if outline:
        under.append({"type": "polygon", "points": [[ox, oy], [ox + W, oy], [ox + W, oy + D], [ox, oy + D]],
                      "class": room_cls, "unit": "mm", "attributes": {"fill": "none"}})
    for dr in lay.doors:
        sp = dr.span(lay.W, lay.D)
        if dr.swing not in ("in", "out"):
            continue
        normal = {"N": (0, -1), "S": (0, 1), "W": (1, 0), "E": (-1, 0)}[dr.wall]
        if dr.swing == "out":
            normal = (-normal[0], -normal[1])
        ends = [(sp.x1, sp.y1), (sp.x2, sp.y2)]
        left_first = {"N": True, "E": False, "S": False, "W": True}[dr.wall]
        hinge = ends[0] if (dr.hinge == "left") == left_first else ends[1]
        other = ends[1] if hinge == ends[0] else ends[0]
        leaf = (hinge[0] + normal[0] * dr.width, hinge[1] + normal[1] * dr.width)
        a0 = math.degrees(math.atan2(other[1] - hinge[1], other[0] - hinge[0]))
        a1 = math.degrees(math.atan2(leaf[1] - hinge[1], leaf[0] - hinge[0]))
        sweep = (a1 - a0 + 540.0) % 360.0 - 180.0
        H = (ox + hinge[0] * 10, oy + hinge[1] * 10)
        over.append({"type": "line", "p1": list(H), "p2": [ox + leaf[0] * 10, oy + leaf[1] * 10],
                     "class": door_cls, "unit": "mm"})
        over.append({"type": "arc", "center": list(H), "radius": dr.width * 10, "start_angle": round(a0, 9),
                     "sweep": round(sweep, 9), "class": door_cls, "unit": "mm"})
    if dims:
        xs = sorted({0.0, lay.W} | {v for p in lay.placed if p.wall in ("S", "N") for v in (p.rect.x1, p.rect.x2)})
        ys = sorted({0.0, lay.D} | {v for p in lay.placed if p.wall in ("W", "E") for v in (p.rect.y1, p.rect.y2)})
        if len(xs) >= 3:
            over.append({"type": "chain_dimension", "points": [[ox + x * 10, oy + D] for x in xs], "offset": 300,
                         "orientation": "horizontal", "class": dim_cls, "unit": "mm"})
        else:
            over.append({"type": "dimension", "p1": [ox, oy + D], "p2": [ox + W, oy + D], "offset": 300,
                         "orientation": "horizontal", "class": dim_cls, "unit": "mm"})
        if len(ys) >= 3:
            over.append({"type": "chain_dimension", "points": [[ox, oy + y * 10] for y in ys], "offset": 300,
                         "orientation": "vertical", "class": dim_cls, "unit": "mm"})
        else:
            over.append({"type": "dimension", "p1": [ox, oy], "p2": [ox, oy + D], "offset": 300,
                         "orientation": "vertical", "class": dim_cls, "unit": "mm"})
    return under, over


@mcp.tool(annotations=WRITE, structured_output=False)
def vw2d_plan_room(
    width: float | None = None,
    depth: float | None = None,
    room: str | None = None,
    elements: list[Any] | None = None,
    layout: str | None = None,
    unit: Literal["cm", "mm", "m", "document"] = "cm",
    mode: Literal["standard", "barrierefrei", "rollstuhlgerecht"] = "standard",
    door: dict[str, Any] | None = None,
    variants: int = 4,
    draw: bool = False,
    variant: int = 1,
    origin: list[float] = [0.0, 0.0],  # noqa: B006  (JSON default, never mutated)
    origin_unit: Literal["document", "mm", "cm", "m"] = "document",
    layer: str | None = None,
    outline: bool | None = None,
    dimensions: bool | None = None,
    label: str | None = None,
    style_name: str | None = None,
    preview: bool = True,
) -> list[Any]:
    """Plan a room from the Raumpilot knowledge: layout variants for a
    rectangular room of width x depth (interior, `unit`), checked against
    the rules (Stellflächen, movement areas, side distances, opposite
    arrangement, door distances and swing; barrier-free/wheelchair with
    mode). The book's standard situations come first (turned/mirrored and
    fitted to the size), then variants Keystone generates with the same
    rules. Width runs along x, depth along y.

    room: a room type (vw2d_knowledge lists them, e.g. wc_raum, duschbad,
    bad) - its required elements are used unless `elements` is given.
    elements: ids or {"element", "variant", "width", "depth", "count"}.
    layout: one of the book's standard layouts (vw2d_knowledge room=...
    lists them) - exactly that one, at its own size or fitted to
    width x depth.
    door: {"wall": "S"|"N"|"E"|"W" (or below/above/right/left), "offset":
    in plan from the room's bottom-left interior corner to the opening (x
    on the N/S walls, y on the W/E walls), "width", "swing":
    "in"|"out"|null, "hinge": "left"|"right" seen from inside the room
    (inward door: DIN links/rechts)} in `unit`.
    Returns the variants (positions in cm from the room's bottom-left
    corner, problems) and a preview. draw=True draws variant `variant` with
    the room's bottom-left interior corner at `origin`: movement areas,
    elements (style symbols), and - per style or the outline/dimensions
    flags - the room outline, the door and dimension chains; label: a text
    under the room (e.g. its name and size). One undo step."""
    st = _st(style_name)
    units = _units() if unit == "document" or draw else None
    f = _cm_factor(unit, units)
    book = None
    if layout:
        book = kb.layouts().get(layout)
        if book is None or book.get("reference_only"):
            raise ToolError("unknown layout %r (vw2d_knowledge room=... lists the standard layouts)" % layout)
        if width is None or depth is None:
            width, depth, f = book["W"], book["D"], 1.0
    try:
        W, D = float(width) * f, float(depth) * f
    except (TypeError, ValueError):
        raise ToolError("width and depth must be numbers (or give a layout)") from None
    if not (30 <= W <= 5000 and 30 <= D <= 5000):
        raise ToolError("room size must be between 0.3 m and 50 m")
    r = None
    if room:
        r = kb.rooms().get(room)
        if r is None:
            raise ToolError("unknown room %r (rooms: %s)" % (room, ", ".join(kb.rooms())))
    doors = _door_arg(door, f)
    n = max(1, min(int(variants), MAX_VARIANTS))
    if book is not None:
        mode = book.get("mode") or mode
        lays = []
        for turn in range(4):
            for mirror in (False, True):
                fl = kb.fit(book, W, D, turn, mirror, mode)
                if fl is None:
                    continue
                if doors:
                    fl.doors = list(doors)
                fl.issues = E.check(fl.placed, W, D, fl.doors, kb.sides(mode), kb.door_reveal())
                fl.score = E._score(fl) + (turn + mirror) * 0.1  # as drawn in the book first
                fl.name = "%s (Raumpilot)" % book["name"]
                fl.source = kb.source_text(book.get("sources"))
                lays.append(fl)
        if not lays:
            raise ToolError("layout %r (%g × %g cm) doesn't fit into %g × %g cm" % (layout, book["W"], book["D"], W, D))
        lays.sort(key=lambda x: x.score)
        lays = lays[:n]
        room = room or book.get("room")
        r = kb.rooms().get(room) if room else None
    else:
        els = _elements_arg(elements, r)
        lays = plan_variants(els, W, D, mode, doors, room if not elements else None, n)
    info: dict[str, Any] = {"room": room, "size_cm": [W, D], "mode": mode, "variants": []}
    for lay in lays:
        d = lay.to_dict()
        d["ok"] = not any(i.severity == "error" for i in lay.issues)
        info["variants"].append(d)
    if r is not None:
        info["room_rules"] = r.get("rules", [])
        info["room_min"] = r.get("min")
    if not any(v["ok"] for v in info["variants"]):
        info["hint"] = ("no variant without problems at this size - see the problems; a bigger room, other "
                        "elements/sizes (variant) or another door position may help")
    out: list[Any] = []
    if draw:
        if not 1 <= variant <= len(lays):
            raise ToolError("variant must be 1..%d" % len(lays))
        lay = lays[variant - 1]
        fo = _cm_factor(origin_unit, units) * 10.0  # mm per origin unit
        o = (float(origin[0]) * fo, float(origin[1]) * fo)
        show = st.get("show") or {}
        under, over = _room_overlay(lay, st, o, show.get("room", True) if outline is None else outline,
                                    show.get("dimensions", False) if dimensions is None else dimensions)
        if label:
            lab, _ = style.class_for(st, "label")
            over.append({"type": "text", "text": str(label)[:200], "position": [o[0], o[1] - 250.0],
                         "size": float(st.get("text_size_pt", 7.0)), "valign": "top", "class": lab, "unit": "mm"})
        res = _draw(lay.placed, st, mode, o, layer, "vw2d_plan_room", extra_under=under, overlay=over,
                    room=E.Rect(0.0, 0.0, lay.W, lay.D))
        info["drawn"] = {"variant": variant, **res}
    out.append(json.dumps(info, ensure_ascii=False))
    if preview and lays:
        out.append(_preview(lays, st))
    return out


# ---- checking a drawn room ------------------------------------------------------------------

@mcp.tool(annotations=READ, structured_output=False)
def vw2d_check_room(
    room_uuid: str | None = None,
    bbox: list[float] | None = None,
    layer: str | None = None,
    mode: Literal["standard", "barrierefrei", "rollstuhlgerecht"] = "standard",
    door: dict[str, Any] | None = None,
    style_name: str | None = None,
    preview: bool = True,
) -> list[Any]:
    """Check a drawn rectangular room against the planning rules: the
    room is a rectangle/polygon (room_uuid) or bbox [x1, y1, x2, y2]
    (document units); the elements are the symbol instances inside it -
    Keystone's elements (recognised by their symbol description) and the
    office's own symbols mapped in the style. door (cm, as in
    vw2d_plan_room) adds door distances and swing. Returns the problems
    (movement areas blocked or outside, overlaps, side distances, opposite
    arrangement, door) and a preview."""
    st = _st(style_name)
    units = _units()
    cm = _cm_factor("document", units)
    if room_uuid:
        recs = app.vw2d("read_objects", uuids=[room_uuid], geometry=True, limit=1)
        objs = recs.get("objects") or []
        if not objs:
            raise ToolError("room %s not found" % room_uuid)
        bb = objs[0].get("bbox")
        layer = layer or objs[0].get("layer")
    elif bbox and len(bbox) == 4:
        bb = [float(v) for v in bbox]
    else:
        raise ToolError("give room_uuid or bbox")
    x0, y0, x1, y1 = min(bb[0], bb[2]), min(bb[1], bb[3]), max(bb[0], bb[2]), max(bb[1], bb[3])
    W, D = (x1 - x0) * cm, (y1 - y0) * cm
    inst = app.vw2d("element_instances", layer=layer)
    own = {v["symbol"]: (k, v) for k, v in (st.get("symbols") or {}).items() if v}
    placed, unknown = [], []
    for it in inst:
        lx, ly = it["location"]
        if not (x0 - 1e-9 <= lx <= x1 + 1e-9 and y0 - 1e-9 <= ly <= y1 + 1e-9):
            continue
        m = DESC_RE.search(it.get("description") or "")
        rot = float(it.get("rotation") or 0.0) % 360.0
        if m:
            eid, w, d = m.group(1), float(m.group(2)), float(m.group(3))
            ix, iy = (lx - x0) * cm, (ly - y0) * cm
        elif it["symbol"] in own:
            eid, mp = own[it["symbol"]]
            e = kb.elements().get(eid)
            if e is None:
                unknown.append(it["symbol"])
                continue
            w, d = float(mp.get("w", e["w"])), float(mp.get("d", e["d"]))
            rot = (rot - float(mp.get("rotation", 0.0))) % 360.0
            dx, dy = E.rot(float(mp.get("dx", 0.0)), float(mp.get("dy", 0.0)), rot)
            ix, iy = (lx - x0) * cm - dx, (ly - y0) * cm - dy
        else:
            unknown.append(it["symbol"])
            continue
        try:
            sp = kb.spec(eid, mode, w=w, d=d)
        except KeyError:
            unknown.append(it["symbol"])
            continue
        wall = None
        for wl, deg in E.WALL_ROT.items():
            if abs(((rot - deg) + 180) % 360 - 180) < 1e-6:
                p = E.Placed(sp, ix, iy, deg, None)
                r = p.rect
                touching = {"N": abs(r.y2 - D), "S": abs(r.y1), "W": abs(r.x1), "E": abs(r.x2 - W)}[wl]
                wall = wl if touching < 1.0 else None
        placed.append(E.Placed(sp, ix, iy, rot, wall, tag="%s (%s)" % (sp.name, it["uuid"][:8])))
    doors = _door_arg(door, 1.0)
    issues = E.check(placed, W, D, doors, kb.sides(mode), kb.door_reveal())
    lay = E.Layout(W, D, placed, doors, issues, name="Prüfung")
    info = {"room_cm": [round(W, 3), round(D, 3)], "mode": mode, "elements": [p.to_dict() for p in placed],
            "issues": [i.to_dict() for i in issues], "ok": not any(i.severity == "error" for i in issues)}
    if unknown:
        info["not_recognised"] = sorted(set(unknown))
        info["hint_unknown"] = ("symbols that are neither Keystone elements nor mapped in the style are ignored; "
                                "map the office's symbols with vw2d_style(save, settings={'symbols': {...}})")
    out: list[Any] = [json.dumps(info, ensure_ascii=False)]
    if preview:
        out.append(_preview([lay], st))
    return out
