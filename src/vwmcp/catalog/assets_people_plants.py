"""Scale figures, trees, vehicles (mm). Centred on 0, 0 in plan; elevations
stand on y = 0."""

from __future__ import annotations

import math

from vwmcp.catalog.shapes import circle, korb_oval, line, path, poly, rect, rounded_rect


def _cloud(cx: float, cy: float, rx: float, ry: float, lobes: int, seed: float = 1.0,
           wobble: float = 0.07, role: str = "outline") -> dict:
    """Closed scalloped outline (tree crown): `lobes` outward arcs between
    points on an ellipse whose radius varies a little (deterministic)."""
    pts = []
    for i in range(lobes):
        th = 2 * math.pi * i / lobes + 0.37 * seed
        k = 1 + wobble * math.sin(i * 2.399 + seed) * math.cos(i * 1.3 + 0.5 * seed)
        pts.append((cx + rx * k * math.cos(th), cy + ry * k * math.sin(th)))
    pts = [(round(x, 6), round(y, 6)) for x, y in pts]
    segs = []
    for i in range(lobes):
        (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % lobes]
        chord = math.hypot(x2 - x1, y2 - y1)
        # radius > chord/2: a lobe flatter than a semicircle, bulging outwards
        # (ccw small arc = centre on the inner side of the chord)
        segs.append(("R", x2, y2, chord * 0.62, "ccw"))
    return path(pts[0], segs, role=role)


def _star(cx: float, cy: float, r_out: float, r_in: float, n: int, rot: float = 0.0, role: str = "outline"):
    pts = []
    for i in range(2 * n):
        a = math.pi * i / n + rot
        r = r_out if i % 2 == 0 else r_in
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return poly(pts, role=role)


def tree_plan(d=6000.0, trunk=300.0, style=0.0, seed=1.0):
    """Tree from above: crown diameter d. style 0 = organic outline (deciduous),
    1 = circle, 2 = conifer (star)."""
    r = d / 2
    items = []
    if style >= 2:
        n = max(14, min(36, int(round(d / 180))))
        items.append(_star(0, 0, r, r * 0.86, n, 0.1 * seed))
        items.append(_star(0, 0, r * 0.5, r * 0.42, max(8, n // 2), 0.3 + 0.1 * seed, role="detail"))
    elif style >= 1:
        items.append(circle(0, 0, r, role="outline"))
        items.append(circle(0, 0, r * 0.82, role="detail"))
    else:
        lobes = max(9, min(24, int(round(d / 450))))
        items.append(_cloud(0, 0, r, r, lobes, seed))
    items.append(circle(0, 0, trunk / 2, role="detail"))
    items.append(line(-trunk * 0.9, 0, trunk * 0.9, 0))
    items.append(line(0, -trunk * 0.9, 0, trunk * 0.9))
    return items


def _conifer_front(d: float, trunk: float, h: float):
    """Conifer in elevation: tiered silhouette from the trunk base to the tip."""
    base = h * 0.12
    tiers = max(4, min(9, int(round((h - base) / 1500))))
    tier_h = (h - base) / tiers
    right = []
    for i in range(tiers):
        y_low = base + i * tier_h
        w = d / 2 * (1 - i / tiers)
        right.append((w, y_low))                               # tip of the branch layer
        right.append((w * 0.55, y_low + tier_h * 0.45))        # notch above it
    t0 = trunk / 2
    pts = [(t0, 0), (t0, base)] + [(x, y) for x, y in right] + [(0, h)]
    pts += [(-x, y) for x, y in reversed(right)] + [(-t0, base), (-t0, 0)]
    # remove a notch point that would sit on the trunk side
    return [poly(pts)]


def tree_front(d=6000.0, trunk=300.0, style=0.0, seed=1.0, h=10000.0):
    """Tree in elevation: height h, crown width d."""
    if style >= 2:
        return _conifer_front(d, trunk, h)
    crown_h = min(h * 0.68, d * 1.25)
    crown_cy = h - crown_h / 2
    base = h - crown_h
    t0, t1 = trunk / 2, trunk * 0.32
    items = [poly([(-t0, 0), (t0, 0), (t1, base + crown_h * 0.25), (-t1, base + crown_h * 0.25)])]
    if style >= 1:
        items.append(korb_oval(0, crown_cy, d, crown_h))
    else:
        lobes = max(10, min(26, int(round((d + crown_h) / 700))))
        items.append(_cloud(0, crown_cy, d / 2, crown_h / 2, lobes, seed))
    return items


def shrub_plan(d=1500.0, seed=2.0):
    return [_cloud(0, 0, d / 2, d / 2, max(7, int(round(d / 250))), seed, wobble=0.1)]


def person_plan(w=600.0):
    k = w / 600.0
    return [korb_oval(0, 0, 600 * k, 280 * k), circle(0, -10 * k, 105 * k, role="outline")]


def person_front(h=1750.0):
    k = h / 1750.0
    right = [(45, 1545), (175, 1482), (232, 1420), (252, 1110), (258, 860), (247, 790), (218, 768),
             (196, 800), (190, 1100), (184, 1290), (172, 1270), (158, 1050), (178, 900), (166, 460),
             (132, 90), (150, 30), (148, 0), (40, 0), (36, 90), (14, 840), (0, 852)]
    left = [(-x, y) for x, y in reversed(right[:-1])]
    pts = [(x * k, y * k) for x, y in right + left]
    return [poly(pts, role="outline"), circle(0, 1640 * k, 105 * k, role="outline")]


def car_plan(l=4700.0, b=1850.0):
    """Car from above, front towards +x."""
    return [
        rounded_rect(-l / 2, -b / 2, l / 2, b / 2, 300),
        poly([(l * 0.17, -b / 2 + 150), (l * 0.30, -b / 2 + 230), (l * 0.30, b / 2 - 230),
              (l * 0.17, b / 2 - 150)], role="detail"),                     # windscreen
        poly([(-l * 0.30, -b / 2 + 170), (-l * 0.37, -b / 2 + 230), (-l * 0.37, b / 2 - 230),
              (-l * 0.30, b / 2 - 170)], role="detail"),                    # rear window
        rect(-l * 0.30, -b / 2 + 150, l * 0.17, b / 2 - 150, role="detail"),  # roof
    ]


def car_side(l=4700.0, b=1850.0, h=1480.0):
    """Car in side elevation, front towards +x, centred on its length, on y = 0."""
    k = l / 4700.0
    kh = h / 1480.0

    def X(x):
        return x * k

    def Y(y):
        return y * kh

    wx, wr, ar = 1400.0, 330.0, 380.0  # wheel centre x (+/-), tyre radius, arch radius
    body = path((X(2200), Y(300)), [
        ("L", X(wx) + ar * k, Y(300)), ("A", X(wx) - ar * k, Y(300), X(wx), Y(300), "ccw"),
        ("L", -X(wx) + ar * k, Y(300)), ("A", -X(wx) - ar * k, Y(300), -X(wx), Y(300), "ccw"),
        ("L", X(-2200), Y(300)), ("L", X(-2350), Y(500)), ("L", X(-2320), Y(950)), ("L", X(-1550), Y(1000)),
        ("L", X(-1000), Y(1450)), ("L", X(500), Y(1480)), ("L", X(1200), Y(1020)), ("L", X(2270), Y(870)),
        ("L", X(2350), Y(600)), ("L", X(2320), Y(380)),
    ])
    items = [body,
             poly([(X(-1100), Y(1010)), (X(-930), Y(1400)), (X(470), Y(1425)), (X(1120), Y(1030))], role="detail"),
             line(X(0), Y(1015), X(0), Y(1420)),
             line(X(-2300), Y(700), X(2330), Y(700)),                     # belt line
             ]
    for x in (wx, -wx):
        items.append(circle(X(x), wr * kh, wr * min(k, kh), role="outline"))
        items.append(circle(X(x), wr * kh, wr * min(k, kh) * 0.6, role="detail"))
    return items


def bicycle_plan(l=1750.0, b=600.0):
    """Bicycle from above, front towards +x."""
    tyre = 660.0
    return [
        rect(-l / 2, -25, -l / 2 + tyre, 25), rect(l / 2 - tyre, -25, l / 2, 25),        # wheels
        line(-l / 2 + tyre / 2, 0, l / 2 - tyre / 2, 0, role="outline"),                  # frame
        line(l / 2 - tyre / 2 - 40, -b / 2, l / 2 - tyre / 2 - 40, b / 2, role="outline"),  # handlebar
        rounded_rect(-l / 2 + tyre / 2 + 120, -80, -l / 2 + tyre / 2 + 380, 80, 60, role="detail"),  # saddle
    ]


ASSETS = [
    dict(id="baum", name="Baum", category="plants",
         views={"plan": tree_plan, "front": tree_front},
         params={"d": (6000.0, "Kronendurchmesser"), "trunk": (300.0, "Stammdurchmesser"),
                 "style": (0.0, "0 = Laubbaum (organische Krone), 1 = Kreis, 2 = Nadelbaum"),
                 "seed": (1.0, "Variante der Kronenform"),
                 "h": (10000.0, "Höhe (nur Ansicht)")},
         insertion="Stammmitte (Ansicht: am Boden)",
         keywords=("baum", "bäume", "pflanze", "vegetation", "grün", "staffage", "nadelbaum", "laubbaum", "tanne")),
    dict(id="strauch", name="Strauch", category="plants",
         views={"plan": shrub_plan},
         params={"d": (1500.0, "Durchmesser"), "seed": (2.0, "Variante")},
         insertion="Mitte", keywords=("strauch", "busch", "hecke", "pflanze", "vegetation")),
    dict(id="person", name="Person", category="people",
         views={"plan": person_plan, "front": person_front},
         params={"w": (600.0, "Schulterbreite (Grundriss)"), "h": (1750.0, "Körpergröße (Ansicht)")},
         insertion="Grundriss: Mitte; Ansicht: zwischen den Füßen am Boden",
         keywords=("person", "mensch", "figur", "maßstabsfigur", "staffage")),
    dict(id="pkw", name="PKW", category="vehicles",
         views={"plan": car_plan, "side": car_side},
         params={"l": (4700.0, "Länge"), "b": (1850.0, "Breite"), "h": (1480.0, "Höhe (nur Ansicht)")},
         insertion="Fahrzeugmitte, Front nach +x (Ansicht: am Boden)",
         keywords=("auto", "pkw", "fahrzeug", "parkplatz", "stellplatz", "garage")),
    dict(id="fahrrad", name="Fahrrad", category="vehicles",
         views={"plan": bicycle_plan},
         params={"l": (1750.0, "Länge"), "b": (600.0, "Lenkerbreite")},
         insertion="Mitte, Front nach +x", keywords=("fahrrad", "rad", "fahrradstellplatz", "bike")),
]
