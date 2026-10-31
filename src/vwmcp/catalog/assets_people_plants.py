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


def tree_plan(d=6000.0, trunk=300.0, style=0.0, seed=1.0):
    """Tree from above: crown diameter d. style 0 = organic outline, 1 = circle."""
    r = d / 2
    items = []
    if style >= 1:
        items.append(circle(0, 0, r, role="outline"))
        items.append(circle(0, 0, r * 0.82, role="detail"))
    else:
        lobes = max(9, min(24, int(round(d / 450))))
        items.append(_cloud(0, 0, r, r, lobes, seed))
    items.append(circle(0, 0, trunk / 2, role="detail"))
    items.append(line(-trunk * 0.9, 0, trunk * 0.9, 0))
    items.append(line(0, -trunk * 0.9, 0, trunk * 0.9))
    return items


def tree_front(d=6000.0, trunk=300.0, style=0.0, seed=1.0, h=10000.0):
    """Tree in elevation: height h, crown width d."""
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


ASSETS = [
    dict(id="baum", name="Baum", category="plants",
         views={"plan": tree_plan, "front": tree_front},
         params={"d": (6000.0, "Kronendurchmesser"), "trunk": (300.0, "Stammdurchmesser"),
                 "style": (0.0, "0 = organische Krone, 1 = Kreis"), "seed": (1.0, "Variante der Kronenform"),
                 "h": (10000.0, "Höhe (nur Ansicht)")},
         insertion="Stammmitte (Ansicht: am Boden)", keywords=("baum", "bäume", "pflanze", "vegetation", "grün", "staffage")),
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
         views={"plan": car_plan},
         params={"l": (4700.0, "Länge"), "b": (1850.0, "Breite")},
         insertion="Fahrzeugmitte, Front nach +x", keywords=("auto", "pkw", "fahrzeug", "parkplatz", "stellplatz")),
]
