"""Keystone's own element symbols in plan, drawn like the Raumpilot books:
plain outlines, rounded front corners, the few inner lines that make an
object recognisable (basin, tap, bowl, drum, burners, pillow, chair seat).

Local frame (mm): insertion point in the middle of the back side (at the
wall), the object reaching towards -y; x in [-w/2, w/2], y in [-d, 0].
Every function takes the element's size in cm (w, d) plus kind parameters
and returns vw2d_draw items (mm) with role "outline" (the Stellfläche-shaped
body, white fill) or "detail" (inner lines).

geometry "outline" (a user style option) draws only the Stellfläche
rectangle with rounded front corners - for offices that put their own
symbols on top or want the plainest look.
"""

from __future__ import annotations

import math

from vwmcp.catalog.shapes import circle, front_rounded_rect, line, path, rect, rounded_rect


def _mm(v: float) -> float:
    return float(v) * 10.0


def body(w: float, d: float, r: float = 3.0, role: str = "outline"):
    """The Stellfläche as a body: back corners square, front corners round."""
    return front_rounded_rect(-_mm(w) / 2, 0.0, _mm(w) / 2, -_mm(d), _mm(min(r, w / 4, d / 4)), role=role)


# ---- sanitary ---------------------------------------------------------------------

def basin(w: float, d: float, bowls: int = 1, r: float = 3.0, **_):
    """Wash basin / vanity: body, basin(s) and tap(s)."""
    W, D = _mm(w), _mm(d)
    items = [body(w, d, r)]
    bowls = max(1, int(bowls))
    seg = W / bowls
    m = min(_mm(4.0), W * 0.08)
    for i in range(bowls):
        x1 = -W / 2 + i * seg + m
        x2 = -W / 2 + (i + 1) * seg - m
        y_top = -min(_mm(12.0), D * 0.3)
        y_bot = -D + m
        items.append(rounded_rect(x1, y_bot, x2, y_top, min(_mm(3.0), (x2 - x1) / 4), role="detail"))
        cx = (x1 + x2) / 2
        tw = min(_mm(2.0), seg * 0.06)
        items.append(rect(cx - tw / 2, -_mm(2.0), cx + tw / 2, y_top - _mm(5.0), role="detail"))
    return items


def wc(w: float, d: float, wall_hung: bool = True, **_):
    """WC (also bidet): narrow part at the wall, round bowl, bowl opening."""
    W, D = _mm(w), _mm(d)
    R = W / 2                      # front bowl radius
    cy = -D + R                    # bowl centre
    nb = W * 0.38                  # half width at the wall
    top = -min(_mm(4.0), D * 0.08)
    # outline: back edge, straight sides to the bowl, round front
    yt = cy + R * 0.25
    xt = R * math.cos(math.asin(0.25))
    outline = path((-nb, 0.0), [
        ("L", nb, 0.0), ("L", nb, top), ("L", xt, yt),
        ("A", -xt, yt, 0.0, cy, "cw"),
        ("L", -nb, top),
    ], role="outline")
    items = [outline, circle(0.0, cy, R * 0.55, role="detail")]
    if not wall_hung:
        items.append(line(-nb, top, nb, top, role="detail"))
    return items


def bidet(w: float, d: float, **_):
    items = wc(w, d)
    R = _mm(w) / 2
    cy = -_mm(d) + R
    items[1] = rounded_rect(-R * 0.45, cy - R * 0.6, R * 0.45, cy + R * 0.6, R * 0.4, role="detail")
    return items


def urinal(w: float, d: float, **_):
    """Urinal: shield shape narrowing to the front."""
    W, D = _mm(w), _mm(d)
    r = W * 0.18
    outline = path((-W / 2, 0.0), [
        ("L", W / 2, 0.0), ("L", W / 2, -D * 0.35),
        ("L", r * 0.7, -D + r * 0.5), ("R", -r * 0.7, -D + r * 0.5, r, "cw"),
        ("L", -W / 2, -D * 0.35),
    ], role="outline")
    inner = path((-W * 0.3, -D * 0.18), [
        ("L", W * 0.3, -D * 0.18), ("L", W * 0.3, -D * 0.4),
        ("L", r * 0.35, -D * 0.78), ("R", -r * 0.35, -D * 0.78, r * 0.5, "cw"),
        ("L", -W * 0.3, -D * 0.4),
    ], role="detail")
    return [outline, inner]


def shower(w: float, d: float, drain: str = "corner", floor: bool = False, **_):
    """Shower tray: square body, small inner margin, drain. floor=True: a
    floor-level shower place (only its outline and a central drain)."""
    W, D = _mm(w), _mm(d)
    if floor:
        return [rect(-W / 2, 0.0, W / 2, -D, role="detail"), circle(0.0, -D / 2, _mm(3.5), role="detail")]
    items = [rect(-W / 2, 0.0, W / 2, -D, role="outline"),
             rounded_rect(-W / 2 + _mm(2), -D + _mm(2), W / 2 - _mm(2), -_mm(2), _mm(4), role="detail")]
    if drain == "center":
        items.append(circle(0.0, -D / 2, _mm(3.5), role="detail"))
    elif drain == "channel":
        items.append(rect(-W / 2 + _mm(8), -_mm(5), W / 2 - _mm(8), -_mm(8), role="detail"))
    else:
        items.append(circle(-W / 2 + _mm(8), -_mm(8), _mm(2.5), role="detail"))
    return items


def bath(w: float, d: float, **_):
    """Bathtub: rectangle, rounded inner tub, tap and overflow at one end."""
    W, D = _mm(w), _mm(d)
    m = min(_mm(6), D * 0.1)
    items = [rect(-W / 2, 0.0, W / 2, -D, role="outline"),
             rounded_rect(-W / 2 + m + _mm(6), -D + m, W / 2 - m, -m, min(_mm(18), D * 0.3), role="detail"),
             rect(-W / 2 + _mm(1.5), -D / 2 - _mm(1.5), -W / 2 + _mm(11), -D / 2 + _mm(1.5), role="detail"),
             circle(-W / 2 + _mm(15), -D / 2, _mm(2), role="detail")]
    return items


def drum(w: float, d: float, **_):
    """Washing machine / dryer: box with drum."""
    W, D = _mm(w), _mm(d)
    return [rect(-W / 2, 0.0, W / 2, -D, role="outline"),
            circle(0.0, -D / 2 - _mm(2), min(W, D) * 0.32, role="detail"),
            line(-W / 2, -D + _mm(8), W / 2, -D + _mm(8), role="detail")]


# ---- kitchen ----------------------------------------------------------------------

def counter(w: float, d: float, **_):
    """Base cabinet / worktop: rectangle with the worktop front edge line."""
    W, D = _mm(w), _mm(d)
    return [rect(-W / 2, 0.0, W / 2, -D, role="outline"),
            line(-W / 2, -D + _mm(3), W / 2, -D + _mm(3), role="detail")]


def sink(w: float, d: float, bowls: int = 1, drainer: bool = True, **_):
    W, D = _mm(w), _mm(d)
    items = counter(w, d)
    bw = min(_mm(40), (W - _mm(10)) / (bowls + (0.8 if drainer else 0.0)))
    x = -W / 2 + _mm(5)
    for _i in range(max(1, int(bowls))):
        items.append(rounded_rect(x, -D + _mm(10), x + bw, -_mm(8), _mm(3), role="detail"))
        x += bw + _mm(3)
    if drainer and x < W / 2 - _mm(15):
        x2 = W / 2 - _mm(5)
        for k in range(4):
            yy = -_mm(14) - k * (D - _mm(28)) / 3
            items.append(line(x + _mm(3), yy, x2, yy, role="detail"))
    return items


def hob(w: float, d: float, burners: int = 4, **_):
    W, D = _mm(w), _mm(d)
    items = counter(w, d)
    n = 2 if burners <= 2 else 4
    cols = 1 if n == 2 else 2
    rows = 2
    for i in range(cols):
        for j in range(rows):
            cx = -W / 2 + W * (i + 0.5) / cols if cols > 1 else 0.0
            cy = -D * (j + 0.5) / rows
            r = min(W / cols, D / rows) * (0.32 if (i + j) % 2 == 0 else 0.25)
            items.append(circle(cx, cy, r, role="detail"))
    return items


def fridge(w: float, d: float, **_):
    W, D = _mm(w), _mm(d)
    return [rect(-W / 2, 0.0, W / 2, -D, role="outline"),
            line(-W / 2, -_mm(5), W / 2, -D, role="detail"), line(W / 2, -_mm(5), -W / 2, -D, role="detail")]


def tall(w: float, d: float, **_):
    """Tall cabinet / wardrobe: rectangle with a diagonal cross (book convention
    for full-height furniture)."""
    W, D = _mm(w), _mm(d)
    return [rect(-W / 2, 0.0, W / 2, -D, role="outline"),
            line(-W / 2, 0.0, W / 2, -D, role="detail"), line(W / 2, 0.0, -W / 2, -D, role="detail")]


def wardrobe(w: float, d: float, **_):
    """Clothes wardrobe: rectangle with the hanging rail and hangers."""
    W, D = _mm(w), _mm(d)
    items = [rect(-W / 2, 0.0, W / 2, -D, role="outline"),
             line(-W / 2 + _mm(3), -D / 2, W / 2 - _mm(3), -D / 2, role="detail")]
    n = max(2, int(W / _mm(15)))
    for k in range(1, n):
        x = -W / 2 + W * k / n
        items.append(line(x - _mm(2), -D / 2 + _mm(18), x + _mm(2), -D / 2 - _mm(18), role="detail"))
    return items


def box(w: float, d: float, **_):
    W, D = _mm(w), _mm(d)
    return [rect(-W / 2, 0.0, W / 2, -D, role="outline")]


def shelf(w: float, d: float, **_):
    W, D = _mm(w), _mm(d)
    return [rect(-W / 2, 0.0, W / 2, -D, role="outline"),
            line(-W / 2, -D / 2, W / 2, -D / 2, role="detail")]


# ---- furniture --------------------------------------------------------------------

def chair(w: float, d: float, **_):
    """Chair seen from above: seat with the back rest at the back (y = 0 side)."""
    W, D = _mm(w), _mm(d)
    return [rounded_rect(-W / 2, -D, W / 2, 0.0, _mm(3), role="outline"),
            line(-W / 2 + _mm(3), -_mm(6), W / 2 - _mm(3), -_mm(6), role="detail")]


def table(w: float, d: float, round_: bool = False, **_):
    W, D = _mm(w), _mm(d)
    if round_:
        return [circle(0.0, -D / 2, min(W, D) / 2, role="outline")]
    return [rect(-W / 2, 0.0, W / 2, -D, role="outline")]


def bed(w: float, d: float, persons: int = 1, **_):
    """Bed (head at the wall): frame, pillow(s), blanket fold."""
    W, D = _mm(w), _mm(d)
    items = [rect(-W / 2, 0.0, W / 2, -D, role="outline")]
    n = 2 if persons >= 2 or w >= 140 else 1
    seg = W / n
    for i in range(n):
        x1 = -W / 2 + i * seg + _mm(6)
        items.append(rounded_rect(x1, -_mm(40), x1 + seg - _mm(12), -_mm(8), _mm(5), role="detail"))
    items.append(line(-W / 2, -_mm(55), W / 2, -_mm(55), role="detail"))
    items.append(line(-W / 2, -_mm(55), -W / 2 + min(W, _mm(40)) * 0.6, -_mm(75), role="detail"))
    return items


def sofa(w: float, d: float, **_):
    W, D = _mm(w), _mm(d)
    arm = min(_mm(20), W * 0.15)
    back = min(_mm(20), D * 0.25)
    return [rounded_rect(-W / 2, -D, W / 2, 0.0, _mm(4), role="outline"),
            line(-W / 2 + arm, -back, W / 2 - arm, -back, role="detail"),
            line(-W / 2 + arm, -back, -W / 2 + arm, -D, role="detail"),
            line(W / 2 - arm, -back, W / 2 - arm, -D, role="detail")]


def armchair(w: float, d: float, **_):
    return sofa(w, d)


def desk(w: float, d: float, **_):
    return [rect(-_mm(w) / 2, 0.0, _mm(w) / 2, -_mm(d), role="outline")]


def piano(w: float, d: float, grand: bool = False, **_):
    W, D = _mm(w), _mm(d)
    if not grand:
        return [rect(-W / 2, 0.0, W / 2, -D, role="outline"),
                line(-W / 2, -D + _mm(10), W / 2, -D + _mm(10), role="detail")]
    # grand piano: straight keyboard side, curved tail
    return [path((-W / 2, -D), [("L", W / 2, -D), ("L", W / 2, -D * 0.45),
                                ("R", W * 0.05, 0.0, W * 0.6, "ccw"), ("L", -W / 2, 0.0)], role="outline"),
            line(-W / 2, -D + _mm(15), W / 2, -D + _mm(15), role="detail")]


def wheelchair(w: float, d: float, **_):
    """Wheelchair turning circle (diameter = w)."""
    return [circle(0.0, -_mm(d) / 2, _mm(w) / 2, role="outline")]


def car(w: float, d: float, **_):
    """Vehicle from above (w = width, d = length), front towards -y."""
    from vwmcp.catalog.assets_people_plants import car_plan
    from vwmcp.catalog.shapes import translate

    items = car_plan(l=_mm(d), b=_mm(w))  # front towards +x, centred
    out = []
    for it in items:
        out.append(_turn(it))
    return translate(out, 0.0, -_mm(d) / 2)


def _turn(it):
    """Item turned by -90° (x, y) -> (y, -x): +x front becomes -y."""
    def t(p):
        return [round(p[1], 6) + 0.0, round(-p[0], 6) + 0.0]

    n = dict(it)
    for k in ("p1", "p2", "center", "start"):
        if k in n:
            n[k] = t(n[k])
    if n.get("type") == "rect":
        (x1, y1), (x2, y2) = n["p1"], n["p2"]
        n["p1"], n["p2"] = [min(x1, x2), max(y1, y2)], [max(x1, x2), min(y1, y2)]
    if "points" in n:
        n["points"] = [t(p) for p in n["points"]]
    if "segments" in n:
        segs = []
        for sg in n["segments"]:
            sg = dict(sg)
            for k in ("line_to", "arc_to", "center"):
                if k in sg:
                    sg[k] = t(sg[k])
            segs.append(sg)
        n["segments"] = segs
    if n.get("type") == "arc":
        n["start_angle"] = round((n["start_angle"] - 90.0) % 360.0, 9)
    return n


def parking(w: float, d: float, **_):
    """Parking space: the stall and a car in it."""
    W, D = _mm(w), _mm(d)
    items = [rect(-W / 2, 0.0, W / 2, -D, role="detail")]
    cw, cl = min(w - 40, 185.0), min(d - 30, 470.0)
    if cw > 50 and cl > 100:
        from vwmcp.catalog.shapes import translate
        for it in car(cw, cl):
            it = dict(it)
            items += translate([it], 0.0, -(D - _mm(cl)) / 2)
    return items


def bicycle(w: float, d: float, **_):
    W, D = _mm(w), _mm(d)
    tyre = min(D * 0.38, _mm(66))
    return [rect(-_mm(2.5), -_mm(1), _mm(2.5), -_mm(1) - tyre, role="outline"),
            rect(-_mm(2.5), -D + tyre, _mm(2.5), -D, role="outline"),
            line(0.0, -tyre / 2, 0.0, -D + tyre / 2, role="outline"),
            line(-W / 2, -tyre * 0.8, W / 2, -tyre * 0.8, role="outline")]


def person(w: float, d: float, **_):
    """Person (or person with aid) from above: shoulders oval and head."""
    from vwmcp.catalog.shapes import korb_oval

    W, D = _mm(w), _mm(d)
    sw, sd = min(W, _mm(60)), min(D, _mm(30))
    return [rect(-W / 2, 0.0, W / 2, -D, role="detail"),
            korb_oval(0.0, -D / 2, sw, sd, role="outline"), circle(0.0, -D / 2, min(sw, sd) * 0.36, role="outline")]


def generic(w: float, d: float, **_):
    return [body(w, d)]


KINDS = {
    "basin": basin, "wc": wc, "bidet": bidet, "urinal": urinal, "shower": shower, "bath": bath,
    "drum": drum, "counter": counter, "sink": sink, "hob": hob, "fridge": fridge, "tall": tall,
    "wardrobe": wardrobe, "box": box, "shelf": shelf, "chair": chair, "table": table, "bed": bed,
    "sofa": sofa, "armchair": armchair, "desk": desk, "piano": piano, "wheelchair": wheelchair,
    "car": car, "parking": parking, "bicycle": bicycle, "person": person, "generic": generic,
}


def draw(kind: str, w: float, d: float, geometry: str = "raumpilot", **params) -> list[dict]:
    """Items (mm, local frame) for one element."""
    if geometry == "outline":
        return [body(w, d)]
    fn = KINDS.get(kind, generic)
    if "round" in params:
        params["round_"] = params.pop("round")
    return fn(w, d, **params)
