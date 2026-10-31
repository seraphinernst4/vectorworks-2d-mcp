# Helper library executed INSIDE Vectorworks (embedded Python 3.9).
#
# The server sends this file's source as a prelude with every tool job, so
# the installed bridge never has to change when tools do. Keep it 3.9-
# compatible: no match statements, no `X | Y` type unions, stdlib only.
# `vs` is injected by the bridge.

TYPE_NAMES = {
    2: "Line", 3: "Rectangle", 4: "Oval", 5: "Polygon", 6: "Arc", 10: "Text",
    11: "Group", 15: "Symbol", 16: "Symbol Definition", 21: "Polyline",
    31: "Layer", 86: "Plug-in Object", 92: "Symbol Folder",
}
TYPE_CODES = dict((v.lower(), k) for k, v in TYPE_NAMES.items())


def _str(x):
    return "" if x is None else str(x)


def _is_nil(h):
    return h is None or str(h) == "0"


def obj_name(h):
    # GetName returns "none" for unnamed objects. An object really named
    # "none" is the one GetObject finds (which may otherwise hit class None).
    nm = _str(vs.GetName(h))
    if nm == "none" and str(vs.GetObject(nm)) != str(h):
        return ""
    return nm


def h_uuid(h):
    return vs.GetObjectUuid(h)


def named(name, types=None):
    """Handle of the object/resource with this name, or None. Names are one
    namespace for objects, classes, layers and resources, so pass the GetTypeN
    values you expect (16 symbol definition, 94 class, 31 layer). Also treats
    the type-0 dummy some Vectorworks versions return for unknown names as
    missing."""
    h = vs.GetObject(name)
    if _is_nil(h):
        return None
    t = vs.GetTypeN(h)
    if t == 0 or (types and t not in types):
        return None
    return h


def children(h, limit=200000):
    """Direct children of a group (or other container), safely: FInGroup of
    an empty group can point into the parent's list, so every child's parent
    must be h; the walk is also bounded."""
    out = []
    k = vs.FInGroup(h)
    uid = vs.GetObjectUuid(h)
    while not _is_nil(k) and len(out) < limit:
        par = vs.GetParent(k)
        if _is_nil(par) or vs.GetObjectUuid(par) != uid:
            break
        out.append(k)
        k = vs.NextObj(k)
    return out


def handle(uuid):
    h = vs.GetObjectByUuid(uuid)
    if _is_nil(h):
        raise LookupError("no object with uuid %s" % uuid)
    return h


def pio_name(h):
    """Plug-in object name (e.g. 'Lighting Device'), or ''."""
    if vs.GetTypeN(h) != 86:
        return ""
    rec = vs.GetParametricRecord(h)
    return "" if _is_nil(rec) else _str(vs.GetName(rec))


def record_names(h):
    return [_str(vs.GetName(vs.GetRecord(h, i))) for i in range(1, vs.NumRecords(h) + 1)]


def obj_info(h):
    t = vs.GetTypeN(h)
    (x1, y1), (x2, y2) = vs.GetBBox(h)
    info = {
        "uuid": h_uuid(h),
        "type": TYPE_NAMES.get(t, "Type %d" % t),
        "type_code": t,
        "name": obj_name(h),
        "class": _str(vs.GetClass(h)),
        "layer": _str(vs.GetLName(vs.GetLayer(h))),
        "bbox": [[x1, y1], [x2, y2]],
        "records": record_names(h),
    }
    if t == 15:
        info["symbol"] = _str(vs.GetSymName(h))
    if t == 86:
        info["plugin"] = pio_name(h)
    return info


def all_objects(criteria="(ALL)"):
    found = []
    vs.ForEachObject(found.append, criteria)
    return found


def matches(h, cls=None, layer=None, type_=None, plugin=None, record=None,
            field=None, value=None, name=None):
    if cls is not None and _str(vs.GetClass(h)) != cls:
        return False
    if layer is not None and _str(vs.GetLName(vs.GetLayer(h))) != layer:
        return False
    if type_ is not None:
        want = TYPE_CODES.get(str(type_).lower(), type_)
        if vs.GetTypeN(h) != want:
            return False
    if plugin is not None and pio_name(h) != plugin:
        return False
    if name is not None and obj_name(h) != name:
        return False
    if record is not None:
        if record not in record_names(h) and pio_name(h) != record:
            return False
        if field is not None and value is not None:
            if _str(vs.GetRField(h, record, field)) != _str(value):
                return False
    return True


# ---- document -------------------------------------------------------------

def list_layers():
    out = []
    layer = vs.FLayer()
    while not _is_nil(layer):
        out.append({
            "name": _str(vs.GetLName(layer)),
            "kind": "sheet" if vs.GetObjectVariableInt(layer, 154) == 2 else "design",
            "visibility": vs.GetObjectVariableInt(layer, 153),
            "active": _str(vs.GetLName(layer)) == _str(vs.GetLName(vs.ActLayer())),
        })
        layer = vs.NextLayer(layer)
    return out


def list_classes(name_contains=None):
    names = [_str(vs.ClassList(i)) for i in range(1, vs.ClassNum() + 1)]
    if name_contains:
        needle = name_contains.lower()
        names = [n for n in names if needle in n.lower()]
    return names


def document_info():
    units = vs.GetUnits()
    return {
        "name": _str(vs.GetFName()),
        "path": _str(vs.GetFPathName()),
        "units": {"display_name": units[4].strip() or units[4], "area_name": units[5].strip()},
        "active_layer": _str(vs.GetLName(vs.ActLayer())),
        "active_class": _str(vs.ActiveClass()),
        "layer_count": vs.NumLayers(),
        "class_count": vs.ClassNum(),
        "selection_count": vs.NumSelectedObjects(),
    }


def get_selection(limit=500):
    out = []
    h = vs.FSActLayer()
    while not _is_nil(h) and len(out) < limit:
        out.append(obj_info(h))
        h = vs.NextSObj(h)
    return out


def find_objects(cls=None, layer=None, type_=None, plugin=None, record=None,
                 field=None, value=None, name=None, limit=500):
    out = []
    for h in all_objects():
        if matches(h, cls, layer, type_, plugin, record, field, value, name):
            out.append(obj_info(h))
            if len(out) >= limit:
                break
    return out


# ---- records ----------------------------------------------------------------

def _record_handle(h, record):
    if pio_name(h) == record:
        return vs.GetParametricRecord(h)
    for i in range(1, vs.NumRecords(h) + 1):
        rec = vs.GetRecord(h, i)
        if _str(vs.GetName(rec)) == record:
            return rec
    raise LookupError("object has no record %r (has %r)" % (record, record_names(h)))


def get_record(uuid, record):
    h = handle(uuid)
    rec = _record_handle(h, record)
    fields = {}
    for i in range(1, vs.NumFields(rec) + 1):
        fname = _str(vs.GetFldName(rec, i))
        fields[fname] = _str(vs.GetRField(h, record, fname))
    return fields


def set_record(uuid, record, values):
    h = handle(uuid)
    rec = _record_handle(h, record)
    known = set(_str(vs.GetFldName(rec, i)) for i in range(1, vs.NumFields(rec) + 1))
    unknown = [k for k in values if k not in known]
    if unknown:
        raise KeyError("unknown field(s) %r on record %r" % (unknown, record))
    before = dict((k, _str(vs.GetRField(h, record, k))) for k in values)
    _write_fields(h, record, values)
    return {"document": current_document(), "before": before}


def _write_fields(h, record, values):
    if record == LD:
        values = _address_fields(h, values)
    for k, v in values.items():
        vs.SetRField(h, record, k, _str(v))
    if vs.GetTypeN(h) == 86:
        vs.ResetObject(h)


def current_document():
    return _str(vs.GetFName())


def revert(ops):
    """Undo journaled tool calls: restore field values, delete created objects."""
    restored, deleted, missing = 0, 0, []
    for op in ops:
        h = vs.GetObjectByUuid(op["uuid"])
        if _is_nil(h):
            missing.append(op["uuid"])
            continue
        if op["op"] == "fields":
            _write_fields(h, op["record"], op["before"])
            restored += 1
        elif op["op"] == "created":
            vs.DelObject(h)
            deleted += 1
    return {"document": current_document(), "restored": restored, "deleted": deleted,
            "missing": missing}


# ---- symbols ------------------------------------------------------------------

def list_symbols(name_contains=None, folder=None, limit=1000):
    out = []

    def walk(h, path):
        while not _is_nil(h) and len(out) < limit:
            t = vs.GetTypeN(h)
            nm = _str(vs.GetName(h))
            if t == 92:
                walk(vs.FInFolder(h), path + [nm])
            elif t == 16:
                fpath = "/".join(path)
                if (not name_contains or name_contains.lower() in nm.lower()) and \
                        (not folder or folder.lower() in fpath.lower()):
                    out.append({"name": nm, "folder": fpath})
            h = vs.NextSymDef(h)

    walk(vs.FSymDef(), [])
    return out


def insert_symbol(name, x, y, z=None, layer=None, cls=None, rotation=0.0):
    if named(name, (16,)) is None:
        what = vs.GetTypeN(vs.GetObject(name)) if not _is_nil(vs.GetObject(name)) else None
        hint = " (that name belongs to a class)" if what == 94 else (
            " (that name belongs to a layer)" if what == 31 else "")
        raise LookupError("no symbol definition named %r%s" % (name, hint))
    if layer:
        if _is_nil(vs.GetLayerByName(layer)):
            raise LookupError("no layer named %r" % layer)
        vs.Layer(layer)
    before = vs.LNewObj()
    vs.Symbol(name, (x, y), rotation)
    h = vs.LNewObj()
    if _is_nil(h) or vs.GetTypeN(h) != 15 or (not _is_nil(before) and str(h) == str(before)):
        raise RuntimeError("Vectorworks did not place symbol %r" % name)
    if cls:
        vs.SetClass(h, cls)
    if z is not None:
        vs.Move3DObj(h, 0, 0, z - vs.GetSymLoc3D(h)[2])
    info = obj_info(h)
    info["document"] = current_document()
    return info


# ---- Spotlight ------------------------------------------------------------------

LD = "Lighting Device"
LD_FIELDS = [
    "Unit Number", "Channel", "Dimmer", "Universe", "DMX Address", "UniverseAddress",
    "Absolute Address", "Num Channels", "Purpose", "Position", "Inst Type", "Wattage",
    "Color", "Template", "Template2", "Focus", "Circuit Number", "Circuit Name",
    "Mark", "Symbol Name", "Fixture Mode", "GDTF Fixture Name", "Device Type",
    "User Field 1", "User Field 2", "User Field 3", "User Field 4", "User Field 5",
    "User Field 6",
]
# Plug-in name -> parameter holding the position's name.
POSITION_PLUGINS = {
    "Light Position Obj": "Position Name",
    "Lighting Pipe": "Name",
    "Lighting Pipe Ladder": "Name",
    "Hanging Position": "Position Name",
    "Truss": "Name",
}


def _natural(s):
    import re
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s or "")]


def lighting_devices():
    return all_objects("(PON='%s')" % LD)


def ld_row(h, fields):
    row = {"uuid": h_uuid(h), "layer": _str(vs.GetLName(vs.GetLayer(h))),
           "class": _str(vs.GetClass(h))}
    for f in fields:
        row[f] = _str(vs.GetRField(h, LD, f))
    return row


def ld_field_names():
    devs = lighting_devices()
    if not devs:
        return []
    rec = vs.GetParametricRecord(devs[0])
    return [_str(vs.GetFldName(rec, i)) for i in range(1, vs.NumFields(rec) + 1)]


def list_lighting_devices(fields=None, position=None, layer=None, limit=5000):
    fields = fields or LD_FIELDS
    keyed = []
    for h in lighting_devices():
        pos = _str(vs.GetRField(h, LD, "Position"))
        if position is not None and pos != position:
            continue
        if layer is not None and _str(vs.GetLName(vs.GetLayer(h))) != layer:
            continue
        unit = _str(vs.GetRField(h, LD, "Unit Number"))
        keyed.append(((_natural(pos), _natural(unit)), ld_row(h, fields)))
    keyed.sort(key=lambda kr: kr[0])
    return [row for _, row in keyed[:limit]]


def _resolve_devices(selectors):
    """Map each selector to exactly one device handle, or raise before any edit."""
    by_uuid, by_pos_unit, by_unit, by_chan = {}, {}, {}, {}
    for h in lighting_devices():
        u = h_uuid(h)
        pos = _str(vs.GetRField(h, LD, "Position"))
        unit = _str(vs.GetRField(h, LD, "Unit Number"))
        by_uuid[u] = h
        by_pos_unit.setdefault((pos, unit), []).append(h)
        by_unit.setdefault(unit, []).append(h)
        by_chan.setdefault(_str(vs.GetRField(h, LD, "Channel")), []).append(h)
    out, errors = [], []
    for i, sel in enumerate(selectors):
        if sel.get("uuid"):
            hs = [by_uuid[sel["uuid"]]] if sel["uuid"] in by_uuid else []
            label = "uuid %s" % sel["uuid"]
        elif sel.get("unit_number") is not None and sel.get("position") is not None:
            hs = by_pos_unit.get((sel["position"], str(sel["unit_number"])), [])
            label = "%s unit %s" % (sel["position"], sel["unit_number"])
        elif sel.get("unit_number") is not None:
            hs = by_unit.get(str(sel["unit_number"]), [])
            label = "unit %s (add position: unit numbers repeat per position)" % sel["unit_number"]
        elif sel.get("channel") is not None:
            hs = by_chan.get(str(sel["channel"]), [])
            label = "channel %s" % sel["channel"]
        else:
            errors.append("update %d: needs uuid, unit_number(+position) or channel" % i)
            continue
        if len(hs) != 1:
            errors.append("update %d: %s matches %d devices" % (i, label, len(hs)))
        else:
            out.append(hs[0])
    if errors:
        raise LookupError("; ".join(errors))
    return out


def _address_fields(h, fields):
    """Spotlight rebuilds Universe/DMX Address from UniverseAddress on reset, so
    route split-address edits through UniverseAddress."""
    fields = dict(fields)
    if ("Universe" in fields or "DMX Address" in fields) and "UniverseAddress" not in fields:
        uni = _str(fields.pop("Universe", vs.GetRField(h, LD, "Universe")))
        addr = _str(fields.pop("DMX Address", vs.GetRField(h, LD, "DMX Address")))
        fields["UniverseAddress"] = "%s/%s" % (uni, addr)
    return fields


def set_lighting_device_fields(updates):
    """updates: [{selector keys..., "fields": {name: value}}]. All-or-nothing:
    every selector and field name is validated before anything changes.
    Values are re-read by the server afterwards (resets apply at script end)."""
    handles = _resolve_devices(updates)
    known = set(ld_field_names())
    bad = sorted(set(k for u in updates for k in u["fields"]) - known)
    if bad:
        raise KeyError("unknown Lighting Device field(s): %r" % bad)
    done = []
    for h, u in zip(handles, updates):
        before = dict((k, _str(vs.GetRField(h, LD, k))) for k in u["fields"])
        _write_fields(h, LD, u["fields"])
        done.append({"uuid": h_uuid(h), "before": before})
    return {"document": current_document(), "devices": done}


def read_devices(uuids, fields):
    return [ld_row(handle(u), fields) for u in uuids]


def list_positions(include_devices=True):
    devices = {}
    for h in lighting_devices():
        devices.setdefault(_str(vs.GetRField(h, LD, "Position")), []).append(
            ld_row(h, ["Unit Number", "Channel", "Inst Type", "Purpose"]))
    positions, named = [], set()
    for h in all_objects("(T=86)"):
        plug = pio_name(h)
        if plug not in POSITION_PLUGINS:
            continue
        nm = _str(vs.GetRField(h, plug, POSITION_PLUGINS[plug]))
        named.add(nm)
        devs = sorted(devices.get(nm, []), key=lambda r: _natural(r["Unit Number"]))
        p = {"name": nm, "uuid": h_uuid(h), "kind": plug,
             "layer": _str(vs.GetLName(vs.GetLayer(h))), "device_count": len(devs) if nm else 0}
        if include_devices and nm:
            p["devices"] = devs
        positions.append(p)
    positions.sort(key=lambda p: _natural(p["name"]))
    orphans = dict((k, len(v)) for k, v in devices.items() if k and k not in named)
    return {"positions": positions,
            "unassigned_device_count": len(devices.get("", [])),
            "device_positions_without_object": orphans}
