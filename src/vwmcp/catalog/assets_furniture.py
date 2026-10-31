"""Furniture assets (mm). Beds/wardrobes stand at a wall on y = 0 (towards
-y); tables and chairs are centred on 0, 0 (chair seat centre)."""

from __future__ import annotations

import math

from vwmcp.catalog.shapes import line, path, rect, rounded_rect


def _chair_plan_at(cx: float, cy: float, angle_deg: float, w: float = 450.0, d: float = 500.0):
    """Chair with its seat centre at (cx, cy). angle 0: back rest towards +y
    (the sitter faces -y); the chair is rotated counter-clockwise by angle."""
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)

    def T(x, y):
        return (cx + x * ca - y * sa, cy + x * sa + y * ca)

    def rotated_rect(x1, y1, x2, y2, role):
        pts = [T(x1, y1), T(x2, y1), T(x2, y2), T(x1, y2)]
        return {"type": "polygon", "points": [[round(px, 6) + 0.0, round(py, 6) + 0.0] for px, py in pts],
                "role": role}

    return [
        rotated_rect(-w / 2, -d / 2, w / 2, d / 2 - 60, "outline"),        # seat
        rotated_rect(-w / 2, d / 2 - 60, w / 2, d / 2, "detail"),          # back rest
    ]


def chair_plan(w=450.0, d=500.0):
    """Seat centred on 0, 0, back rest towards +y."""
    return [rounded_rect(-w / 2, -d / 2, w / 2, d / 2 - 70, 30),
            rounded_rect(-w / 2, d / 2 - 70, w / 2, d / 2, 20, role="detail")]


def chair_front(w=450.0, d=500.0, h=450.0):
    back_top = h + 420
    return [
        rect(-w / 2, h - 40, w / 2, h),                                    # seat
        rect(-w / 2 + 20, h, w / 2 - 20, back_top, role="detail"),          # back rest
        rect(-w / 2, 0, -w / 2 + 40, h - 40),                               # legs
        rect(w / 2 - 40, 0, w / 2, h - 40),
    ]


def chair_side(w=450.0, d=500.0, h=450.0):
    back_top = h + 420
    return [
        rect(0, h - 40, d, h),
        rect(d - 40, h, d, back_top),
        rect(0, 0, 40, h - 40),
        rect(d - 40, 0, d, h - 40),
    ]


def table_plan(b=1600.0, t=900.0, chairs=6.0, round_table=0.0):
    """Table centred on 0, 0 with chairs around it (6 = 2 per long side + ends)."""
    items = []
    n = max(0, int(round(chairs)))
    if round_table >= 1:
        r = b / 2
        items.append({"type": "circle", "center": [0.0, 0.0], "radius": r, "role": "outline"})
        for i in range(n):
            ang = 360.0 * i / n
            a = math.radians(ang)
            dist = r + 150
            items += _chair_plan_at(round(-math.sin(a) * dist, 6), round(math.cos(a) * dist, 6), ang)
        return items
    items.append(rect(-b / 2, -t / 2, b / 2, t / 2))
    ends = 2 if n >= 6 else (1 if n % 2 == 1 and n >= 3 else 0)
    side = n - ends
    top, bottom = (side + 1) // 2, side // 2
    for count, y, ang in ((top, t / 2 + 150, 0.0), (bottom, -t / 2 - 150, 180.0)):
        for i in range(count):
            items += _chair_plan_at(-b / 2 + b * (i + 0.5) / count, y, ang)
    if ends >= 1:
        items += _chair_plan_at(-b / 2 - 150, 0, 90.0)
    if ends >= 2:
        items += _chair_plan_at(b / 2 + 150, 0, -90.0)
    return items


def table_front(b=1600.0, t=900.0, chairs=6.0, round_table=0.0, h=750.0):
    return [rect(-b / 2, h - 40, b / 2, h), rect(-b / 2 + 40, 0, -b / 2 + 100, h - 40),
            rect(b / 2 - 100, 0, b / 2 - 40, h - 40)]


def bed_plan(b=1400.0, l=2000.0):
    """Bed at the wall (head end on y = 0), towards -y."""
    pillows = 2 if b >= 1200 else 1
    items = [rect(-b / 2, 0, b / 2, -l)]
    pw = (b - 120 - (pillows - 1) * 40) / pillows
    x = -b / 2 + 60
    for _ in range(pillows):
        items.append(rounded_rect(x, -60, x + pw, -380, 60, role="detail"))
        x += pw + 40
    # duvet folded back: diagonal fold line
    items.append(line(-b / 2, -560, b / 2, -560))
    items.append(line(b / 2 - 300, -560, b / 2, -860))
    return items


def sofa_plan(b=2100.0, t=900.0):
    arm = 180.0
    back = 180.0
    seats = max(1, int(round((b - 2 * arm) / 600)))
    items = [rounded_rect(-b / 2, 0, b / 2, -t, 40),
             rect(-b / 2 + arm, -back, b / 2 - arm, -t, role="detail")]
    sw = (b - 2 * arm) / seats
    for i in range(1, seats):
        x = -b / 2 + arm + i * sw
        items.append(line(x, -back, x, -t))
    return items


def wardrobe_plan(b=1000.0, t=600.0):
    """Wardrobe at the wall, doors at -t; German plan convention: diagonal."""
    return [rect(-b / 2, 0, b / 2, -t), line(-b / 2, -t + 20, b / 2, -t + 20),
            line(-b / 2, 0, b / 2, -t + 20)]


def desk_plan(b=1600.0, t=800.0):
    return [rect(-b / 2, 0, b / 2, -t)] + _chair_plan_at(0, -t - 150, 180.0)


def armchair_plan(b=800.0, t=850.0):
    """Armchair, back at y = 0, seat towards -y."""
    return sofa_plan(b, t)


def coffee_table_plan(b=1100.0, t=600.0):
    return [rect(-b / 2, t / 2, b / 2, -t / 2), rect(-b / 2 + 40, t / 2 - 40, b / 2 - 40, -t / 2 + 40, role="detail")]


def nightstand_plan(b=450.0, t=400.0):
    return [rect(-b / 2, 0, b / 2, -t), line(-b / 2, -t + 30, b / 2, -t + 30)]


def dresser_plan(b=1200.0, t=500.0):
    return [rect(-b / 2, 0, b / 2, -t), line(-b / 2, -t + 30, b / 2, -t + 30)]


def shelf_plan(b=1000.0, t=350.0):
    """Open shelf at the wall: outline, sides and a division every ~800 mm."""
    items = [rect(-b / 2, 0, b / 2, -t)]
    n = max(1, int(round(b / 800.0)))
    for i in range(1, n):
        x = -b / 2 + b * i / n
        items.append(line(x, 0, x, -t))
    items.append(line(-b / 2 + 25, -t, -b / 2 + 25, 0))
    items.append(line(b / 2 - 25, -t, b / 2 - 25, 0))
    return items


ASSETS = [
    dict(id="stuhl", name="Stuhl", category="furniture",
         views={"plan": chair_plan, "front": chair_front, "side": chair_side},
         params={"w": (450.0, "Breite"), "d": (500.0, "Tiefe"), "h": (450.0, "Sitzhöhe")},
         insertion="Grundriss: Sitzmitte, Lehne nach +y; Ansicht: Mitte am Boden",
         keywords=("stuhl", "sitz", "möbel")),
    dict(id="tisch", name="Esstisch mit Stühlen", category="furniture",
         views={"plan": table_plan, "front": table_front},
         params={"b": (1600.0, "Länge (rund: Durchmesser)"), "t": (900.0, "Breite"),
                 "chairs": (6.0, "Anzahl Stühle"), "round_table": (0.0, "1 = runder Tisch"),
                 "h": (750.0, "Tischhöhe (nur Ansicht)")},
         insertion="Tischmitte", keywords=("tisch", "esstisch", "stühle", "möbel", "essen")),
    dict(id="bett", name="Bett", category="furniture",
         views={"plan": bed_plan},
         params={"b": (1400.0, "Breite (90/140/160/180 cm)"), "l": (2000.0, "Länge")},
         insertion="Mitte Kopfende an der Wand, Bett nach -y", keywords=("bett", "schlafen", "möbel")),
    dict(id="sofa", name="Sofa", category="furniture",
         views={"plan": sofa_plan},
         params={"b": (2100.0, "Breite"), "t": (900.0, "Tiefe")},
         insertion="Mitte Rückseite (Wand), Sofa nach -y", keywords=("sofa", "couch", "möbel", "wohnen")),
    dict(id="schrank", name="Schrank", category="furniture",
         views={"plan": wardrobe_plan},
         params={"b": (1000.0, "Breite"), "t": (600.0, "Tiefe")},
         insertion="Mitte Rückwand, Türen nach -y", keywords=("schrank", "kleiderschrank", "möbel")),
    dict(id="schreibtisch", name="Schreibtisch mit Stuhl", category="furniture",
         views={"plan": desk_plan},
         params={"b": (1600.0, "Breite"), "t": (800.0, "Tiefe")},
         insertion="Mitte Wandseite, Arbeitsplatz nach -y", keywords=("schreibtisch", "arbeitsplatz", "büro", "möbel")),
    dict(id="sessel", name="Sessel", category="furniture",
         views={"plan": armchair_plan},
         params={"b": (800.0, "Breite"), "t": (850.0, "Tiefe")},
         insertion="Mitte Rückseite, Sitz nach -y", keywords=("sessel", "armchair", "möbel", "wohnen")),
    dict(id="couchtisch", name="Couchtisch", category="furniture",
         views={"plan": coffee_table_plan},
         params={"b": (1100.0, "Länge"), "t": (600.0, "Breite")},
         insertion="Tischmitte", keywords=("couchtisch", "beistelltisch", "möbel", "wohnen")),
    dict(id="nachttisch", name="Nachttisch", category="furniture",
         views={"plan": nightstand_plan},
         params={"b": (450.0, "Breite"), "t": (400.0, "Tiefe")},
         insertion="Mitte Wandseite, nach -y", keywords=("nachttisch", "nachtkästchen", "schlafen", "möbel")),
    dict(id="kommode", name="Kommode / Sideboard", category="furniture",
         views={"plan": dresser_plan},
         params={"b": (1200.0, "Breite"), "t": (500.0, "Tiefe")},
         insertion="Mitte Wandseite, nach -y", keywords=("kommode", "sideboard", "anrichte", "möbel")),
    dict(id="regal", name="Regal", category="furniture",
         views={"plan": shelf_plan},
         params={"b": (1000.0, "Breite"), "t": (350.0, "Tiefe")},
         insertion="Mitte Wandseite, nach -y", keywords=("regal", "bücherregal", "möbel")),
]
