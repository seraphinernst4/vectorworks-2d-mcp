"""Keystone asset catalogue: exact 2D symbols for architecture plans.

Each asset can be drawn in one or more views:
  plan   Grundriss (from above)
  front  Ansicht von vorne
  side   Ansicht von der Seite / Schnitt
Coordinates are mm. Plan: wall-mounted things stand at a wall on y = 0 and
reach into the room towards -y (insertion point: middle of the wall side);
free-standing things are centred on 0, 0. Elevations: floor at y = 0,
insertion point at the floor in the middle (front) or at the wall (side,
object towards +x).

Dimensions follow common German practice (DIN 18022 / DIN 18040 / typical
product sizes) and can be changed per call.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field

from vwmcp.catalog import assets_furniture, assets_people_plants, assets_sanitary


@dataclass
class Asset:
    id: str
    name: str                      # German display name
    category: str                  # sanitary | kitchen | furniture | plants | people | vehicles
    views: dict[str, Callable[..., list[dict]]]
    params: dict[str, tuple[float, str]] = field(default_factory=dict)  # name -> (default, meaning)
    insertion: str = ""
    keywords: tuple[str, ...] = ()


CATEGORY_CLASS = {
    "sanitary": "Ausstattung-Sanitär",
    "kitchen": "Ausstattung-Küche",
    "furniture": "Ausstattung-Möbel",
    "plants": "Staffage-Vegetation",
    "people": "Staffage-Personen",
    "vehicles": "Staffage-Fahrzeuge",
}
# Attributes of those classes when Keystone has to create them (outline / detail).
CLASS_ATTRS = {
    "Ausstattung-Sanitär": {"pen": "#404040", "fill": "#ffffff", "line_weight": 0.18},
    "Ausstattung-Küche": {"pen": "#404040", "fill": "#ffffff", "line_weight": 0.18},
    "Ausstattung-Möbel": {"pen": "#404040", "fill": "#ffffff", "line_weight": 0.18},
    "Staffage-Vegetation": {"pen": "#606060", "fill": "none", "line_weight": 0.18},
    "Staffage-Personen": {"pen": "#606060", "fill": "none", "line_weight": 0.18},
    "Staffage-Fahrzeuge": {"pen": "#606060", "fill": "#ffffff", "line_weight": 0.18},
}
DETAIL_SUFFIX = "-Detail"
DETAIL_ATTRS = {"pen": "#7f7f7f", "fill": "none", "line_weight": 0.13}
CATEGORY_DE = {"sanitary": "Sanitär", "kitchen": "Küche", "furniture": "Möbel",
               "plants": "Vegetation", "people": "Personen", "vehicles": "Fahrzeuge"}
VIEW_DE = {"plan": "Grundriss", "front": "Ansicht", "side": "Seitenansicht"}
FOLDER_PREFIX = "Keystone "
NAME_MAX = 63  # Vectorworks object names: keep within the classic limit

CATALOG: dict[str, Asset] = {}
SIZES = {"b", "t", "h", "l", "d", "w", "trunk"}  # dimensions: must be > 0
MAX_MM = 100000.0  # 100 m: no asset dimension or position beyond this
CHOICES = {"bowls": (1, 2), "gully": (0, 1), "mirror": (0, 1), "round_table": (0, 1), "style": (0, 1, 2)}
RANGES = {"chairs": (0, 24, True), "seed": (0, 1000, False)}  # (min, max, whole numbers only)


def register(asset: Asset) -> Asset:
    CATALOG[asset.id] = asset
    return asset


for _mod in (assets_sanitary, assets_furniture, assets_people_plants):
    for _a in _mod.ASSETS:
        register(Asset(**_a))


def find(query: str | None = None, category: str | None = None) -> list[Asset]:
    q = (query or "").lower().split()
    out = []
    for a in CATALOG.values():
        if category and a.category != category:
            continue
        hay = " ".join((a.id, a.name, a.category, " ".join(a.keywords))).lower()
        if all(w in hay for w in q):
            out.append(a)
    return out


def build(asset_id: str, view: str = "plan", params: dict | None = None) -> list[dict]:
    """Items (mm) of one view of an asset with the given parameters."""
    a = CATALOG.get(asset_id)
    if a is None:
        raise KeyError("unknown asset %r (see vw2d_assets)" % asset_id)
    if view not in a.views:
        raise KeyError("asset %r has no view %r (views: %s)" % (asset_id, view, ", ".join(a.views)))
    p = {k: v[0] for k, v in a.params.items()}
    for k, v in (params or {}).items():
        if k not in a.params:
            raise KeyError("asset %r has no parameter %r (parameters: %s)"
                           % (asset_id, k, ", ".join(a.params) or "none"))
        try:
            p[k] = float(v)
        except (TypeError, ValueError):
            raise KeyError("parameter %r must be a number, got %r" % (k, v)) from None
        _check_param(k, p[k], a.params[k][1])
    fn = a.views[view]
    accepted = inspect.signature(fn).parameters
    return fn(**{k: v for k, v in p.items() if k in accepted})


def _check_param(k: str, v: float, meaning: str) -> None:
    if v != v or abs(v) == float("inf"):
        raise KeyError("parameter %r is not a number" % k)
    if k in CHOICES:
        if v not in CHOICES[k]:
            raise KeyError("parameter %r (%s) must be one of %s"
                           % (k, meaning, ", ".join(str(c) for c in CHOICES[k])))
    elif k in RANGES:
        lo, hi, whole = RANGES[k]
        if not lo <= v <= hi or (whole and v != int(v)):
            raise KeyError("parameter %r (%s) must be a %s from %g to %g"
                           % (k, meaning, "whole number" if whole else "number", lo, hi))
    elif k in SIZES:
        if not 0 < v <= MAX_MM:
            raise KeyError("parameter %r (%s) must be > 0 and at most %g mm" % (k, meaning, MAX_MM))
    elif not 0 <= v <= MAX_MM:  # positions and widths along the asset (0 = none)
        raise KeyError("parameter %r (%s) must be from 0 to %g mm" % (k, meaning, MAX_MM))


def resolve_params(asset_id: str, view: str, params: dict | None = None) -> dict:
    """Parameters the view actually uses: defaults overridden by params."""
    a = CATALOG[asset_id]
    p = {k: v[0] for k, v in a.params.items()}
    p.update({k: float(v) for k, v in (params or {}).items() if k in a.params})
    accepted = inspect.signature(a.views[view]).parameters
    return {k: v for k, v in p.items() if k in accepted}


def symbol_name(asset_id: str, view: str, params: dict | None = None, mirrored: bool = False) -> str:
    """Deterministic symbol name for one asset view + parameter set, e.g.
    'WC wandhängend - Grundriss' or 'Waschtisch - Ansicht (b=800, h=900) gespiegelt'.
    Only parameters the view uses and that differ from the defaults appear."""
    a = CATALOG[asset_id]
    used = resolve_params(asset_id, view, params)
    diff = [(k, v) for k, v in used.items() if v != a.params[k][0]]
    name = "%s - %s" % (a.name, VIEW_DE.get(view, view))
    if diff:
        name += " (%s)" % ", ".join("%s=%s" % (k, "%.10g" % v) for k, v in diff)
    if mirrored:
        name += " gespiegelt"
    if len(name) > NAME_MAX:
        import hashlib

        h = hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]
        name = name[:NAME_MAX - 8].rstrip() + " #" + h
    return name


def folder_name(asset_id: str) -> str:
    return FOLDER_PREFIX + CATEGORY_DE[CATALOG[asset_id].category]


def describe(a: Asset) -> dict:
    return {"id": a.id, "name": a.name, "category": a.category, "views": list(a.views),
            "class": CATEGORY_CLASS[a.category],
            "params": {k: {"default": v[0], "meaning": v[1]} for k, v in a.params.items()},
            "insertion": a.insertion}
