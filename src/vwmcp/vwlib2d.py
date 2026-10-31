# 2D drawing helpers executed INSIDE Vectorworks (embedded Python 3.9).
#
# Sent after vwlib.py with every 2D tool call and uses its helpers (_str,
# _is_nil, obj_name, h_uuid, handle, current_document, _write_fields).
# Keep it 3.9-compatible: no match statements, no `X | Y`, stdlib only.
#
# Conventions:
# - All coordinates are in document units, passed through unrounded.
# - Objects are addressed by UUID; handles never leave a run.
# - Every write function returns enough for the server to journal an exact
#   revert ("created" uuids, "replaced" snapshots, "attrs" before-values).

import math

KINDS = {
    2: "line", 3: "rect", 4: "oval", 5: "polygon", 6: "arc", 8: "freehand",
    10: "text", 11: "group", 13: "rounded_rect", 14: "bitmap", 15: "symbol",
    17: "locus", 21: "polyline", 63: "dimension", 68: "wall", 71: "slab",
    86: "plugin", 122: "viewport",
}
SHAPE_TYPES = (2, 3, 4, 5, 6, 8, 13, 21)  # objects with pen/fill geometry
EDITABLE = (2, 3, 4, 5, 6, 10, 11, 15, 17, 21, 63)  # can be snapshot + restored
MM_PER_MIL = 0.0254
RES_FOLDERS = {66: (105, 131, 104, 124), 96: (174,)}  # hatches, line types
TEXT_JUST = {"left": 1, "center": 2, "right": 3}
TEXT_VALIGN = {"top": 1, "top_baseline": 2, "center": 3, "baseline": 4, "bottom": 5}
DIM_TYPES = {"horizontal": 0, "vertical": 1, "aligned": 4}


class NotEditable(ValueError):
    pass


# ---- units, colors ------------------------------------------------------------

def units():
    u = vs.GetUnits()
    upi = float(u[3])
    return {"name": _str(u[4]).strip(), "area_name": _str(u[5]).strip(),
            "upi": upi, "mm_per_unit": 25.4 / upi}


def none_class():
    return _str(vs.ClassList(1))


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(int(round(c / 257.0)) for c in rgb[:3])


def _color(c):
    """'#rrggbb', '#rgb' or [r, g, b] (0-255) -> 16-bit tuple."""
    if isinstance(c, str):
        s = c.strip().lstrip("#")
        if len(s) == 3:
            s = "".join(ch * 2 for ch in s)
        if len(s) != 6:
            raise ValueError("color %r: use '#rrggbb'" % c)
        return tuple(int(s[i:i + 2], 16) * 257 for i in (0, 2, 4))
    vals = [int(v) for v in c]
    if len(vals) != 3 or max(vals) > 255 or min(vals) < 0:
        raise ValueError("color %r: use [r, g, b] with 0-255" % (c,))
    return tuple(v * 257 for v in vals)


def _pt(p):
    return (float(p[0]), float(p[1]))


_JOB_CACHE = {}  # per job: the libraries run in a fresh scope for every job


def _arc_radius(h, c, p0, sweep):
    """Radius of an arc as stored. Vectorworks stores it exactly, but
    start - center in document units loses the last bits (20.6 - 20.0 =
    0.6000000000000014). Full circle: half the stored width (exact).
    Otherwise snap to whole picometres only when the difference is within
    the rounding noise of the subtraction (a few ulps of the coordinates), so
    a real radius like 1000/3 mm is kept as it is."""
    if abs(abs(sweep) - 360.0) < 1e-9:
        return vs.HWidth(h) / 2.0
    r = math.hypot(p0[0] - c[0], p0[1] - c[1])
    if not _JOB_CACHE.get("mmpu"):
        _JOB_CACHE["mmpu"] = units()["mm_per_unit"]
    mmpu = _JOB_CACHE["mmpu"]
    r_mm = r * mmpu
    clean = round(r_mm, 9)
    noise = 16.0 * max(math.ulp(abs(p0[0]) + abs(c[0])), math.ulp(abs(p0[1]) + abs(c[1])), math.ulp(r)) * mmpu
    return clean / mmpu if abs(r_mm - clean) <= noise else r


def _bbox(h):
    (x1, y1), (x2, y2) = vs.GetBBox(h)
    return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]


# ---- resources, classes, layers ------------------------------------------------

def _resource_names(rtype, folder):
    lid, n = vs.BuildResourceList(rtype, folder, "")
    return lid, [_str(vs.GetNameFromResourceList(lid, i)) for i in range(1, n + 1)]


def ensure_resource(rtype, name):
    """Hatch (66) or line type (96) by name; imported from the default
    content if the document doesn't have it yet. Returns the name in the doc."""
    _, doc = _resource_names(rtype, 0)
    if name in doc:
        return name
    for f in RES_FOLDERS[rtype]:
        lid, names = _resource_names(rtype, -f)
        if name in names:
            h = vs.ImportResourceToCurrentFile(lid, names.index(name) + 1)
            if _is_nil(h):
                break
            return _str(vs.GetName(h))
    kind = "hatch" if rtype == 66 else "line type"
    raise LookupError("%s %r not found in the document or Vectorworks' default content "
                      "(list them with vw2d_resources)" % (kind, name))


def list_resources(kind, query=None, include_defaults=True, limit=300):
    rtype = {"hatch": 66, "line_type": 96}[kind]
    out, seen = [], set()
    sources = [(0, "document")] + ([(-f, "default") for f in RES_FOLDERS[rtype]]
                                   if include_defaults else [])
    q = (query or "").lower()
    for folder, src in sources:
        _, names = _resource_names(rtype, folder)
        for n in names:
            if n in seen or (q and q not in n.lower()):
                continue
            seen.add(n)
            out.append({"name": n, "source": src})
            if len(out) >= limit:
                return out
    return out


def class_names():
    return [_str(vs.ClassList(i)) for i in range(1, vs.ClassNum() + 1)]


def _class_attrs(name):
    a = {}
    try:
        a["pen"] = _hex(vs.GetClPenFore(name))
        a["fill"] = _hex(vs.GetClFillBack(name))
        a["fill_pattern"] = vs.GetClFPat(name)
        a["line_weight_mm"] = round(vs.GetClLW(name) * MM_PER_MIL, 4)
        ls = vs.GetClLSN(name)
        a["line_style"] = _str(vs.Index2Name(-ls)) if ls < 0 else {0: "none", 2: "solid"}.get(ls, ls)
        ok, hatch = vs.GetClVectorFill(name)
        a["hatch"] = _str(hatch) if ok else None
        a["use_at_creation"] = bool(vs.GetClUseGraphic(name))
    except Exception as e:  # noqa: BLE001 - attributes are informational
        a["error"] = str(e)
    return a


def ensure_class(name, attrs=None):
    """Create the class if missing (active class is kept) and optionally set
    its graphic attributes. Returns True if it was created."""
    created = name not in class_names()
    if created:
        prev = _str(vs.ActiveClass())
        vs.NameClass(name)
        vs.NameClass(prev)
    if attrs:
        _apply_class_attrs(name, attrs)
    return created


def _apply_class_attrs(name, a):
    if "pen" in a:
        vs.SetClPenFore(name, _color(a["pen"]))
    if "fill" in a:
        if a["fill"] == "none":
            vs.SetClFPat(name, 0)
        else:
            col = _color(a["fill"])
            vs.SetClFPat(name, 1)
            vs.SetClFillBack(name, col)
            vs.SetClFillFore(name, col)
    if a.get("hatch"):
        vs.SetClVectorFill(name, ensure_resource(66, a["hatch"]))
    if "line_weight" in a:
        vs.SetClLW(name, int(round(float(a["line_weight"]) / MM_PER_MIL)))
    if "line_style" in a:
        vs.SetClLSN(name, _lsn(a["line_style"]))
    if "opacity" in a:
        po, fo = _opacity(a["opacity"])
        vs.SetCLOpacityN(name, fo, po)  # (class, fill, pen)
    vs.SetClUseGraphic(name, bool(a.get("use_at_creation", True)))


def ensure_layer(name, scale=None):
    """Create a design layer if missing (active layer is kept)."""
    created = _is_nil(vs.GetLayerByName(name))
    if created:
        prev = _str(vs.GetLName(vs.ActLayer()))
        vs.Layer(name)
        vs.Layer(prev)
    if scale is not None:
        vs.SetLScale(vs.GetLayerByName(name), float(scale))
    return created


def setup_structure(classes=None, layers=None, active_layer=None, active_class=None):
    out = {"classes_created": [], "classes_updated": [], "layers_created": [], "layers_updated": []}
    for c in classes or []:
        if ensure_class(c["name"], c.get("attributes")):
            out["classes_created"].append(c["name"])
        elif c.get("attributes"):
            out["classes_updated"].append(c["name"])
    for l in layers or []:
        if ensure_layer(l["name"], l.get("scale")):
            out["layers_created"].append(l["name"])
        elif l.get("scale") is not None:
            out["layers_updated"].append(l["name"])
    if active_layer:
        if _is_nil(vs.GetLayerByName(active_layer)):
            raise LookupError("no layer %r" % active_layer)
        vs.Layer(active_layer)
    if active_class:
        if active_class not in class_names():
            raise LookupError("no class %r" % active_class)
        vs.NameClass(active_class)
    out["document"] = current_document()
    return out


# ---- attributes ---------------------------------------------------------------

def _lsn(style):
    if style == "solid":
        return 2
    if style == "none":
        return 0
    return -vs.Name2Index(ensure_resource(96, style))


def _opacity(o):
    if isinstance(o, (int, float)):
        return int(o), int(o)
    return int(o[0]), int(o[1])


def read_attrs(h):
    ok, hatch = vs.GetVectorFill(h)
    fpat = vs.GetFPat(h)
    ls = vs.GetLSN(h)
    okop, po, fo = vs.GetOpacityN(h)
    a = {
        "pen": _hex(vs.GetPenFore(h)),
        "fill": "none" if fpat == 0 else ("hatch" if ok else ("solid" if fpat == 1 else "pattern")),
        "fill_color": _hex(vs.GetFillBack(h)),
        "hatch": _str(hatch) if ok else None,
        "line_style": _str(vs.Index2Name(-ls)) if ls < 0 else {0: "none", 2: "solid"}.get(ls, "pattern %d" % ls),
        "line_weight_mm": round(vs.GetLW(h) * MM_PER_MIL, 4),
        "opacity": [po, fo],
        "by_class": {
            "fill": bool(vs.IsFPatByClass(h)),
            "fill_color": bool(vs.IsFillColorByClass(h)),
            "pen_color": bool(vs.IsPenColorByClass(h)),
            "line_weight": bool(vs.IsLWByClass(h)),
            "line_style": bool(vs.IsLSByClass(h)),
        },
    }
    return a


def raw_attrs(h):
    """Exact attribute state for restoring (16-bit colors, raw indices)."""
    ok, hatch = vs.GetVectorFill(h)
    okop, po, fo = vs.GetOpacityN(h)
    return {
        "pen_fore": list(vs.GetPenFore(h)), "pen_back": list(vs.GetPenBack(h)),
        "fill_fore": list(vs.GetFillFore(h)), "fill_back": list(vs.GetFillBack(h)),
        "fpat": vs.GetFPat(h), "hatch": _str(hatch) if ok else None,
        "lsn": vs.GetLSN(h), "lw": vs.GetLW(h), "opacity": [po, fo],
        "by_class": [bool(vs.IsFPatByClass(h)), bool(vs.IsFillColorByClass(h)),
                     bool(vs.IsPenColorByClass(h)), bool(vs.IsLWByClass(h)),
                     bool(vs.IsLSByClass(h))],
    }


def restore_raw_attrs(h, r):
    vs.SetPenFore(h, tuple(r["pen_fore"]))
    vs.SetPenBack(h, tuple(r["pen_back"]))
    vs.SetFillFore(h, tuple(r["fill_fore"]))
    vs.SetFillBack(h, tuple(r["fill_back"]))
    if r.get("hatch"):
        vs.SetVectorFill(h, r["hatch"])
    else:
        vs.SetFPat(h, r["fpat"])
    vs.SetLSN(h, r["lsn"])
    vs.SetLW(h, r["lw"])
    vs.SetOpacityN(h, r["opacity"][0], r["opacity"][1])
    fp, fc, pc, lw, ls = r["by_class"]
    # By-class flags last: the color setters clear them.
    if fp:
        vs.SetFPatByClass(h)
    if fc:
        vs.SetFillColorByClass(h)
    if pc:
        vs.SetPenColorByClass(h)
    if lw:
        vs.SetLWByClass(h)
    if ls:
        vs.SetLSByClass(h)


def _set_name(h, name):
    if not name:
        return
    other = named(name)
    if other is not None and str(other) != str(h):
        kind = {94: "a class", 31: "a layer", 16: "a symbol"}.get(vs.GetTypeN(other), "another object")
        raise ValueError("name %r is already used by %s in this document (names are shared by "
                         "objects, classes, layers and resources)" % (name, kind))
    vs.SetName(h, name)


def apply_attrs(h, a):
    """Apply attributes. Keys: class, name, pen, fill ('none' | color),
    hatch, line_style ('solid' | 'none' | name), line_weight (mm), opacity,
    by_class (True = every graphic attribute from the class). Any value
    'class' means 'by class' for that attribute."""
    if not a:
        return
    if a.get("class"):
        ensure_class(a["class"])
        vs.SetClass(h, a["class"])
    if "name" in a:
        _set_name(h, a["name"])
    later = []
    pen = a.get("pen")
    if pen == "class":
        later.append(vs.SetPenColorByClass)
    elif pen is not None:
        vs.SetPenFore(h, _color(pen))
    lw = a.get("line_weight")
    if lw == "class":
        later.append(vs.SetLWByClass)
    elif lw is not None:
        vs.SetLW(h, int(round(float(lw) / MM_PER_MIL)))
    ls = a.get("line_style")
    if ls == "class":
        later.append(vs.SetLSByClass)
    elif ls is not None:
        vs.SetLSN(h, _lsn(ls))
    fill = a.get("fill")
    if fill == "class":
        later.append(vs.SetFPatByClass)
        later.append(vs.SetFillColorByClass)
    elif fill == "none":
        vs.SetFPat(h, 0)
    elif fill is not None:
        col = _color(fill)
        vs.SetFPat(h, 1)
        vs.SetFillBack(h, col)
        vs.SetFillFore(h, col)
    if a.get("hatch"):
        if not vs.SetVectorFill(h, ensure_resource(66, a["hatch"])):
            raise ValueError("could not apply hatch %r" % a["hatch"])
    if "opacity" in a and a["opacity"] != "class":
        po, fo = _opacity(a["opacity"])
        vs.SetOpacityN(h, po, fo)
    if a.get("by_class"):
        later = [vs.SetFPatByClass, vs.SetFillColorByClass, vs.SetPenColorByClass,
                 vs.SetLWByClass, vs.SetLSByClass]
    for fn in later:
        fn(h)


# ---- reading -------------------------------------------------------------------

def _verts(h):
    out = []
    for i in range(1, vs.GetVertNum(h) + 1):
        p, t, r = vs.GetPolylineVertex(h, i)
        out.append([p[0], p[1], int(t), float(r)])
    return out


def _holes(h):
    if vs.GetTypeN(h) != 21:
        return []
    ok, n = vs.GetNumHoles(h)
    out = []
    for i in range(1, (n if ok else 0) + 1):
        okh, hh = vs.GetHole(h, i)
        if okh and not _is_nil(hh):
            out.append(_verts(hh))
    return out


def _approx_polygon(h):
    """Vectorworks' own polygonization (temporary copy, deleted again)."""
    g = vs.ConvertToPolygon(h, 512)
    if _is_nil(g):
        return None
    pts = [list(vs.GetPolyPt(g, i)) for i in range(1, vs.GetVertNum(g) + 1)]
    vs.DelObject(g)
    return pts


def _layer_name(h):
    """Layer of an object; '' for objects without one (symbol components)."""
    lay = vs.GetLayer(h)
    return "" if _is_nil(lay) else _str(vs.GetLName(lay))


def read_obj(h, geometry=True, parent=None, allow_temp=True, depth=0):
    t = vs.GetTypeN(h)
    rec = {"uuid": h_uuid(h), "kind": KINDS.get(t, "type%d" % t), "type_code": t,
           "name": obj_name(h), "class": _str(vs.GetClass(h)),
           "layer": _layer_name(h), "bbox": _bbox(h)}
    if parent:
        rec["parent"] = parent
    if t in SHAPE_TYPES or t in (10, 17, 63):
        try:
            rec["attrs"] = read_attrs(h)
        except Exception:  # noqa: BLE001
            pass
    if not geometry:
        return rec
    if t == 2:
        rec["p1"], rec["p2"] = list(vs.GetSegPt1(h)), list(vs.GetSegPt2(h))
    elif t == 3:
        rec["corners"] = [list(vs.Get2DPt(h, i)) for i in range(1, 5)]
        rec["angle"] = vs.HAngle(h)
        rec["width"], rec["height"] = vs.HWidth(h), vs.HHeight(h)
        rec["center"] = list(vs.HCenter(h))
    elif t == 4:
        rec["center"] = list(vs.HCenter(h))
        rec["width"], rec["height"] = vs.HWidth(h), vs.HHeight(h)
        rec["angle"] = vs.HAngle(h)
    elif t in (5, 21):
        rec["vertices"] = _verts(h)
        rec["closed"] = bool(vs.IsPolyClosed(h))
        rec["holes"] = _holes(h)
        if allow_temp and any(v[2] in (1, 2, 4) for v in rec["vertices"]):
            rec["approx_polygon"] = _approx_polygon(h)
    elif t == 6:
        c, p0, p1 = vs.Get2DPt(h, 1), vs.Get2DPt(h, 2), vs.Get2DPt(h, 3)
        start, sweep = vs.GetArc(h)
        rec["center"], rec["start_point"], rec["end_point"] = list(c), list(p0), list(p1)
        rec["radius"] = _arc_radius(h, c, p0, sweep)
        rec["start_angle"], rec["sweep"] = start, sweep
    elif t == 10:
        origin, ang, mirrored = vs.GetTextOrientation(h)
        rec["text"] = _str(vs.GetText(h))
        rec["origin"], rec["angle"], rec["mirrored"] = list(origin), ang, bool(mirrored)
        rec["size"] = vs.GetTextSize(h, 0)
        rec["font"] = _str(vs.GetFontName(vs.GetTextFont(h, 0)))
        rec["just"], rec["valign"] = vs.GetTextJust(h), vs.GetTextVerticalAlign(h)
    elif t == 63:
        rec["p1"], rec["p2"] = list(vs.GetSegPt1(h)), list(vs.GetSegPt2(h))
        rec["text"] = _str(vs.GetDimText(h))
    elif t == 15:
        rec["symbol"] = _str(vs.GetSymName(h))
        rec["location"] = list(vs.GetSymLoc(h))[:2]
        rec["rotation"] = vs.GetSymRot(h)
    elif t == 17:
        rec["point"] = list(vs.GetLocPt(h))[:2]
    elif t == 11:
        rec["children"] = [h_uuid(k) for k in children(h)]
    elif t in (8, 13) and allow_temp:
        rec["approx_polygon"] = _approx_polygon(h)
    if t in (3, 4, 5, 6, 8, 13, 21):
        try:
            rec["area"] = vs.HAreaN(h)
            rec["perimeter"] = vs.HPerimN(h)
        except Exception:  # noqa: BLE001
            pass
    return rec


def _is_sheet(layer):
    return vs.GetObjectVariableInt(layer, 154) == 2


def _bbox_hits(h, box):
    x1, y1, x2, y2 = _bbox(h)
    return not (x2 < box[0] or x1 > box[2] or y2 < box[1] or y1 > box[3])


def collect(uuids=None, layer=None, cls=None, selection=False, bbox=None):
    """Top-level handles in scope. layer: name, '*' (all design layers) or
    None (active layer, unless uuids/selection are given). bbox
    [x1, y1, x2, y2] keeps only objects whose bounding box touches it."""
    if uuids:
        return [handle(u) for u in uuids]
    found = []
    vs.ForEachObject(found.append, "(SEL=TRUE)" if selection else "(ALL)")
    act = _str(vs.GetLName(vs.ActLayer()))
    out, seen = [], set()
    for h in found:
        par = vs.GetParent(h)
        if not _is_nil(par) and vs.GetTypeN(par) == 11:
            continue  # inside a group: reached through the group
        lay = vs.GetLayer(h)
        lname = _str(vs.GetLName(lay))
        if layer == "*":
            if _is_sheet(lay):
                continue
        elif layer is not None:
            if lname != layer:
                continue
        elif not selection and lname != act:
            continue
        if cls is not None and _str(vs.GetClass(h)) != cls and vs.GetTypeN(h) != 11:
            continue
        if bbox is not None and not _bbox_hits(h, bbox):
            continue
        u = h_uuid(h)
        if u not in seen:
            seen.add(u)
            out.append(h)
    return out


def _walk(h, out, geometry, allow_temp, parent, cls, depth=0):
    rec = read_obj(h, geometry, parent, allow_temp, depth)
    if vs.GetTypeN(h) == 11:
        out.append(rec)
        if depth < 20:
            for k in children(h):
                _walk(k, out, geometry, allow_temp, rec["uuid"], cls, depth + 1)
        return
    if cls is None or rec["class"] == cls:
        out.append(rec)


def read_objects(uuids=None, layer=None, cls=None, selection=False, geometry=True,
                 allow_temp=True, limit=20000, bbox=None):
    out = []
    hs = collect(uuids, layer, cls, selection, bbox)
    stopped = False
    for i, h in enumerate(hs):
        _walk(h, out, geometry, allow_temp, None, cls)
        if len(out) >= limit and i + 1 < len(hs):
            stopped = True
            break
    return {"document": current_document(), "units": units(), "none_class": none_class(),
            "active_layer": _str(vs.GetLName(vs.ActLayer())),
            "objects": out[:limit], "truncated": stopped or len(out) > limit}


# ---- structure overview ----------------------------------------------------------

def structure():
    layers = []
    lay = vs.FLayer()
    act = _str(vs.GetLName(vs.ActLayer()))
    while not _is_nil(lay):
        layers.append({"name": _str(vs.GetLName(lay)),
                       "kind": "sheet" if _is_sheet(lay) else "design",
                       "scale": vs.GetLScale(lay), "objects": vs.NumObj(lay),
                       "visibility": vs.GetObjectVariableInt(lay, 153),
                       "active": _str(vs.GetLName(lay)) == act})
        lay = vs.NextLayer(lay)
    per_class, matrix, explicit = {}, {}, {}
    kinds = {}

    def visit(h, depth=0):
        t = vs.GetTypeN(h)
        c = _str(vs.GetClass(h))
        l = _str(vs.GetLName(vs.GetLayer(h)))
        per_class[c] = per_class.get(c, 0) + 1
        key = l + "\t" + c
        matrix[key] = matrix.get(key, 0) + 1
        kk = c + "\t" + KINDS.get(t, "type%d" % t)
        kinds[kk] = kinds.get(kk, 0) + 1
        if t in SHAPE_TYPES:
            try:
                if not (vs.IsFPatByClass(h) and vs.IsPenColorByClass(h) and vs.IsLWByClass(h)
                        and vs.IsLSByClass(h) and vs.IsFillColorByClass(h)):
                    explicit[c] = explicit.get(c, 0) + 1
            except Exception:  # noqa: BLE001
                pass
        if t == 11 and depth < 20:
            for k in children(h):
                visit(k, depth + 1)

    found = []
    vs.ForEachObject(found.append, "(ALL)")
    for h in found:
        par = vs.GetParent(h)
        if not _is_nil(par) and vs.GetTypeN(par) == 11:
            continue
        visit(h)
    classes = []
    for name in class_names():
        classes.append({"name": name, "objects": per_class.get(name, 0),
                        "explicit_attributes": explicit.get(name, 0),
                        "attributes": _class_attrs(name)})
    return {"document": current_document(), "units": units(), "none_class": none_class(),
            "active_class": _str(vs.ActiveClass()), "layers": layers, "classes": classes,
            "layer_class_counts": [{"layer": k.split("\t")[0], "class": k.split("\t")[1], "objects": v}
                                   for k, v in sorted(matrix.items())],
            "class_kind_counts": [{"class": k.split("\t")[0], "kind": k.split("\t")[1], "objects": v}
                                  for k, v in sorted(kinds.items())]}


# ---- creating --------------------------------------------------------------------

def _begin(layer=None, create_layer=True):
    state = (_str(vs.GetLName(vs.ActLayer())), _str(vs.ActiveClass()))
    vs.PushAttrs()
    if layer and layer != state[0]:  # switching layers costs ~13 ms in a 20 000-object layer
        if _is_nil(vs.GetLayerByName(layer)) and not create_layer:
            vs.PopAttrs()
            raise LookupError("no layer %r" % layer)
        vs.Layer(layer)
    return state


def _end(state):
    vs.PopAttrs()
    if _str(vs.GetLName(vs.ActLayer())) != state[0]:
        vs.Layer(state[0])
    vs.NameClass(state[1])


def _use_layer(name):
    """Make an existing layer active if it isn't (for loops over many objects:
    switch once, not twice per object)."""
    if name and _str(vs.GetLName(vs.ActLayer())) != name and not _is_nil(vs.GetLayerByName(name)):
        vs.Layer(name)


def make_poly(verts, closed):
    if closed:
        vs.ClosePoly()
    else:
        vs.OpenPoly()
    vs.BeginPoly()
    for v in verts:
        t = int(v[2]) if len(v) > 2 else 0
        if t == 3:
            vs.ArcTo((float(v[0]), float(v[1])), float(v[3]))
        elif t == 0:
            vs.AddPoint((float(v[0]), float(v[1])))
        else:
            raise ValueError("vertex type %d can't be drawn exactly; use corners and arcs" % t)
    vs.EndPoly()
    h = vs.LNewObj()
    if _is_nil(h):
        raise RuntimeError("Vectorworks did not create the polygon")
    return h


def add_holes(h, holes):
    for hv in holes or []:
        hh = make_poly(hv, True)
        ok, h2 = vs.AddHole(h, hh)
        vs.DelObject(hh)
        if not ok or _is_nil(h2):
            raise RuntimeError("Vectorworks could not cut the hole (must lie inside the outline)")
        h = h2
    return h


def _make_text(it):
    vs.TextSize(float(it.get("size", 10)))
    if it.get("font"):
        fid = vs.GetFontID(it["font"])
        if fid <= 0:
            raise LookupError("font %r not available" % it["font"])
        vs.TextFont(fid)
    vs.TextJust(TEXT_JUST[it.get("align", "left")])
    vs.TextVerticalAlign(TEXT_VALIGN[it.get("valign", "baseline")])
    vs.TextRotate(float(it.get("angle", 0.0)))
    vs.TextOrigin(_pt(it["position"]))
    vs.CreateText(it["text"])
    vs.TextRotate(0.0)
    return vs.LNewObj()


def _make_one(it):
    k = it["kind"]
    if k == "poly":
        return add_holes(make_poly(it["verts"], it["closed"]), it.get("holes"))
    if k == "rect":
        (x1, y1), (x2, y2) = it["p1"], it["p2"]
        vs.Rect((min(x1, x2), max(y1, y2)), (max(x1, x2), min(y1, y2)))
        return vs.LNewObj()
    if k == "oval":
        (x1, y1), (x2, y2) = it["p1"], it["p2"]
        vs.Oval((min(x1, x2), max(y1, y2)), (max(x1, x2), min(y1, y2)))
        h = vs.LNewObj()
        if it.get("angle"):
            vs.HRotate(h, vs.HCenter(h), float(it["angle"]))
        return h
    if k == "circle":
        c = it["center"]
        vs.ArcByCenter(float(c[0]), float(c[1]), float(it["radius"]), 0.0, 360.0)
        return vs.LNewObj()
    if k == "arc":
        c = it["center"]
        vs.ArcByCenter(float(c[0]), float(c[1]), float(it["radius"]), float(it["start"]),
                       float(it["sweep"]))
        return vs.LNewObj()
    if k == "line":
        vs.MoveTo(_pt(it["p1"]))
        vs.LineTo(_pt(it["p2"]))
        return vs.LNewObj()
    if k == "text":
        return _make_text(it)
    if k == "dim":
        vs.LinearDim(_pt(it["p1"]), _pt(it["p2"]), float(it.get("offset", 0.0)),
                     DIM_TYPES[it.get("orientation", "aligned")], 0, 0, 0)
        return vs.LNewObj()
    if k == "chain_dim":
        pts = it["points"]
        dt = DIM_TYPES[it.get("orientation", "aligned")]
        off = float(it.get("offset", 0.0))
        vs.LinearDim(_pt(pts[0]), _pt(pts[1]), off, dt, 0, 0, 0)
        h = vs.LNewObj()
        for i in range(1, len(pts) - 1):
            vs.LinearDim(_pt(pts[i]), _pt(pts[i + 1]), off, dt, 0, 0, 0)
            h2 = vs.LNewObj()
            h3 = vs.CreateChainDimension(h, h2)
            if _is_nil(h3):
                raise RuntimeError("could not chain dimension %d" % i)
            h = h3
        return h
    if k == "locus":
        vs.Locus(_pt(it["point"]))
        return vs.LNewObj()
    raise ValueError("unknown item kind %r" % k)


def draw_items(items):
    """Create items (already validated + normalized by the server). All or
    nothing: on any error everything created so far is deleted."""
    made = []
    home = _str(vs.GetLName(vs.ActLayer()))
    try:
        for it in items:
            _use_layer(it.get("layer"))
            state = _begin(it.get("layer"))
            try:
                if it.get("class"):
                    ensure_class(it["class"])
                    vs.NameClass(it["class"])
                h = _make_one(it)
            finally:
                _end(state)
            if _is_nil(h):
                raise RuntimeError("Vectorworks did not create item %r" % it.get("kind"))
            made.append(h)
            apply_attrs(h, it.get("attributes"))
    except BaseException:
        for h in made:
            vs.DelObject(h)
        _use_layer(home)
        raise
    _use_layer(home)
    recs = [read_obj(h, True, None, False) for h in made]
    return {"document": current_document(), "objects": recs}


# ---- assets: symbol definitions + instances ---------------------------------------

SYMDEF, SYMFOLDER, SYMBOL = 16, 92, 15


def _draw_local(items):
    """Draw normalized items with their own classes (inside BeginSym or
    BeginGroup). Returns the handles."""
    made = []
    for it in items:
        vs.NameClass(it["class"])
        h = _make_one(it)
        if _is_nil(h):
            raise RuntimeError("Vectorworks did not create item %r" % it.get("kind"))
        apply_attrs(h, it.get("attributes"))
        made.append(h)
    return made


def _symbol_folder(name):
    """Handle of the symbol folder `name`, created if missing. (handle, created)"""
    h = named(name, (SYMFOLDER,))
    if h is not None:
        return h, False
    if named(name) is not None:
        raise ValueError("the name %r is already used by another object or resource" % name)
    vs.NameObject(name)
    vs.BeginFolderN(SYMDEF)
    vs.EndFolder()
    h = named(name, (SYMFOLDER,))
    if h is None:
        raise RuntimeError("Vectorworks did not create the symbol folder %r" % name)
    return h, True


def _define_symbol(name, items, default_class=None, description=None):
    """Create symbol definition `name` from items drawn at local coordinates
    (insertion point = 0, 0). All or nothing."""
    opened = False
    try:
        vs.BeginSym(name)
        opened = True
        _draw_local(items)
        vs.EndSym()
        opened = False
        sd = named(name, (SYMDEF,))
        if sd is None:
            raise RuntimeError("Vectorworks did not create the symbol %r" % name)
        if default_class:
            vs.SetSymbolOptionsN(name, 0, 4, default_class)  # centre line, no wall break
        if description:
            try:
                vs.SetDescriptionText(sd, description)
            except Exception:  # noqa: BLE001  (description is cosmetic)
                pass
        return sd
    except BaseException:
        if opened:
            try:
                vs.EndSym()
            except Exception:  # noqa: BLE001
                pass
        sd = named(name, (SYMDEF,))
        if sd is not None:
            vs.DelObject(sd)
        raise


def _place_one(symbol, pl):
    p = (float(pl["position"][0]), float(pl["position"][1]))
    before = vs.LNewObj()
    vs.Symbol(symbol, p, float(pl.get("rotation", 0.0)))
    h = vs.LNewObj()
    if _is_nil(h) or vs.GetTypeN(h) != SYMBOL or (not _is_nil(before) and str(h) == str(before)):
        raise RuntimeError("Vectorworks did not place symbol %r" % symbol)
    return h


def _group_one(items, pl):
    before = vs.LNewObj()
    vs.BeginGroup()
    ok = False
    try:
        _draw_local(items)
        ok = True
    finally:
        vs.EndGroup()
        if not ok:  # remove the half-built group
            g = vs.LNewObj()
            if not _is_nil(g) and vs.GetTypeN(g) == 11 and (_is_nil(before) or str(g) != str(before)):
                vs.DelObject(g)
    g = vs.LNewObj()
    if _is_nil(g) or vs.GetTypeN(g) != 11:
        raise RuntimeError("Vectorworks did not create the group")
    local = [read_obj(k, True, None, False) for k in children(g)]
    x, y = float(pl["position"][0]), float(pl["position"][1])
    if x or y:
        vs.HMove(g, x, y)
    rot = float(pl.get("rotation", 0.0))
    if rot:
        vs.HRotate(g, (x, y), rot)
    return g, local


def place_asset(symbol, items, placements, folder=None, layer=None, cls=None, as_symbol=True,
                new_classes=None, description=None):
    """Asset as symbol (definition reused if it exists) or as groups.

    items: normalized items at local coordinates, each with its class.
    new_classes: {name: attributes} for classes to create if missing (existing
    classes are never changed). Returns the definition's components as read
    back (for the exactness check) and the placed instances."""
    created_classes = []
    for name, attrs in (new_classes or {}).items():
        if name not in class_names():
            ensure_class(name, attrs)
            created_classes.append(name)
    if cls and cls not in class_names():
        ensure_class(cls)
        created_classes.append(cls)
    out = {"classes_created": created_classes, "folder_created": False, "symbol_created": False}
    state = _begin(layer)
    made = []
    try:
        if as_symbol:
            sd = named(symbol, (SYMDEF,))
            if sd is None:
                if named(symbol) is not None:
                    raise ValueError("the name %r is already used by another object, class or layer"
                                     % symbol)
                fh = None
                if folder:
                    fh, out["folder_created"] = _symbol_folder(folder)
                sd = _define_symbol(symbol, items, cls, description)
                out["symbol_created"] = True
                if fh is not None:
                    vs.InsertSymbolInFolder(fh, sd)
            out["symbol"] = {"name": symbol, "uuid": h_uuid(sd)}
            out["definition"] = [read_obj(k, True, None, False) for k in children(sd)]
            if cls:
                vs.NameClass(cls)
            for pl in placements:
                h = _place_one(symbol, pl)
                made.append(h)
                if cls:
                    vs.SetClass(h, cls)
        else:
            out["definition"] = None
            for pl in placements:
                g, local = _group_one(items, pl)
                made.append(g)
                if out["definition"] is None:
                    out["definition"] = local
                if cls:
                    vs.SetClass(g, cls)
    except BaseException:
        for h in made:
            vs.DelObject(h)
        if out.get("symbol_created"):
            sd = named(symbol, (SYMDEF,))
            if sd is not None:
                vs.DelObject(sd)
        if out.get("folder_created"):
            fh = named(folder, (SYMFOLDER,))
            if fh is not None and _is_nil(vs.FInFolder(fh)):
                vs.DelObject(fh)
        raise
    finally:
        _end(state)
    out["instances"] = [read_obj(h, True, None, False) for h in made]
    out["document"] = current_document()
    return out


def _contains_instance(h, name, depth=0):
    """Does this container (symbol definition, group) hold an instance of name?"""
    for k in children(h):
        t = vs.GetTypeN(k)
        if t == SYMBOL and _str(vs.GetSymName(k)) == name:
            return True
        if t == 11 and depth < 20 and _contains_instance(k, name, depth + 1):
            return True
    return False


def _used_in_definitions(name):
    """Is the symbol placed inside another symbol definition? Count() doesn't
    see those (live, 2026 U7). Walks the symbol library incl. folders."""
    stack, n = [vs.FSymDef()], 0
    while stack and n < 100000:
        h = stack.pop()
        while not _is_nil(h) and n < 100000:
            n += 1
            t = vs.GetTypeN(h)
            if t == SYMFOLDER:
                stack.append(vs.FInFolder(h))
            elif t == SYMDEF and _str(vs.GetName(h)) != name and _contains_instance(h, name):
                return True
            h = vs.NextObj(h)
    return False


def _symbol_instances(name):
    """Instances of a symbol anywhere (layers, groups, other definitions);
    -1 if unsure (then nothing is deleted)."""
    if "'" in name:
        return -1  # can't be quoted in a criterion
    try:
        n = int(vs.Count("(S='%s')" % name))
        if n == 0 and _used_in_definitions(name):
            n = 1
        return n
    except Exception:  # noqa: BLE001
        return -1


# ---- snapshots (exact revert) ------------------------------------------------------

BULK_LOOKUP = 64  # from this many uuids on, one walk beats GetObjectByUuid each


def _uuid_map(uuids):
    """{uuid: handle} for many objects in one walk over the document.
    GetObjectByUuid searches the drawing each time (measured: quadratic for
    thousands of objects). Missing uuids are left out (callers fall back)."""
    want = set(u for u in uuids if u)
    found = {}
    if len(want) < BULK_LOOKUP:
        return found

    def visit(h):
        u = vs.GetObjectUuid(h)
        if u in want:
            found[u] = h

    vs.ForEachObject(visit, "(ALL)")
    return found


def handles(uuids):
    """Handles for uuids (bulk when there are many); LookupError if one is missing."""
    m = _uuid_map(uuids)
    return [m.get(u) or handle(u) for u in uuids]


def _get_by_uuid(u, m):
    h = m.get(u)
    return h if h is not None else vs.GetObjectByUuid(u)


def _z_map(hs):
    """Stacking index (from the back) of each handle, one walk per parent
    list instead of one PrevObj walk per object."""
    out, done = {}, set()
    for h in hs:
        par = vs.GetParent(h)
        key = str(par)
        if key in done:
            continue
        done.add(key)
        k = vs.FInGroup(par) if (not _is_nil(par) and vs.GetTypeN(par) == 11) else None
        if _is_nil(k):
            # layer list: walk back from h to its first object, then forward
            k = h
            n = 0
            while n < 1000000:
                p = vs.PrevObj(k)
                if _is_nil(p):
                    break
                k = p
                n += 1
        i = 0
        while not _is_nil(k) and i < 1000000:
            out[str(k)] = i
            i += 1
            k = vs.NextObj(k)
    return out


def _z_index(h):
    i, k = 0, vs.PrevObj(h)
    while not _is_nil(k) and i < 100000:
        i += 1
        k = vs.PrevObj(k)
    return i


def snapshot(h, z=None):
    t = vs.GetTypeN(h)
    if t not in EDITABLE:
        raise NotEditable("%s objects can't be changed by these tools (uuid %s)"
                          % (KINDS.get(t, "type %d" % t), h_uuid(h)))
    par = vs.GetParent(h)
    nxt = vs.NextObj(h)
    snap = {"rec": read_obj(h, True, None, False), "z": _z_index(h) if z is None else z,
            "parent": h_uuid(par) if (not _is_nil(par) and vs.GetTypeN(par) == 11) else None,
            "above": None if _is_nil(nxt) else h_uuid(nxt)}
    if t in SHAPE_TYPES or t in (10, 17):
        snap["raw"] = raw_attrs(h)
    if t == 11:
        snap["children"] = [snapshot(k) for k in children(h)]
    return snap


def _place_below(h, above_uuid, z=None, hmap=None):
    """Move a new object to its old stacking position: directly behind
    `above` (the object that was in front of it). Lists run from the back
    (first object) to the front (last, where new objects land);
    HMoveBackward moves one step towards the first. With the old index z
    (from the back), an object near the back is sent to the back first and
    moved forward - fewer steps in big layers."""
    if not above_uuid:
        return
    above = _get_by_uuid(above_uuid, hmap or {})
    if _is_nil(above) or str(vs.GetParent(above)) != str(vs.GetParent(h)):
        return
    par = vs.GetParent(h)
    count = vs.NumObj(par) if (not _is_nil(par) and vs.GetTypeN(par) == 31) else None
    n = 0
    if z is not None and count and z < count / 2:
        vs.HMoveBackward(h, True)
        while n < 1000000:
            nxt = vs.NextObj(h)
            if _is_nil(nxt) or str(nxt) == str(above):
                return
            vs.HMoveForward(h, False)
            n += 1
        return
    while n < 1000000:
        nxt = vs.NextObj(h)
        if not _is_nil(nxt) and str(nxt) == str(above):
            return
        if _is_nil(vs.PrevObj(h)):
            return  # at the back: `above` is not behind us
        vs.HMoveBackward(h, False)
        n += 1

def _rebuild(rec):
    k = rec["kind"]
    if k == "line":
        vs.MoveTo(_pt(rec["p1"]))
        vs.LineTo(_pt(rec["p2"]))
    elif k == "rect":
        w, hh, c, a = rec["width"], rec["height"], rec["center"], rec["angle"]
        if abs(a) < 1e-12:
            x = [p[0] for p in rec["corners"]]
            y = [p[1] for p in rec["corners"]]
            vs.Rect((min(x), max(y)), (max(x), min(y)))
        else:
            vs.Rect((c[0] - w / 2.0, c[1] + hh / 2.0), (c[0] + w / 2.0, c[1] - hh / 2.0))
            vs.HRotate(vs.LNewObj(), (c[0], c[1]), a)
    elif k == "oval":
        w, hh, c, a = rec["width"], rec["height"], rec["center"], rec["angle"]
        vs.Oval((c[0] - w / 2.0, c[1] + hh / 2.0), (c[0] + w / 2.0, c[1] - hh / 2.0))
        if abs(a) > 1e-12:
            vs.HRotate(vs.LNewObj(), (c[0], c[1]), a)
    elif k in ("polygon", "polyline"):
        return add_holes(make_poly(rec["vertices"], rec["closed"]), rec.get("holes"))
    elif k == "arc":
        c = rec["center"]
        vs.ArcByCenter(c[0], c[1], rec["radius"], rec["start_angle"], rec["sweep"])
    elif k == "text":
        just = dict((v, k2) for k2, v in TEXT_JUST.items()).get(rec["just"], "left")
        val = dict((v, k2) for k2, v in TEXT_VALIGN.items()).get(rec["valign"], "baseline")
        return _make_text({"text": rec["text"], "position": rec["origin"], "size": rec["size"],
                           "font": rec["font"], "align": just, "valign": val,
                           "angle": rec["angle"]})
    elif k == "dimension":
        vs.LinearDim(_pt(rec["p1"]), _pt(rec["p2"]), 0.0, 4, 0, 0, 0)
    elif k == "locus":
        vs.Locus(_pt(rec["point"]))
    elif k == "symbol":
        vs.Symbol(rec["symbol"], _pt(rec["location"]), rec["rotation"])
    else:
        raise NotEditable("can't restore %s" % k)
    return vs.LNewObj()


BULK_RESTACK = 32  # from this many objects in one list on, restack the list in one pass


def _list_of(h):
    """All objects of h's list (layer or group), back to front."""
    k, n = h, 0
    while n < 1000000:
        p = vs.PrevObj(k)
        if _is_nil(p):
            break
        k = p
        n += 1
    out = []
    while not _is_nil(k) and len(out) < 1000000:
        out.append(k)
        k = vs.NextObj(k)
    return out


def _restack(entries, hmap=None, mapping=None, restored=None):
    """Put many objects back to their old stacking positions at once.
    entries: [(handle, above_uuid, z)] - each object goes directly behind the
    object that was in front of it (`above`, possibly also in entries). Per
    list: work out the final order, then move every object from the first
    changed position on to the front, in that order (one move per object
    instead of stepping each object through the list)."""
    mapping, hmap, restored = mapping or {}, hmap or {}, restored or {}
    by_list = {}
    for h, above, z in entries:
        by_list.setdefault(str(vs.GetParent(h)), []).append((h, above, z))
    for items in by_list.values():
        lst = _list_of(items[0][0])
        moving = dict((str(h), (h, above, z)) for h, above, z in items)
        base = [k for k in lst if str(k) not in moving]
        in_list = set(str(k) for k in lst)
        before = {}  # str(above handle) -> objects that go directly behind it
        tail = []
        for key, (h, above, z) in moving.items():
            ah = None
            if above:
                u = mapping.get(above, above)
                ah = restored.get(u)
                if ah is None:
                    ah = _get_by_uuid(u, hmap)
            if ah is not None and not _is_nil(ah) and str(ah) in in_list and str(ah) != key:
                before.setdefault(str(ah), []).append((h, z))
            else:
                tail.append((h, z))
        final, seen = [], set()

        def emit(k, depth=0):
            if str(k) in seen or depth > 100000:
                return
            seen.add(str(k))
            for h2, _z in sorted(before.get(str(k), []), key=lambda e: e[1] if e[1] is not None else 0):
                emit(h2, depth + 1)
            final.append(k)

        for k in base:
            emit(k)
        for h2, _z in sorted(tail, key=lambda e: e[1] if e[1] is not None else 0):
            emit(h2)
        for h2, _a, _z in items:  # anything left over (cycles): keep it, in front
            emit(h2)
        cur = _list_of(items[0][0])
        i = 0
        while i < len(final) and i < len(cur) and str(final[i]) == str(cur[i]):
            i += 1
        for k in final[i:]:
            vs.HMoveForward(k, True)


def restore(snap, mapping, hmap=None, place=True):
    """Recreate an object from its snapshot. Returns the new handle.
    place=False leaves it in front (the caller restacks many at once)."""
    rec = snap["rec"]
    state = _begin(rec["layer"], create_layer=True)
    try:
        vs.NameClass(rec["class"])
        if rec["kind"] == "group":
            kids = [restore(c, mapping, hmap) for c in sorted(snap.get("children", []), key=lambda s: s["z"])]
            vs.DSelectAll()
            for k in kids:
                vs.SetSelect(k)
            vs.Group()
            h = vs.LNewObj()
        else:
            h = _rebuild(rec)
    finally:
        _end(state)
    if _is_nil(h):
        raise RuntimeError("could not restore %s %s" % (rec["kind"], rec["uuid"]))
    vs.SetClass(h, rec["class"])
    if snap.get("raw"):
        restore_raw_attrs(h, snap["raw"])
    par = snap.get("parent")
    if par:
        ph = vs.GetObjectByUuid(mapping.get(par, par))
        if not _is_nil(ph):
            vs.SetParent(h, ph)
    above = snap.get("above")
    if place:
        _place_below(h, mapping.get(above, above) if above else None, snap.get("z"), hmap)
    if rec.get("name"):
        try:
            _set_name(h, rec["name"])
        except ValueError:
            pass
    mapping[rec["uuid"]] = h_uuid(h)
    return h


def revert2d(ops):
    """Undo journaled ops (newest last). Returns uuid remapping for the
    journal plus counts."""
    mapping, deleted, restored, missing, kept, restack = {}, 0, 0, [], [], []
    home = _str(vs.GetLName(vs.ActLayer()))
    replaced = [op for op in ops if op.get("op") == "replaced"]  # ("attrs" ops keep a dict in "before")
    hmap = _uuid_map([op.get("uuid") for op in ops] + [u for op in replaced for u in op.get("after") or []]
                     + [s.get("above") for op in replaced for s in op.get("before") or []]
                     + [op.get("above") for op in ops])
    for op in reversed(ops):
        kind = op["op"]
        if kind == "created":
            h = _get_by_uuid(op["uuid"], hmap)
            if _is_nil(h):
                missing.append(op["uuid"])
            else:
                hmap.pop(op["uuid"], None)
                vs.DelObject(h)
                deleted += 1
        elif kind == "replaced":
            for u in op.get("after", []):
                h = _get_by_uuid(u, hmap)
                if not _is_nil(h):
                    hmap.pop(u, None)
                    vs.DelObject(h)
                    deleted += 1
            befores = sorted(op.get("before", []), key=lambda s: -s["z"])
            if len(befores) >= BULK_RESTACK:
                placed = []
                for s in befores:
                    _use_layer(s["rec"].get("layer"))
                    h = restore(s, mapping, hmap, place=False)
                    placed.append((h, s.get("above"), s.get("z")))
                    restored += 1
                new_by_uuid = dict((h_uuid(h), h) for h, _a, _z in placed)
                _restack(placed, hmap, mapping, new_by_uuid)
            else:
                for s in befores:
                    restore(s, mapping, hmap)
                    restored += 1
        elif kind == "attrs":
            h = _get_by_uuid(op["uuid"], hmap)
            if _is_nil(h):
                missing.append(op["uuid"])
            else:
                if op.get("before"):
                    restore_raw_attrs(h, op["before"])
                if op.get("class_before") is not None:
                    vs.SetClass(h, op["class_before"])
                if op.get("layer_before"):
                    lay = vs.GetLayerByName(op["layer_before"])
                    if not _is_nil(lay):
                        vs.SetParent(h, lay)
                        if "above" in op:
                            restack.append((op, h))
                restored += 1
        elif kind == "symdef":
            # only if nothing uses it any more (the user may have placed more)
            sd = named(op["name"], (SYMDEF,))
            if sd is None:
                missing.append(op["name"])
            elif _symbol_instances(op["name"]) == 0:
                vs.DelObject(sd)
                deleted += 1
            else:
                kept.append(op["name"])
        elif kind == "symfolder":
            fh = named(op["name"], (SYMFOLDER,))
            if fh is not None and _is_nil(vs.FInFolder(fh)):
                vs.DelObject(fh)
                deleted += 1
        elif kind == "fields":
            h = _get_by_uuid(op["uuid"], hmap)
            if _is_nil(h):
                missing.append(op["uuid"])
            else:
                _write_fields(h, op["record"], op["before"])
                restored += 1
    # objects moved back to their layer: old stacking position, frontmost first
    # (so an object's `above` neighbour is already back in place)
    _use_layer(home)
    if len(restack) >= BULK_RESTACK:
        _restack([(h, op.get("above"), op.get("z")) for op, h in restack], hmap)
    else:
        for op, h in sorted(restack, key=lambda x: -(x[0].get("z") or 0)):
            _place_below(h, op.get("above"), op.get("z"), hmap)
    out = {"document": current_document(), "deleted": deleted, "restored": restored,
           "missing": missing, "mapping": mapping}
    if kept:
        out["kept_in_use"] = kept
    return out


# ---- editing ---------------------------------------------------------------------

def _snaps(uuids, hs=None):
    hs = handles(uuids) if hs is None else hs
    if len(hs) < BULK_LOOKUP:
        return [snapshot(h) for h in hs]
    zm = _z_map(hs)
    return [snapshot(h, zm.get(str(h))) for h in hs]


def set_attributes(uuids, attributes, layer=None):
    hs = handles(uuids)
    ops = []
    for h in hs:
        t = vs.GetTypeN(h)
        op = {"op": "attrs", "uuid": h_uuid(h), "class_before": _str(vs.GetClass(h)),
              "layer_before": _str(vs.GetLName(vs.GetLayer(h))) if layer else None}
        if t in SHAPE_TYPES or t in (10, 17, 63, 11, 15):
            op["before"] = raw_attrs(h)
        ops.append(op)
    target = None
    if layer:
        ensure_layer(layer)
        target = vs.GetLayerByName(layer)
    for h in hs:
        apply_attrs(h, attributes)
        if target is not None:
            par = vs.GetParent(h)
            if not _is_nil(par) and vs.GetTypeN(par) == 11:
                raise ValueError("object %s is inside a group; move the group instead" % h_uuid(h))
            vs.SetParent(h, target)
    return {"document": current_document(), "ops": ops,
            "objects": [read_obj(h, False) for h in hs]}


def organize_apply(moves):
    """Put objects into classes / onto layers (vw2d_organize), one job, one
    journal entry. moves: [{"uuids", "class", "layer", "by_class"}]. Layer
    moves journal the old stacking position. On any error everything done so
    far is undone."""
    applied = []
    hmap = _uuid_map([u for m in moves for u in m["uuids"]])
    movers = [hmap.get(u) or handle(u) for m in moves if m.get("layer") for u in m["uuids"]]
    zm = _z_map(movers) if movers else {}
    have = set(class_names())
    try:
        for m in moves:
            cls = m.get("class")
            if cls and cls not in have:
                ensure_class(cls)
                have.add(cls)
            target = None
            if m.get("layer"):
                ensure_layer(m["layer"])
                target = vs.GetLayerByName(m["layer"])
            for u in m["uuids"]:
                h = hmap.get(u) or handle(u)
                op = {"op": "attrs", "uuid": u, "class_before": _str(vs.GetClass(h)),
                      "layer_before": _layer_name(h) if target is not None else None}
                t = vs.GetTypeN(h)
                if m.get("by_class") and (t in SHAPE_TYPES or t in (10, 17, 63, 11, 15)):
                    op["before"] = raw_attrs(h)
                if target is not None:
                    par = vs.GetParent(h)
                    if not _is_nil(par) and vs.GetTypeN(par) == 11:
                        raise ValueError("object %s is inside a group; move the group instead" % u)
                    nxt = vs.NextObj(h)
                    op["above"] = None if _is_nil(nxt) else h_uuid(nxt)
                    op["z"] = zm.get(str(h))
                applied.append(op)
                if cls:
                    vs.SetClass(h, cls)
                if m.get("by_class"):
                    apply_attrs(h, {"by_class": True})
                if target is not None:
                    vs.SetParent(h, target)
    except BaseException:
        try:
            revert2d(applied)
        except Exception:  # noqa: BLE001 - then one by one, so one bad object can't stop the rest
            for op in reversed(applied):
                try:
                    revert2d([op])
                except Exception:  # noqa: BLE001
                    pass
        raise
    return {"document": current_document(), "ops": applied, "changed": len(applied)}


def delete_objects(uuids):
    hs = handles(uuids)
    snaps = _snaps(uuids, hs)
    for h in hs:
        vs.DelObject(h)
    return {"document": current_document(), "ops": [{"op": "replaced", "before": snaps, "after": []}]}


def replace_geometry(changes):
    """changes: [{uuid, verts, closed, holes}] -> rewrite polygons/polylines
    exactly. Same vertex count and no holes: edited in place (uuid kept);
    otherwise rebuilt with identical attributes, class, name, group and
    stacking position."""
    snaps, after, out = [], [], []
    hmap = _uuid_map([ch["uuid"] for ch in changes])
    zm = _z_map(list(hmap.values())) if hmap else {}
    home = _str(vs.GetLName(vs.ActLayer()))
    try:
        return _replace_all(changes, hmap, zm, snaps, after, out)
    finally:
        _use_layer(home)


def _replace_all(changes, hmap, zm, snaps, after, out):
    for ch in changes:
        h = hmap.get(ch["uuid"]) or handle(ch["uuid"])
        t = vs.GetTypeN(h)
        snaps.append(snapshot(h, zm.get(str(h))))
        holes = ch.get("holes") or []
        verts = ch["verts"]
        inplace = (t in (5, 21) and vs.GetVertNum(h) == len(verts) and not holes
                   and not _holes(h) and all(int(v[2]) in (0, 3) for v in verts)
                   and (t == 21 or all(int(v[2]) == 0 for v in verts)))
        if inplace:
            n = len(verts)
            for i, v in enumerate(verts):
                vs.SetPolylineVertex(h, i + 1, (float(v[0]), float(v[1])), int(v[2]),
                                     float(v[3]) if len(v) > 3 else 0.0, i == n - 1)
            if bool(vs.IsPolyClosed(h)) != bool(ch["closed"]):
                vs.SetPolyClosed(h, bool(ch["closed"]))
            vs.ResetObject(h)
        else:
            hmap.pop(ch["uuid"], None)  # the old object goes away
            _use_layer(_layer_name(h))
            h = _replace_with_poly(h, verts, ch["closed"], holes, hmap)
        after.append(h_uuid(h))
        out.append(read_obj(h, True, None, False))
    return {"document": current_document(), "objects": out,
            "ops": [{"op": "replaced", "before": snaps, "after": after}]}


def _replace_with_poly(old, verts, closed, holes, hmap=None):
    raw = raw_attrs(old) if vs.GetTypeN(old) in SHAPE_TYPES else None
    name = obj_name(old)
    cls = _str(vs.GetClass(old))
    par = vs.GetParent(old)
    nxt = vs.NextObj(old)
    above = None if _is_nil(nxt) else h_uuid(nxt)
    state = _begin(_str(vs.GetLName(vs.GetLayer(old))))
    try:
        vs.NameClass(cls)
        h = add_holes(make_poly(verts, closed), holes)
    finally:
        _end(state)
    vs.SetClass(h, cls)
    if raw:
        restore_raw_attrs(h, raw)
    if not _is_nil(par) and vs.GetTypeN(par) == 11:
        vs.SetParent(h, par)
    _place_below(h, above, None, hmap)
    vs.DelObject(old)
    if name:
        _set_name(h, name)
    return h


def set_line_points(changes):
    """changes: [{uuid, p1, p2}] for line objects (edited in place)."""
    snaps, after, out = [], [], []
    hmap = _uuid_map([ch["uuid"] for ch in changes])
    zm = _z_map(list(hmap.values())) if hmap else {}
    for ch in changes:
        h = hmap.get(ch["uuid"]) or handle(ch["uuid"])
        if vs.GetTypeN(h) != 2:
            raise NotEditable("%s is not a line" % ch["uuid"])
        snaps.append(snapshot(h, zm.get(str(h))))
        vs.SetSegPt1(h, _pt(ch["p1"]))
        vs.SetSegPt2(h, _pt(ch["p2"]))
        vs.ResetObject(h)
        after.append(h_uuid(h))
        out.append(read_obj(h, True, None, False))
    return {"document": current_document(), "objects": out,
            "ops": [{"op": "replaced", "before": snaps, "after": after}]}


def insert_vertices(inserts):
    """inserts: [{uuid, before (1-based index), x, y}] (processed per object
    from the highest index down so indices stay valid)."""
    by = {}
    for ins in inserts:
        by.setdefault(ins["uuid"], []).append(ins)
    snaps, after = [], []
    hmap = _uuid_map(list(by))
    zm = _z_map(list(hmap.values())) if hmap else {}
    for u, lst in by.items():
        h = hmap.get(u) or handle(u)
        if vs.GetTypeN(h) not in (5, 21):
            raise NotEditable("vertices can only be inserted into polygons/polylines")
        snaps.append(snapshot(h, zm.get(str(h))))
        for ins in sorted(lst, key=lambda d: -d["before"]):
            vs.InsertVertex(h, float(ins["x"]), float(ins["y"]), int(ins["before"]), 0, 0.0)
        vs.ResetObject(h)
        after.append(h_uuid(h))
    return {"document": current_document(),
            "ops": [{"op": "replaced", "before": snaps, "after": after}]}


def transform(uuids, op, params, copy=False):
    hs = [handle(u) for u in uuids]
    snaps = [] if copy else [snapshot(h) for h in hs]
    out = []
    for h in hs:
        if copy:
            h = vs.CreateDuplicateObject(h, vs.GetParent(h))
        if op == "move":
            vs.HMove(h, float(params["dx"]), float(params["dy"]))
        elif op == "rotate":
            vs.HRotate(h, _pt(params["center"]), float(params["angle"]))
        elif op == "mirror":
            m = vs.MirrorN(h, False, _pt(params["p1"]), _pt(params["p2"]), True)
            if not _is_nil(m):
                h = m
        elif op == "scale":
            c = params["center"]
            vs.HScale2D(h, float(c[0]), float(c[1]), float(params["sx"]), float(params["sy"]),
                        bool(params.get("scale_text", True)))
        else:
            raise ValueError("unknown transform %r" % op)
        vs.ResetObject(h)
        out.append(h)
    uu = [h_uuid(h) for h in out]
    ops = [{"op": "created", "uuid": u} for u in uu] if copy else \
        [{"op": "replaced", "before": snaps, "after": uu}]
    return {"document": current_document(), "ops": ops,
            "objects": [read_obj(h, True, None, False) for h in out]}


def _coord_values(hs):
    xs, ys = set(), set()
    for h in hs:
        t = vs.GetTypeN(h)
        pts = []
        if t in (5, 21):
            pts = [v[:2] for v in _verts(h)]
            for hv in _holes(h):
                pts += [v[:2] for v in hv]
        elif t == 3:
            pts = [vs.Get2DPt(h, i) for i in range(1, 5)]
        elif t == 6:
            pts = [vs.Get2DPt(h, i) for i in range(1, 4)]
        for p in pts:
            xs.add(p[0])
            ys.add(p[1])
    return sorted(xs), sorted(ys)


def _snapval(v, vals, eps):
    """Nearest value in sorted vals within eps, else v unchanged."""
    import bisect
    i = bisect.bisect_left(vals, v)
    best, bd = v, None
    for j in (i - 1, i):
        if 0 <= j < len(vals):
            d = abs(vals[j] - v)
            if d <= eps and (bd is None or d < bd):
                best, bd = vals[j], d
    return best


def _clean_noise(h, xs, ys, eps):
    """Snap floating-point noise from Parasolid booleans back onto the exact
    input coordinates (|delta| <= eps). Returns the number of vertices fixed."""
    if vs.GetTypeN(h) not in (5, 21):
        return 0
    fixed = 0
    n = vs.GetVertNum(h)
    for i in range(1, n + 1):
        p, t, r = vs.GetPolylineVertex(h, i)
        nx, ny = _snapval(p[0], xs, eps), _snapval(p[1], ys, eps)
        if nx != p[0] or ny != p[1]:
            vs.SetPolylineVertex(h, i, (nx, ny), t, r, i == n)
            fixed += 1
    return fixed


def _results(h):
    """A boolean result may be one object or a group of objects."""
    if _is_nil(h):
        return []
    if vs.GetTypeN(h) == 11:
        return children(h)
    return [h]


def boolean(operation, targets, tools=None, keep_tools=True, line=None, eps=1e-9):
    """union / subtract / intersect / split (by a line through two points).
    Results keep the attributes of the (first) target."""
    tools = tools or []
    ths = [handle(u) for u in targets]
    tlh = [handle(u) for u in tools]
    xs, ys = _coord_values(ths + tlh)
    snaps = [snapshot(h) for h in ths]
    if tools and not keep_tools:
        snaps += [snapshot(h) for h in tlh]
    results = []
    if operation == "union":
        allh = ths + tlh
        acc = allh[0]
        for h in allh[1:]:
            nh = vs.AddSurface(acc, h)
            if _is_nil(nh):
                raise RuntimeError("union failed: do the shapes touch or overlap?")
            acc = nh
        results = _results(acc)
    elif operation == "subtract":
        for t in ths:
            cur = [t]
            for tool in tlh:
                nxt = []
                for c in cur:
                    nh = vs.ClipSurfaceN(c, tool)
                    nxt += _results(nh) if not _is_nil(nh) else [c]
                cur = nxt
            results += cur
        if not keep_tools:
            for tool in tlh:
                vs.DelObject(tool)
    elif operation == "intersect":
        for t in ths:
            for tool in tlh:
                results += _results(vs.IntersectSurface(t, tool))
        if not results:
            raise RuntimeError("the objects do not intersect; nothing was changed")
        for t in ths:
            vs.DelObject(t)
        if not keep_tools:
            for tool in tlh:
                vs.DelObject(tool)
    elif operation == "split":
        (ax, ay), (bx, by) = line
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy)
        if L == 0:
            raise ValueError("split line needs two different points")
        for t in ths:
            x1, y1, x2, y2 = _bbox(t)
            big = 4.0 * (math.hypot(x2 - x1, y2 - y1) + math.hypot(ax - x1, ay - y1) + L)
            ux, uy = dx / L, dy / L
            nx_, ny_ = -uy, ux
            p0 = (ax - ux * big, ay - uy * big)
            p1 = (ax + ux * big, ay + uy * big)
            half = [[p0[0], p0[1], 0, 0.0], [p1[0], p1[1], 0, 0.0],
                    [p1[0] + nx_ * big, p1[1] + ny_ * big, 0, 0.0],
                    [p0[0] + nx_ * big, p0[1] + ny_ * big, 0, 0.0]]
            state = _begin(_str(vs.GetLName(vs.GetLayer(t))))
            try:
                hp = make_poly(half, True)
            finally:
                _end(state)
            left = _results(vs.IntersectSurface(t, hp))
            right = _results(vs.ClipSurfaceN(t, hp))
            vs.DelObject(hp)
            results += left + right
    else:
        raise ValueError("unknown boolean operation %r" % operation)
    fixed = sum(_clean_noise(h, xs, ys, eps) for h in results)
    for h in results:
        vs.ResetObject(h)
    after = [h_uuid(h) for h in results]
    return {"document": current_document(), "noise_fixed": fixed,
            "objects": [read_obj(h, True, None, False) for h in results],
            "ops": [{"op": "replaced", "before": snaps, "after": after}]}


def subtract_many(pairs, eps=1e-9):
    """pairs: [{target, tools, covered}] processed in order; tools stay.
    covered=True: the target lies completely inside its tools and is deleted."""
    snaps, after, fixed = [], [], 0
    hmap = _uuid_map([pr["target"] for pr in pairs] + [u for pr in pairs for u in pr["tools"]])
    zm = _z_map([hmap[pr["target"]] for pr in pairs if pr["target"] in hmap]) if hmap else {}
    for pr in pairs:
        # a target is replaced by its clipped result: never use its old handle again
        t = hmap.pop(pr["target"], None) or handle(pr["target"])
        snaps.append(snapshot(t, zm.get(str(t))))
        if pr.get("covered"):
            vs.DelObject(t)
            continue
        tools = [hmap.get(u) or handle(u) for u in pr["tools"]]
        xs, ys = _coord_values([t] + tools)
        cur = [t]
        for tool in tools:
            nxt = []
            for c in cur:
                nh = vs.ClipSurfaceN(c, tool)
                nxt += _results(nh) if not _is_nil(nh) else [c]
            cur = nxt
        for h in cur:
            fixed += _clean_noise(h, xs, ys, eps)
            vs.ResetObject(h)
            after.append(h_uuid(h))
    return {"document": current_document(), "noise_fixed": fixed, "created": after,
            "ops": [{"op": "replaced", "before": snaps, "after": after}]}


def offset(uuid, distance, smooth=False, replace=False):
    h = handle(uuid)
    snap = snapshot(h) if replace else None
    cls = _str(vs.GetClass(h))
    attrs = raw_attrs(h)
    name = _str(vs.GetName(h)) if replace else ""
    d = float(distance)
    xs, ys = _coord_values([h])
    # exact candidates: 2.615 - 0.01 is 2.605, not 2.6050000000000004
    from decimal import Decimal
    dd = Decimal(repr(d))
    xs = sorted(set(float(Decimal(repr(x)) + s * dd) for x in xs for s in (-1, 0, 1)))
    ys = sorted(set(float(Decimal(repr(y)) + s * dd) for y in ys for s in (-1, 0, 1)))
    # Vectorworks puts the result on the active layer: make the original's layer active
    par = vs.GetParent(h)
    lay = _str(vs.GetLName(par)) if not _is_nil(par) and vs.GetTypeN(par) == 31 else None
    state = _begin(lay, create_layer=False)
    try:
        nh = vs.OffsetPolyN(h, d, bool(smooth))
    finally:
        _end(state)
    if _is_nil(nh):
        raise RuntimeError("offset failed")
    res = _results(nh)
    eps = 1e-9 * max(1.0, abs(d))
    for x in res:
        _clean_noise(x, xs, ys, eps)
        # the result is a new object: same class and attributes as the original
        vs.SetClass(x, cls)
        restore_raw_attrs(x, attrs)
        vs.ResetObject(x)
    if replace:
        vs.DelObject(h)
        if name and len(res) == 1:
            vs.SetName(res[0], name)
        ops = [{"op": "replaced", "before": [snap], "after": [h_uuid(x) for x in res]}]
    else:
        ops = [{"op": "created", "uuid": h_uuid(x)} for x in res]
    return {"document": current_document(), "ops": ops,
            "objects": [read_obj(x, True, None, False) for x in res]}


def select_objects(uuids, zoom=True):
    vs.DSelectAll()
    hs = [handle(u) for u in uuids]
    for h in hs:
        vs.SetSelect(h)
    if zoom and hs:
        vs.DoMenuTextByName("Fit To Objects", 0)
    return {"selected": len(hs)}
