"""Drawing styles: how planning elements look in Vectorworks.

Knowledge (what an element needs: Stellfläche, Bewegungsfläche, distances)
and representation (lines, colours, classes, which symbols) are separate.
Keystone's own style "raumpilot" is the first layer: drawn like the
Raumpilot books - plain outlines with rounded corners, white fill, thin dark
lines; movement areas light blue from the wall to the required depth, the
extra depth for opposite arrangement in a lighter blue. A user style is a
set of overrides on top of it (or on top of another user style): other
classes and colours, movement areas off, and - most important for offices
with their own libraries - the office's own symbols per element.

User styles live in ~/.vwmcp/styles.json:
  {"active": "buero-x", "styles": {"buero-x": {"base": "raumpilot", ...}}}
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

DEFAULT = "raumpilot"
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
ROLES = ("object", "detail", "movement", "movement_extra", "movement_bf", "room", "door", "dimension", "label")
SHOW = ("movement", "movement_extra", "dimensions", "labels", "room", "doors")
CATEGORIES = ("sanitaer", "kueche", "essen", "wohnen", "schlafen", "kinder", "arbeiten", "lernen",
              "wirtschaften", "aufbewahren", "entsorgen", "verkehr", "freiraum", "person", "sonstiges")

RAUMPILOT: dict = {
    "name": "raumpilot",
    "label": "Raumpilot (Keystone-Standard)",
    "description": ("Wie in den Raumpilot-Bänden: Objekte als schlichte Umrisse mit runden Ecken, weiß "
                    "gefüllt, dünne dunkle Linie; Bewegungsflächen hellblau von der Wand bis zur "
                    "Mindesttiefe, Zusatztiefe bei gegenüberliegender Anordnung heller; Maße in cm."),
    # class per role; {category} becomes the element's category class
    "classes": {
        "object": {"name": "{category}", "pen": "#3c3c3c", "fill": "#ffffff", "line_weight": 0.13},
        "detail": {"name": "{category}-Detail", "pen": "#3c3c3c", "fill": "#ffffff", "line_weight": 0.1},
        "movement": {"name": "Bewegungsfläche", "pen": "#b3d0df", "fill": "#b3d0df", "line_weight": 0.05,
                     "line_style": "none"},
        "movement_extra": {"name": "Bewegungsfläche-Zusatz", "pen": "#e6eff4", "fill": "#e6eff4",
                           "line_weight": 0.05, "line_style": "none"},
        "movement_bf": {"name": "Bewegungsfläche-barrierefrei", "pen": "#4d90b5", "fill": "#b3d0df",
                        "line_weight": 0.1},
        "room": {"name": "Raumbegrenzung", "pen": "#000000", "fill": "none", "line_weight": 0.35},
        "door": {"name": "Öffnung-Tür", "pen": "#3c3c3c", "fill": "none", "line_weight": 0.13},
        "dimension": {"name": "Bemaßung"},
        "label": {"name": "Beschriftung"},
    },
    "category_classes": {
        "sanitaer": "Ausstattung-Sanitär", "kueche": "Ausstattung-Küche", "essen": "Ausstattung-Möbel",
        "wohnen": "Ausstattung-Möbel", "schlafen": "Ausstattung-Möbel", "kinder": "Ausstattung-Möbel",
        "arbeiten": "Ausstattung-Büro", "lernen": "Ausstattung-Schule", "wirtschaften": "Ausstattung-Sanitär",
        "aufbewahren": "Ausstattung-Möbel", "entsorgen": "Ausstattung-Entsorgung",
        "verkehr": "Staffage-Fahrzeuge", "freiraum": "Staffage-Vegetation", "person": "Staffage-Personen",
        "sonstiges": "Ausstattung",
    },
    "show": {"movement": True, "movement_extra": True, "dimensions": False, "labels": False,
             "room": True, "doors": True},
    # geometry family of Keystone's own symbols: "raumpilot" (outline with the
    # typical inner lines) or "outline" (Stellfläche rectangle only)
    "geometry": "raumpilot",
    "corner_radius_cm": 3.0,
    # element id -> the office's own symbol: {"symbol": name, "library": path
    # to a .vwx (optional), "dx": cm, "dy": cm, "rotation": deg} - offsets
    # from Keystone's insertion point (middle of the wall side, object
    # towards -y) to the symbol's own insertion point
    "symbols": {},
    "text_size_pt": 7.0,
}


class StyleError(ValueError):
    pass


def _root() -> Path:
    return Path(os.environ.get("VWMCP_HOME", Path.home() / ".vwmcp"))


def store_path() -> Path:
    return _root() / "styles.json"


def _load_store() -> dict:
    p = store_path()
    if not p.exists():
        return {"active": DEFAULT, "styles": {}}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"active": DEFAULT, "styles": {}}
    if not isinstance(d, dict):
        return {"active": DEFAULT, "styles": {}}
    d.setdefault("active", DEFAULT)
    d.setdefault("styles", {})
    return d


def _save_store(d: dict) -> None:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "symbols_replace":
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def names() -> list[str]:
    return [DEFAULT] + sorted(_load_store()["styles"])


def active_name() -> str:
    a = _load_store().get("active") or DEFAULT
    return a if a in names() else DEFAULT


def resolve(name: str | None = None, _seen: tuple = ()) -> dict:
    """The full style (defaults + overrides along the base chain)."""
    name = name or active_name()
    if name == DEFAULT:
        return copy.deepcopy(RAUMPILOT)
    store = _load_store()["styles"]
    if name not in store:
        raise StyleError("unknown style %r (styles: %s)" % (name, ", ".join(names())))
    if name in _seen:
        raise StyleError("style %r has a circular base" % name)
    over = store[name]
    base = resolve(over.get("base") or DEFAULT, _seen + (name,))
    st = _merge(base, {k: v for k, v in over.items() if k not in ("base", "name")})
    st["name"] = name
    st["base"] = over.get("base") or DEFAULT
    return st


def validate(over: dict) -> None:
    """Check a user style (overrides) before it is saved."""
    if not isinstance(over, dict):
        raise StyleError("a style is an object")
    known = set(RAUMPILOT) | {"base"}
    bad = set(over) - known
    if bad:
        raise StyleError("unknown style keys %s (known: %s)" % (sorted(bad), ", ".join(sorted(known))))
    for role, c in (over.get("classes") or {}).items():
        if role not in ROLES:
            raise StyleError("unknown class role %r (roles: %s)" % (role, ", ".join(ROLES)))
        if not isinstance(c, dict):
            raise StyleError("classes.%s must be an object" % role)
        for k in ("pen", "fill"):
            v = c.get(k)
            if v is not None and v != "none" and not (isinstance(v, str) and HEX.match(v)):
                raise StyleError("classes.%s.%s must be '#rrggbb' or 'none'" % (role, k))
        if "line_weight" in c and not (isinstance(c["line_weight"], (int, float)) and 0 <= c["line_weight"] <= 5):
            raise StyleError("classes.%s.line_weight must be mm between 0 and 5" % role)
        if "name" in c and not (isinstance(c["name"], str) and c["name"].strip()):
            raise StyleError("classes.%s.name must be a class name" % role)
    for cat, cls in (over.get("category_classes") or {}).items():
        if cat not in CATEGORIES or not isinstance(cls, str) or not cls.strip():
            raise StyleError("category_classes: %r -> %r (categories: %s)" % (cat, cls, ", ".join(CATEGORIES)))
    for k, v in (over.get("show") or {}).items():
        if k not in SHOW or not isinstance(v, bool):
            raise StyleError("show.%s must be true/false (keys: %s)" % (k, ", ".join(SHOW)))
    if "geometry" in over and over["geometry"] not in ("raumpilot", "outline"):
        raise StyleError("geometry must be 'raumpilot' or 'outline'")
    if "corner_radius_cm" in over and not (isinstance(over["corner_radius_cm"], (int, float))
                                           and 0 <= over["corner_radius_cm"] <= 20):
        raise StyleError("corner_radius_cm must be 0..20")
    for el, s in (over.get("symbols") or {}).items():
        if s is None:
            continue
        if not isinstance(s, dict) or not isinstance(s.get("symbol"), str) or not s["symbol"].strip():
            raise StyleError("symbols.%s needs {'symbol': '<name in the document or library>'}" % el)
        for k in ("dx", "dy", "rotation"):
            if k in s and not isinstance(s[k], (int, float)):
                raise StyleError("symbols.%s.%s must be a number" % (el, k))
        if "library" in s and not isinstance(s["library"], str):
            raise StyleError("symbols.%s.library must be a file path" % el)


def save(name: str, over: dict, activate: bool = False) -> dict:
    if not re.match(r"^[A-Za-z0-9ÄÖÜäöüß _.-]{1,40}$", name or "") or name == DEFAULT:
        raise StyleError("style name: 1-40 letters/digits/space/._- and not %r" % DEFAULT)
    validate(over)
    store = _load_store()
    prev = store["styles"].get(name, {})
    merged = _merge(prev, over)
    if "symbols" in over:  # None removes a mapping
        merged["symbols"] = {k: v for k, v in merged.get("symbols", {}).items() if v is not None}
    store["styles"][name] = merged
    resolve_chain = merged.get("base") or DEFAULT
    if resolve_chain != DEFAULT and resolve_chain not in store["styles"]:
        raise StyleError("base style %r doesn't exist" % resolve_chain)
    if activate:
        store["active"] = name
    _save_store(store)
    resolve(name)  # raises on a circular base
    return store["styles"][name]


def delete(name: str) -> None:
    store = _load_store()
    if name not in store["styles"]:
        raise StyleError("unknown style %r" % name)
    users = [n for n, s in store["styles"].items() if s.get("base") == name]
    if users:
        raise StyleError("style %r is the base of %s" % (name, ", ".join(users)))
    del store["styles"][name]
    if store.get("active") == name:
        store["active"] = DEFAULT
    _save_store(store)


def activate(name: str) -> None:
    if name not in names():
        raise StyleError("unknown style %r (styles: %s)" % (name, ", ".join(names())))
    store = _load_store()
    store["active"] = name
    _save_store(store)


def class_for(st: dict, role: str, category: str | None = None) -> tuple[str, dict]:
    """(class name, attributes to create it with) for a role."""
    c = st["classes"][role]
    cat = st["category_classes"].get(category or "sonstiges", "Ausstattung")
    name = c["name"].replace("{category}", cat)
    attrs = {k: v for k, v in c.items() if k != "name"}
    return name, attrs
