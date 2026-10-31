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


def read_obj(h, geometry=True, parent=None, allow_temp=True, depth=0):
    t = vs.GetTypeN(h)
    rec = {"uuid": h_uuid(h), "kind": KINDS.get(t, "type%d" % t), "type_code": t,
           "name": obj_name(h), "class": _str(vs.GetClass(h)),
           "layer": _str(vs.GetLName(vs.GetLayer(h))), "bbox": _bbox(h)}
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
    for h in collect(uuids, layer, cls, selection, bbox):
        _walk(h, out, geometry, allow_temp, None, cls)
        if len(out) >= limit:
            break
    return {"document": current_document(), "units": units(), "none_class": none_class(),
            "active_layer": _str(vs.GetLName(vs.ActLayer())),
            "objects": out[:limit], "truncated": len(out) > limit}


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
    if layer:
        if _is_nil(vs.GetLayerByName(layer)) and not create_layer:
            vs.PopAttrs()
            raise LookupError("no layer %r" % layer)
        vs.Layer(layer)
    return state


def _end(state):
    vs.PopAttrs()
    vs.Layer(state[0])
    vs.NameClass(state[1])


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
    try:
        for it in items:
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
        raise
    recs = [read_obj(h, True, None, False) for h in made]
    return {"document": current_document(), "objects": recs}


# ---- snapshots (exact revert) ------------------------------------------------------

def _z_index(h):
    i, k = 0, vs.PrevObj(h)
    while not _is_nil(k) and i < 100000:
        i += 1
        k = vs.PrevObj(k)
    return i


def snapshot(h):
    t = vs.GetTypeN(h)
    if t not in EDITABLE:
        raise NotEditable("%s objects can't be changed by these tools (uuid %s)"
                          % (KINDS.get(t, "type %d" % t), h_uuid(h)))
    par = vs.GetParent(h)
    nxt = vs.NextObj(h)
    snap = {"rec": read_obj(h, True, None, False), "z": _z_index(h),
            "parent": h_uuid(par) if (not _is_nil(par) and vs.GetTypeN(par) == 11) else None,
            "above": None if _is_nil(nxt) else h_uuid(nxt)}
    if t in SHAPE_TYPES or t in (10, 17):
        snap["raw"] = raw_attrs(h)
    if t == 11:
        snap["children"] = [snapshot(k) for k in children(h)]
    return snap


def _place_below(h, above_uuid):
    if not above_uuid:
        return
    above = vs.GetObjectByUuid(above_uuid)
    if _is_nil(above) or str(vs.GetParent(above)) != str(vs.GetParent(h)):
        return
    n = 0
    while n < 50000:
        nxt = vs.NextObj(h)
        if _is_nil(nxt) or str(nxt) == str(above):
            return
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


def restore(snap, mapping):
    """Recreate an object from its snapshot. Returns the new handle."""
    rec = snap["rec"]
    state = _begin(rec["layer"], create_layer=True)
    try:
        vs.NameClass(rec["class"])
        if rec["kind"] == "group":
            kids = [restore(c, mapping) for c in sorted(snap.get("children", []), key=lambda s: s["z"])]
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
    _place_below(h, mapping.get(above, above) if above else None)
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
    mapping, deleted, restored, missing = {}, 0, 0, []
    for op in reversed(ops):
        kind = op["op"]
        if kind == "created":
            h = vs.GetObjectByUuid(op["uuid"])
            if _is_nil(h):
                missing.append(op["uuid"])
            else:
                vs.DelObject(h)
                deleted += 1
        elif kind == "replaced":
            for u in op.get("after", []):
                h = vs.GetObjectByUuid(u)
                if not _is_nil(h):
                    vs.DelObject(h)
                    deleted += 1
            for s in sorted(op.get("before", []), key=lambda s: -s["z"]):
                restore(s, mapping)
                restored += 1
        elif kind == "attrs":
            h = vs.GetObjectByUuid(op["uuid"])
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
                restored += 1
        elif kind == "fields":
            h = vs.GetObjectByUuid(op["uuid"])
            if _is_nil(h):
                missing.append(op["uuid"])
            else:
                _write_fields(h, op["record"], op["before"])
                restored += 1
    return {"document": current_document(), "deleted": deleted, "restored": restored,
            "missing": missing, "mapping": mapping}


# ---- editing ---------------------------------------------------------------------

def _snaps(uuids):
    return [snapshot(handle(u)) for u in uuids]


def set_attributes(uuids, attributes, layer=None):
    hs = [handle(u) for u in uuids]
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


def delete_objects(uuids):
    snaps = _snaps(uuids)
    for u in uuids:
        vs.DelObject(handle(u))
    return {"document": current_document(), "ops": [{"op": "replaced", "before": snaps, "after": []}]}


def replace_geometry(changes):
    """changes: [{uuid, verts, closed, holes}] -> rewrite polygons/polylines
    exactly. Same vertex count and no holes: edited in place (uuid kept);
    otherwise rebuilt with identical attributes, class, name, group and
    stacking position."""
    snaps, after, out = [], [], []
    for ch in changes:
        h = handle(ch["uuid"])
        t = vs.GetTypeN(h)
        snaps.append(snapshot(h))
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
            h = _replace_with_poly(h, verts, ch["closed"], holes)
        after.append(h_uuid(h))
        out.append(read_obj(h, True, None, False))
    return {"document": current_document(), "objects": out,
            "ops": [{"op": "replaced", "before": snaps, "after": after}]}


def _replace_with_poly(old, verts, closed, holes):
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
    _place_below(h, above)
    vs.DelObject(old)
    if name:
        _set_name(h, name)
    return h


def set_line_points(changes):
    """changes: [{uuid, p1, p2}] for line objects (edited in place)."""
    snaps, after, out = [], [], []
    for ch in changes:
        h = handle(ch["uuid"])
        if vs.GetTypeN(h) != 2:
            raise NotEditable("%s is not a line" % ch["uuid"])
        snaps.append(snapshot(h))
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
    for u, lst in by.items():
        h = handle(u)
        if vs.GetTypeN(h) not in (5, 21):
            raise NotEditable("vertices can only be inserted into polygons/polylines")
        snaps.append(snapshot(h))
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
    for pr in pairs:
        t = handle(pr["target"])
        snaps.append(snapshot(t))
        if pr.get("covered"):
            vs.DelObject(t)
            continue
        tools = [handle(u) for u in pr["tools"]]
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
