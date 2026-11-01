"""The planning knowledge base: elements, distances, rooms, standard
layouts and rules, distilled from the Raumpilot books (Wüstenrot Stiftung,
2012: Grundlagen, Wohnen, Arbeiten, Lernen) and the norms they cite. Facts
only, in Keystone's own words, each with its source page so it can be
checked in the book.

Data files (planning/data/*.json), all lengths cm, areas m²:
  elements.json   [{id, name, category, kind, w, d, short, wall, areas,
                    areas_bf, areas_rg, variants, params, heights, notes,
                    sources, keywords}]
  clearances.json {"sanitary_sides": {a: {b: cm}}, "door_reveal": cm, ...}
  rooms.json      [{id, name, context, min, typical, requires, rules,
                    sources}]
  layouts.json    [{id, room, name, W, D, barrier_free, elements: [{element,
                    x, y, w, d, against}], doors, sources}]
  rules.json      [{id, topic, title, text, values, condition, norm, sources}]
"""

from __future__ import annotations

import functools
import json
import re
import unicodedata
from importlib import resources

from vwmcp.planning import engine as E

BOOKS = {"G": "Raumpilot Grundlagen", "W": "Raumpilot Wohnen", "A": "Raumpilot Arbeiten",
         "L": "Raumpilot Lernen"}
MODES = ("standard", "barrierefrei", "rollstuhlgerecht")
ACCESSORIES = {"stuetzklappgriff", "handlauf"}


def _load(name: str):
    try:
        text = resources.files("vwmcp.planning").joinpath("data", name).read_text(encoding="utf-8")
    except FileNotFoundError:
        return [] if name != "clearances.json" else {}
    return json.loads(text)


@functools.cache
def elements() -> dict[str, dict]:
    return {e["id"]: e for e in _load("elements.json")}


@functools.cache
def clearances() -> dict:
    return _load("clearances.json")


@functools.cache
def rooms() -> dict[str, dict]:
    return {r["id"]: r for r in _load("rooms.json")}


@functools.cache
def layouts() -> dict[str, dict]:
    return {lay["id"]: lay for lay in _load("layouts.json")}


@functools.cache
def rules() -> list[dict]:
    return _load("rules.json")


def source_text(sources) -> str:
    """'Raumpilot Grundlagen S. 269, VDI 6000 Blatt 1' from [{"book": "G",
    "page": 269}, {"norm": "VDI 6000 Blatt 1"}]."""
    out = []
    for s in sources or []:
        if isinstance(s, str):
            out.append(s)
        elif "norm" in s:
            out.append(s["norm"])
        else:
            book = BOOKS.get(s.get("book"), s.get("book", ""))
            pg = s.get("page")
            out.append("%s S. %s" % (book, pg) if pg is not None else book)
    return ", ".join(out)


# ---- search ----------------------------------------------------------------------

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = s.replace("ß", "ss")
    return "".join(c for c in s if not unicodedata.combining(c))


SYNONYMS = {
    "toilette": "wc", "klo": "wc", "klosett": "wc", "waschtisch": "waschbecken", "lavabo": "waschbecken",
    "badewanne": "wanne", "duschwanne": "dusche", "bad": "bad", "badezimmer": "bad", "kueche": "kueche",
    "herd": "kochfeld", "spuelbecken": "spuele", "schlafzimmer": "schlafen", "buero": "buero",
    "arbeitsplatz": "arbeitsplatz", "klassenzimmer": "klassenzimmer", "klassenraum": "klassenzimmer",
    "rollstuhl": "rollstuhl", "barrierefrei": "barrierefrei", "treppe": "treppe", "stufe": "treppe",
    "aufzug": "aufzug", "lift": "aufzug", "stellplatz": "stellplatz", "parkplatz": "stellplatz",
    "tuer": "tuer", "flur": "flur", "gang": "flur",
}


def _hay(*parts) -> str:
    return _norm(" ".join(str(p) for p in parts if p))


def search(query: str, limit: int = 40) -> dict:
    """Elements, rooms, layouts and rules matching all words of the query
    (German, umlauts optional, a few synonyms)."""
    words = [SYNONYMS.get(w, w) for w in re.findall(r"\w+", _norm(query or ""))]
    out = {"elements": [], "rooms": [], "layouts": [], "rules": []}
    if not words:
        return out

    def one(w: str, hay: str) -> bool:
        # a little stemming: "dusche" finds "Duschwanne", "toiletten" "Toilette"
        if w in hay:
            return True
        if len(w) >= 5 and w[:-1] in hay:
            return True
        return len(w) >= 7 and w[:-2] in hay

    def hit(hay: str) -> bool:
        return all(one(w, hay) for w in words)

    for e in elements().values():
        if hit(_hay(e["id"], e["name"], e.get("category"), e.get("short"), " ".join(e.get("keywords", [])),
                    " ".join(e.get("context", [])))):
            out["elements"].append(e["id"])
    for r in rooms().values():
        if hit(_hay(r["id"], r["name"], " ".join(r.get("context", [])), " ".join(r.get("keywords", [])))):
            out["rooms"].append(r["id"])
    for lay in layouts().values():
        if hit(_hay(lay["id"], lay["name"], lay.get("room"))):
            out["layouts"].append(lay["id"])
    for r in rules():
        if hit(_hay(r.get("id"), r.get("topic"), r.get("title"), r.get("text"), r.get("condition"),
                    r.get("norm"), " ".join(r.get("keywords", [])))):
            out["rules"].append(r)
    for k in ("elements", "rooms", "layouts"):
        out[k] = out[k][:limit]
    out["rules"] = out["rules"][:limit]
    return out


def topics() -> dict[str, int]:
    t: dict[str, int] = {}
    for r in rules():
        t[r.get("topic", "?")] = t.get(r.get("topic", "?"), 0) + 1
    return dict(sorted(t.items()))


# ---- element -> engine spec ------------------------------------------------------

def element(eid: str) -> dict:
    e = elements().get(eid)
    if e is None:
        raise KeyError("unknown element %r (vw2d_knowledge lists them)" % eid)
    return e


def size_of(e: dict, variant: str | None = None, w: float | None = None, d: float | None = None) -> tuple[float, float, dict]:
    """(w, d, params) of an element, a named variant, or explicit sizes."""
    params = dict(e.get("params") or {})
    W, D = float(e["w"]), float(e["d"])
    if variant:
        v = next((v for v in e.get("variants", []) if v.get("id") == variant or v.get("label") == variant), None)
        if v is None:
            raise KeyError("element %r has no variant %r (variants: %s)" % (
                e["id"], variant, ", ".join(v.get("id", v.get("label", "?")) for v in e.get("variants", [])) or "none"))
        W, D = float(v.get("w", W)), float(v.get("d", D))
        params.update(v.get("params") or {})
    if w is not None:
        W = float(w)
    if d is not None:
        D = float(d)
    return W, D, params


def areas_of(e: dict, mode: str = "standard") -> list[E.Area]:
    key = {"standard": "areas", "barrierefrei": "areas_bf", "rollstuhlgerecht": "areas_rg"}[mode]
    raw = e.get(key)
    if raw is None and mode == "rollstuhlgerecht":
        raw = e.get("areas_bf")
    if raw is None:
        raw = e.get("areas") or []
    return [E.Area(side=a.get("side", "front"), w=a.get("w"), d=float(a.get("d", 0.0)), d_extra=a.get("d_extra"),
                   align=a.get("align", "center"), offset=float(a.get("offset", 0.0)), note=a.get("note", ""))
            for a in raw]


def spec(eid: str, mode: str = "standard", variant: str | None = None, w: float | None = None,
         d: float | None = None, tag: str = "") -> E.Spec:
    e = element(eid)
    W, D, _ = size_of(e, variant, w, d)
    return E.Spec(id=eid, w=W, d=D, short=e.get("short"), wall=bool(e.get("wall", True)),
                  areas=areas_of(e, mode), overlap_ok=tuple(e.get("overlap_ok", ())),
                  corner_ok=bool(e.get("corner_ok")), name=tag or e["name"],
                  walkable=bool(e.get("walkable")), group=e.get("group"))


def sides(mode: str = "standard") -> dict:
    c = clearances()
    if mode != "standard" and c.get("sanitary_sides_bf"):
        return c["sanitary_sides_bf"]
    return c.get("sanitary_sides", {})


def door_reveal() -> float:
    return float(clearances().get("door_reveal", 10.0))


# ---- standard layouts ------------------------------------------------------------

def _wall_of(el: dict, W: float, D: float) -> str | None:
    a = el.get("against")
    if a in ("N", "E", "S", "W"):
        return a
    return None


def placed_from_layout(lay: dict, mode: str = "standard") -> list[E.Placed]:
    """The layout's elements as engine placements in its own W x D room."""
    W, D = float(lay["W"]), float(lay["D"])
    out = []
    for i, el in enumerate(lay["elements"]):
        if el["element"] in ACCESSORIES:
            continue  # grab rails etc. don't take floor space
        x, y, w, d = (float(el[k]) for k in ("x", "y", "w", "d"))
        wall = _wall_of(el, W, D)
        rot = E.WALL_ROT.get(wall or "N", 0.0) if wall else float(el.get("rotation", 0.0))
        # local size: w along the wall
        lw, ld = (w, d) if wall in (None, "N", "S") else (d, w)
        if wall is None and el.get("faces") in ("E", "W"):
            lw, ld = d, w
            rot = 90.0 if el["faces"] == "E" else 270.0
        try:
            sp = spec(el["element"], mode, w=lw, d=ld)
        except KeyError:
            sp = E.Spec(el["element"], lw, ld, wall=wall is not None)
        cx, cy = x + w / 2, y + d / 2
        # insertion point = middle of the back side
        bx, by = E.rot(0.0, ld / 2, rot)
        out.append(E.Placed(sp, cx + bx, cy + by, rot, wall, tag="%s#%d" % (el["element"], i + 1)))
    return out


def fit(lay: dict, W: float, D: float, turn: int = 0, mirror: bool = False, mode: str = "standard") -> E.Layout | None:
    """A standard layout turned/mirrored and adapted to a W x D room (at
    least as big): elements keep their walls and their distance to the
    nearer corner; the extra space goes to the middle."""
    W0, D0 = E.turned_size(float(lay["W"]), float(lay["D"]), turn)
    if W0 > W + E.EPS or D0 > D + E.EPS:
        return None
    dW, dD = W - W0, D - D0
    placed = []
    for p in placed_from_layout(lay, mode):
        r = p.rect
        # transform the rect corners
        pts = [E.transform_point(x, y, float(lay["W"]), float(lay["D"]), turn, mirror)
               for x, y in ((r.x1, r.y1), (r.x2, r.y2))]
        x1, x2 = sorted((pts[0][0], pts[1][0]))
        y1, y2 = sorted((pts[0][1], pts[1][1]))
        wall = E.turn_wall(p.wall, turn, mirror)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        sx = dW if (wall == "E" or (wall != "W" and cx > W0 / 2)) else 0.0
        sy = dD if (wall == "N" or (wall != "S" and cy > D0 / 2)) else 0.0
        x1, x2, y1, y2 = x1 + sx, x2 + sx, y1 + sy, y2 + sy
        rot = E.WALL_ROT[wall] if wall else (p.deg + turn * 90.0) % 360.0
        if mirror and not wall:
            rot = (-p.deg + turn * 90.0) % 360.0
        lw, ld = (x2 - x1, y2 - y1) if rot % 180 == 0 else (y2 - y1, x2 - x1)
        sp = E.Spec(p.spec.id, lw, ld, p.spec.short, p.spec.wall, p.spec.areas, p.spec.overlap_ok,
                    p.spec.corner_ok, p.spec.name, p.spec.walkable, p.spec.group)
        bx, by = E.rot(0.0, ld / 2, rot)
        placed.append(E.Placed(sp, (x1 + x2) / 2 + bx, (y1 + y2) / 2 + by, rot, wall, p.tag))
    doors = []
    for dr in lay.get("doors") or []:
        doors.append(_turn_door(dr, float(lay["W"]), float(lay["D"]), turn, mirror, dW, dD))
    out = E.Layout(W, D, placed, [d for d in doors if d is not None], source=source_text(lay.get("sources")),
                   name=lay["name"])
    return out


def _turn_door(dr: dict, W0: float, D0: float, turn: int, mirror: bool, dW: float, dD: float) -> E.Door | None:
    try:
        if dr.get("wall") not in ("N", "E", "S", "W"):
            return None
        d = E.Door(dr["wall"], float(dr["offset"]), float(dr.get("width") or 80), dr.get("swing"),
                   dr.get("hinge") or "left")
    except (KeyError, TypeError, ValueError):
        return None
    span = d.span(W0, D0)
    pts = [E.transform_point(x, y, W0, D0, turn, mirror) for x, y in ((span.x1, span.y1), (span.x2, span.y2))]
    wall = E.turn_wall(d.wall, turn, mirror)
    Wt, Dt = E.turned_size(W0, D0, turn)
    xs = sorted(p[0] for p in pts)
    ys = sorted(p[1] for p in pts)
    if wall in ("N", "S"):
        a, b = xs
        shift = dW if (a + b) / 2 > Wt / 2 else 0.0
        a, b = a + shift, b + shift
        offset = a if wall == "N" else (Wt + dW) - b
    else:
        a, b = ys
        shift = dD if (a + b) / 2 > Dt / 2 else 0.0
        a, b = a + shift, b + shift
        offset = a if wall == "W" else (Dt + dD) - b
    hinge = d.hinge if not mirror else {"left": "right", "right": "left"}.get(d.hinge, d.hinge)
    return E.Door(wall, offset, d.width, d.swing, hinge)
