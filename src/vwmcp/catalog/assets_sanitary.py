"""Sanitary and kitchen assets (mm). Plan: wall on y = 0, object towards -y."""

from __future__ import annotations

from vwmcp.catalog.shapes import (arc, circle, front_rounded_rect, korb_oval, line, path, poly, rect,
                                  rounded_rect)

# ---------------------------------------------------------------- WC


def wc_wall_plan(b=360.0, t=540.0):
    plate_w = b * 0.72
    bowl_top = 40.0
    bowl_h = t - bowl_top
    cy = -(bowl_top + bowl_h / 2)
    return [
        rect(-plate_w / 2, 0, plate_w / 2, -70),                       # mounting plate
        korb_oval(0, cy, b, bowl_h),                                  # bowl
        korb_oval(0, cy - bowl_h * 0.05, b * 0.6, bowl_h * 0.6, role="detail"),  # opening
    ]


def wc_wall_front(b=360.0, t=540.0, h=400.0):
    rim = h
    return [
        poly([(-b / 2, rim), (b / 2, rim), (b / 2, rim - 30), (b * 0.3, rim - 150),
              (-b * 0.3, rim - 150), (-b / 2, rim - 30)]),             # ceramic
        rect(-b / 2, rim, b / 2, rim + 25, role="detail"),            # seat
        rect(-125, 1000, 125, 1150, role="detail"),                   # flush plate
    ]


def wc_wall_side(b=360.0, t=540.0, h=400.0):
    rim = h
    bottom = rim - 260
    return [
        path((0, bottom + 30), [("L", 0, rim + 25), ("L", t - 30, rim + 25),
                                ("A", t, rim - 5, t - 30, rim - 5, "cw"), ("L", t - 15, rim - 110),
                                ("L", t - 190, bottom), ("L", 60, bottom)]),
        line(15, rim, t - 15, rim),                                    # seat joint
    ]


def wc_floor_plan(b=380.0, t=700.0):
    tank_d = 190.0
    bowl_top = tank_d - 30
    bowl_h = t - bowl_top
    cy = -(bowl_top + bowl_h / 2)
    return [
        rounded_rect(-b / 2 - 10, 0, b / 2 + 10, -tank_d, 25),       # cistern
        korb_oval(0, cy, b * 0.95, bowl_h),
        korb_oval(0, cy - bowl_h * 0.05, b * 0.58, bowl_h * 0.6, role="detail"),
    ]


def wc_floor_side(b=380.0, t=700.0, h=420.0):
    return [
        rounded_rect(0, 440, 190, 800, 20),                           # cistern
        path((130, 0), [("L", t - 280, 0), ("L", t - 180, 220), ("L", t, h - 25),
                        ("A", t - 25, h, t - 25, h - 25, "ccw"), ("L", 170, h),
                        ("L", 170, 380), ("L", 210, 300)]),
        line(190, h - 15, t - 30, h - 15),                             # seat joint
    ]


# ---------------------------------------------------------------- urinal


def urinal_plan(b=350.0, t=330.0):
    return [
        front_rounded_rect(-b / 2, 0, b / 2, -t, b / 2 * 0.9),
        korb_oval(0, -t * 0.55, b * 0.6, t * 0.6, role="detail"),
    ]


def urinal_front(b=350.0, t=330.0, h=650.0):
    top = h + 400
    return [
        path((-b / 2, top), [("L", b / 2, top), ("L", b / 2, h + 60),
                             ("A", 0, h - b / 2 + 60, 0, h + 60, "cw"),
                             ("A", -b / 2, h + 60, 0, h + 60, "cw")]),
        korb_oval(0, h + 120, b * 0.55, 260, role="detail"),
    ]


# ---------------------------------------------------------------- wash basin


def basin_plan(b=600.0, t=480.0):
    return [
        front_rounded_rect(-b / 2, 0, b / 2, -t, 60),
        korb_oval(0, -t * 0.55, b * 0.62, t * 0.58, role="detail"),     # bowl
        circle(0, -t * 0.55, 18, role="detail"),                       # drain
        rect(-15, -25, 15, -95, role="detail"),                         # tap
    ]


def basin_front(b=600.0, t=480.0, h=850.0, mirror=0.0):
    items = [
        path((-b / 2, h), [("L", b / 2, h), ("L", b / 2, h - 40),
                           ("L", b / 2 - 60, h - 170), ("L", -b / 2 + 60, h - 170),
                           ("L", -b / 2, h - 40)]),
        poly([(-15, h), (-15, h + 120), (60, h + 120), (60, h + 100), (15, h + 100), (15, h)],
             role="detail"),                                            # tap
        rect(-30, h - 170, 30, h - 420, role="detail"),                 # siphon
    ]
    if mirror >= 1:
        items.append(rect(-b / 2, h + 250, b / 2, h + 1000, role="detail"))
    return items


def basin_side(b=600.0, t=480.0, h=850.0):
    return [
        path((0, h - 170), [("L", 0, h), ("L", t - 25, h), ("A", t, h - 25, t - 25, h - 25, "cw"),
                            ("L", t, h - 50), ("L", t - 120, h - 170)]),
        poly([(40, h), (40, h + 120), (150, h + 120), (150, h + 100), (60, h + 100), (60, h)],
             role="detail"),
    ]


def hand_basin_plan(b=450.0, t=350.0):
    return basin_plan(b, t)


# ---------------------------------------------------------------- shower, bath


def shower_plan(b=900.0, t=900.0, gully=1.0):
    items = [rect(-b / 2, 0, b / 2, -t)]
    if gully >= 1:  # linear drain along the wall
        items.append(rect(-b / 2 + 100, -40, b / 2 - 100, -90, role="detail"))
    else:           # point drain in the middle, slope lines to the corners
        cy = -t / 2
        items.append(rect(-60, cy + 60, 60, cy - 60, role="detail"))
        for x, y in ((-b / 2, 0), (b / 2, 0), (b / 2, -t), (-b / 2, -t)):
            items.append(line(x, y, -60 if x < 0 else 60, cy + (60 if y == 0 else -60)))
    items.append(arc(b / 2 - 150, -150, 60, 0, 360, role="detail"))   # shower head
    return items


def bath_plan(b=1700.0, t=750.0):
    r_in = min(t * 0.3, 260)
    return [
        rect(-b / 2, 0, b / 2, -t),
        rounded_rect(-b / 2 + 60, -60, b / 2 - 60, -t + 60, r_in, role="detail"),
        circle(-b / 2 + 60 + 180, -t / 2, 25, role="detail"),          # drain at the foot end
    ]


def bath_side(b=1700.0, t=750.0, h=600.0):
    return [
        rect(-b / 2, 0, b / 2, h),
        path((-b / 2 + 60, h), [("L", -b / 2 + 260, 150), ("L", b / 2 - 160, 150),
                                ("A", b / 2 - 60, 250, b / 2 - 160, 250, "ccw"),
                                ("L", b / 2 - 60, h)], closed=False, role="detail"),
    ]


# ---------------------------------------------------------------- kitchen


def sink_plan(b=800.0, t=600.0):
    return [
        rect(-b / 2, 0, b / 2, -t),
        rounded_rect(-b / 2 + 50, -80, 120, -t + 70, 40, role="detail"),     # bowl
        circle((-b / 2 + 50 + 120) / 2, (-80 - t + 70) / 2, 25, role="detail"),  # drain
        rect(170, -80, b / 2 - 50, -t + 70, role="detail"),                    # drainer
        circle(-b / 2 + 50 + 160, -45, 20, role="detail"),                     # tap
    ]


def hob_plan(b=600.0, t=600.0):
    items = [rect(-b / 2, 0, b / 2, -t), rect(-b / 2 + 30, -30, b / 2 - 30, -t + 30, role="detail")]
    for x, y, r in ((-b / 4, -t / 4 - 10, 95), (b / 4, -t / 4 - 10, 75),
                    (-b / 4, -3 * t / 4 + 10, 75), (b / 4, -3 * t / 4 + 10, 95)):
        items.append(circle(x, y, r, role="detail"))
    return items


def fridge_plan(b=600.0, t=600.0):
    return [rect(-b / 2, 0, b / 2, -t), line(-b / 2, -t + 40, b / 2, -t + 40),
            line(-b / 2, 0, b / 2, -t + 40)]


ASSETS = [
    dict(id="wc_wand", name="WC wandhängend", category="sanitary",
         views={"plan": wc_wall_plan, "front": wc_wall_front, "side": wc_wall_side},
         params={"b": (360.0, "Breite"), "t": (540.0, "Ausladung ab Wand"), "h": (400.0, "Höhe Oberkante Keramik (barrierefrei 460)")},
         insertion="Grundriss: Mitte Wandseite, Objekt nach -y; Ansicht: Mitte am Boden",
         keywords=("toilette", "klo", "wc", "sanitär", "wand", "hänge")),
    dict(id="wc_stand", name="WC stehend mit Spülkasten", category="sanitary",
         views={"plan": wc_floor_plan, "side": wc_floor_side},
         params={"b": (380.0, "Breite"), "t": (700.0, "Tiefe ab Wand"), "h": (420.0, "Höhe Sitz")},
         insertion="Grundriss: Mitte Wandseite, Objekt nach -y",
         keywords=("toilette", "klo", "wc", "stand", "spülkasten")),
    dict(id="urinal", name="Urinal", category="sanitary",
         views={"plan": urinal_plan, "front": urinal_front},
         params={"b": (350.0, "Breite"), "t": (330.0, "Ausladung"), "h": (650.0, "Höhe Unterkante")},
         insertion="Mitte Wandseite", keywords=("pissoir", "urinal", "sanitär")),
    dict(id="waschtisch", name="Waschtisch", category="sanitary",
         views={"plan": basin_plan, "front": basin_front, "side": basin_side},
         params={"b": (600.0, "Breite"), "t": (480.0, "Ausladung"), "h": (850.0, "Höhe Oberkante"),
                 "mirror": (0.0, "1 = Spiegel in der Ansicht")},
         insertion="Grundriss: Mitte Wandseite, Objekt nach -y; Ansicht: Mitte am Boden",
         keywords=("waschbecken", "lavabo", "waschtisch", "sanitär")),
    dict(id="handwaschbecken", name="Handwaschbecken", category="sanitary",
         views={"plan": hand_basin_plan},
         params={"b": (450.0, "Breite"), "t": (350.0, "Ausladung")},
         insertion="Mitte Wandseite", keywords=("waschbecken", "handwaschbecken", "gäste-wc")),
    dict(id="dusche", name="Dusche bodengleich", category="sanitary",
         views={"plan": shower_plan},
         params={"b": (900.0, "Breite (barrierefrei 1200)"), "t": (900.0, "Tiefe (barrierefrei 1200)"),
                 "gully": (1.0, "1 = Rinne an der Wand, 0 = Punktablauf mittig")},
         insertion="Mitte Wandseite", keywords=("dusche", "brause", "sanitär")),
    dict(id="badewanne", name="Badewanne", category="sanitary",
         views={"plan": bath_plan, "side": bath_side},
         params={"b": (1700.0, "Länge"), "t": (750.0, "Breite"), "h": (600.0, "Höhe Wannenrand")},
         insertion="Grundriss: Mitte der Längsseite an der Wand, Wanne nach -y",
         keywords=("wanne", "badewanne", "bad", "sanitär")),
    dict(id="spuele", name="Küchenspüle mit Abtropffläche", category="kitchen",
         views={"plan": sink_plan},
         params={"b": (800.0, "Breite"), "t": (600.0, "Tiefe Arbeitsplatte")},
         insertion="Mitte Wandseite", keywords=("spüle", "küche", "spülbecken")),
    dict(id="kochfeld", name="Kochfeld / Herd", category="kitchen",
         views={"plan": hob_plan},
         params={"b": (600.0, "Breite"), "t": (600.0, "Tiefe")},
         insertion="Mitte Wandseite", keywords=("herd", "kochfeld", "küche", "ceran")),
    dict(id="kuehlschrank", name="Kühlschrank", category="kitchen",
         views={"plan": fridge_plan},
         params={"b": (600.0, "Breite"), "t": (600.0, "Tiefe")},
         insertion="Mitte Wandseite", keywords=("kühlschrank", "küche", "kühl")),
]
