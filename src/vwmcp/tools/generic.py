"""Generic Vectorworks tools: session, document, objects, records, symbols."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from vwmcp import app, calls
from vwmcp.app import READ, WRITE, mcp, vw
from vwmcp.client import BridgeError
from vwmcp.journal import JournalError

PING_CODE = """
v = vs.GetVersionEx()
result = {"version": "%d.%d.%d" % tuple(v[:3]), "build": v[-1], "document": vs.GetFName()}
"""

LOOKUP_CODE = """
import json as _json
_q = _json.loads(%r)
_words = _q["query"].lower().split()
_hits = []
for _n in dir(vs):
    if _n.startswith("_"):
        continue
    _doc = getattr(getattr(vs, _n), "__doc__", None) or ""
    _hay = (_n + " " + _doc).lower()
    if all(w in _hay for w in _words):
        _score = sum(w in _n.lower() for w in _words)
        _hits.append((-_score, _n, _doc))
_hits.sort()
result = [{"name": n, "doc": d} for _, n, d in _hits[: _q["limit"]]]
"""


def _run_raw(code: str) -> dict:
    try:
        return app.get_bridge().run(code)
    except BridgeError as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=READ)
def vw_ping() -> dict:
    """Check the Vectorworks bridge: returns Vectorworks version, active document
    and bridge version. Warns if the installed bridge doesn't match this server."""
    res = _run_raw(PING_CODE)
    if res.get("ok") and res.get("bridge_version") != app.EXPECTED_BRIDGE_VERSION:
        # an old bridge still in memory: reload it now, then ask again
        bridge = app.get_bridge()
        calls.maybe_reload(bridge)
        if bridge.bridge_version == app.EXPECTED_BRIDGE_VERSION:
            res = _run_raw(PING_CODE)
    if not res["ok"]:
        raise ToolError(res["error"])
    out = {
        "vectorworks_version": res["value"]["version"],
        "build": res["value"]["build"],
        "document": res["value"]["document"],
        "bridge_version": res.get("bridge_version"),
    }
    warnings = []
    if out["bridge_version"] != app.EXPECTED_BRIDGE_VERSION:
        warnings.append(
            f"Installed bridge is {out['bridge_version']}, server expects "
            f"{app.EXPECTED_BRIDGE_VERSION}. Run `vwmcp install-plugin` and retry."
        )
    major = int(out["vectorworks_version"].split(".")[0])
    if major in app.PREPARED_VW_MAJORS:
        out["note"] = (
            f"Vectorworks {app.PREPARED_VW_MAJORS[major]} is supported (Python 3.14, signature "
            "index incl. its new functions) but has not been tested live yet; save before "
            "larger edits and report problems."
        )
    elif major not in app.TESTED_VW_MAJORS:
        tested = ", ".join(f"Vectorworks {y}" for y in app.TESTED_VW_MAJORS.values())
        warnings.append(
            f"Vectorworks {out['vectorworks_version']} is untested (tested: {tested}). "
            "Field names and behavior may differ; verify results and save first."
        )
    target = getattr(app.get_bridge().trigger, "target", None)
    if target:
        out["application"] = target
    timing = app.get_bridge().last_timing
    if timing:
        out["round_trip_s"] = timing.get("total_s")
    if warnings:
        out["warning"] = " ".join(warnings)
    return out


@mcp.tool(annotations=WRITE)
def vw_exec_python(code: str, modifies_document: bool = True) -> dict:
    """Run arbitrary Python inside Vectorworks with `vs` in scope.

    Returns stdout, the value of the last expression (or of a variable named
    `result`), and the traceback if it raised. Python 3.9 (Vectorworks 2026)
    or 3.14 (2027): write 3.9-compatible code. Handles are only valid during
    the call; return UUIDs (vs.GetObjectUuid) to refer to objects later.
    Every vs.* call is checked against the signature index first: a wrong
    argument count/type raises VSArgumentError with the right signature
    instead of opening Vectorworks' modal "Script Errors" dialog. Look up
    signatures with vw_vs_lookup (instant). Set modifies_document=False for
    read-only code; otherwise vw_revert_last can't step past this call.

    2D pitfalls: POINT = (x, y) or two numbers; colours one (r, g, b) tuple
    0..65535. vs.Arc(p1, p2, start, SWEEP) - last value is the sweep, not
    the end angle. Names are ONE namespace for objects, classes, layers and
    resources (vs.GetObject(name) may return a class: check vs.GetTypeN).
    Collect objects with vs.ForEachObject(def f(h), criteria) first, change
    them afterwards; never create/delete/restack inside the callback.
    Criteria quote names: "(L='EG')", "((C='Wand'))". ClassList(i) is
    1-based. Renamed: ShowClass/HideClass, SetName (not SetLName),
    MirrorN, LinearDim (7 args), DoMenuTextByName('Fit To Objects', 0).
    GetBBox of rotated text is a screen box (use GetTextOrientation).
    Import/export commands open modal dialogs: don't call them.
    open() defaults to ASCII in Vectorworks: pass encoding="utf-8".
    vs.Mirror on a symbol instance breaks it (place a mirrored definition
    instead - vw2d_place_asset does that).
    """
    res = _run_raw(code)
    if modifies_document:
        app.get_journal().record(vw("current_document"), "vw_exec_python", None)
    return {k: res.get(k) for k in ("ok", "value", "stdout", "error", "traceback")}


TYPE_NOTES = {
    "POINT": "POINT = (x, y) tuple or two numbers",
    "POINT3D": "POINT3D = (x, y, z) tuple or three numbers",
    "VECTOR": "VECTOR = (x, y, z) tuple or three numbers",
    "COLOR": "COLOR = ONE (r, g, b) tuple, 0..65535 (loose numbers are ignored silently)",
    "PROCEDURE": "callback: a def with named parameters (no lambda *a)",
}


def lookup_index(query: str, limit: int = 25) -> list[dict]:
    """Search the bundled vs.* signature index (no Vectorworks needed)."""
    idx = app.vs_index()
    q = query.strip()
    words = q.lower().split()
    if not words:
        return []
    hits = []
    for name, e in idx.items():
        lname = name.lower()
        hay = " ".join((lname, e.get("a", "").lower(), e.get("c", "").lower(), e.get("d", "").lower()))
        if not all(w in hay for w in words):
            continue
        params = e.get("a", "").lower()
        score = (0 if lname == q.lower() else 1, -sum(w in lname for w in words),
                 -sum(w in params for w in words), len(name))
        hits.append((score, name, e))
    hits.sort(key=lambda h: (h[0], h[1]))
    out = []
    for _, name, e in hits[:limit]:
        params = [p.split(":", 1) for p in e.get("a", "").split(", ") if p]
        sig = ", ".join(f"{n}: {t}" if t and t != "?" else n for n, t in
                        ((p[0], p[1] if len(p) > 1 else "") for p in params))
        types = {p[1] for p in params if len(p) > 1}
        row = {"name": name, "signature": f"vs.{name}({sig})", "returns": e.get("r") or None,
               "category": e.get("c") or None, "doc": e.get("d") or None,
               "argument_check": e.get("t") is not None}
        if e.get("v") and e["v"] >= "2025":  # only where it matters for 2026/2027
            row["since"] = f"Vectorworks {e['v']}"
        notes = [TYPE_NOTES[t] for t in sorted(types) if t in TYPE_NOTES]
        if notes:
            row["notes"] = notes
        out.append({k: v for k, v in row.items() if v is not None})
    return out


@mcp.tool(annotations=READ)
def vw_vs_lookup(query: str, limit: int = 25, live: bool = False) -> list[dict]:
    """Look up vs.* functions: exact signature with parameter types, return
    type, category, description and first Vectorworks version, from the
    bundled index of all ~3,000 functions (instant, no Vectorworks needed).
    Search by name or words, e.g. "Rect", "record field", "symbol insert".
    POINT parameters take a tuple (x, y) or two numbers; COLOR takes one
    (r, g, b) tuple. live=True searches the running Vectorworks' own
    docstrings instead (also finds partner functions like CE_*/CW_*); this
    happens automatically when the index has no match."""
    hits = [] if live else lookup_index(query, limit)
    if hits:
        return hits
    res = _run_raw(LOOKUP_CODE % __import__("json").dumps({"query": query, "limit": limit}))
    if not res["ok"]:
        raise ToolError(res["error"])
    return [dict(r, source="live") for r in res["value"]]


@mcp.tool(annotations=READ)
def vw_document_info() -> dict:
    """Active document: name, path, units, active layer/class, counts, selection size."""
    return vw("document_info")


@mcp.tool(annotations=READ)
def vw_list_layers() -> list[dict]:
    """All layers (design and sheet) with visibility and which one is active."""
    return vw("list_layers")


@mcp.tool(annotations=READ)
def vw_list_classes(name_contains: str | None = None) -> list[str]:
    """All class names, optionally filtered by a case-insensitive substring."""
    return vw("list_classes", name_contains=name_contains)


@mcp.tool(annotations=READ)
def vw_get_selection(limit: int = 500) -> list[dict]:
    """Selected objects on the active layer: uuid, type, name, class, layer,
    bbox, attached records, and symbol / plug-in name where relevant."""
    return vw("get_selection", limit=limit)


@mcp.tool(annotations=READ)
def vw_find_objects(
    class_name: str | None = None,
    layer: str | None = None,
    object_type: str | None = None,
    plugin: str | None = None,
    record: str | None = None,
    field: str | None = None,
    value: str | None = None,
    name: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """Find objects by any combination of exact class, layer, type ("Symbol",
    "Rectangle", "Plug-in Object"...), plug-in name ("Lighting Device"),
    attached record (optionally with field == value) and object name."""
    return vw(
        "find_objects", cls=class_name, layer=layer, type_=object_type, plugin=plugin,
        record=record, field=field, value=value, name=name, limit=limit,
    )


@mcp.tool(annotations=READ)
def vw_get_record(uuid: str, record: str) -> dict[str, Any]:
    """All field values of a record (or a plug-in object's parameters, using
    its plug-in name as the record) on the object with this UUID."""
    return vw("get_record", uuid=uuid, record=record)


@mcp.tool(annotations=WRITE)
def vw_set_record(uuid: str, record: str, values: dict[str, Any]) -> dict[str, Any]:
    """Set record fields (or plug-in parameters) on one object; unknown field
    names are rejected before anything changes. One undo step. Returns the
    record afterwards."""
    out = vw("set_record", uuid=uuid, record=record, values=values)
    app.get_journal().record(
        out["document"], "vw_set_record",
        [{"op": "fields", "uuid": uuid, "record": record, "before": out["before"]}],
    )
    return vw("get_record", uuid=uuid, record=record)


@mcp.tool(annotations=READ)
def vw_list_symbols(
    name_contains: str | None = None, folder: str | None = None, limit: int = 1000
) -> list[dict]:
    """Symbol definitions in the document's resource list, with their folder
    path; filter by name and/or folder substring (e.g. fixture lookup)."""
    return vw("list_symbols", name_contains=name_contains, folder=folder, limit=limit)


@mcp.tool(annotations=WRITE)
def vw_insert_symbol(
    name: str,
    x: float,
    y: float,
    z: float | None = None,
    layer: str | None = None,
    class_name: str | None = None,
    rotation: float = 0.0,
) -> dict:
    """Place a symbol from the document's resources at x, y (document units),
    optionally at height z, on a layer and in a class. One undo step."""
    info = vw(
        "insert_symbol", name=name, x=x, y=y, z=z, layer=layer, cls=class_name,
        rotation=rotation,
    )
    app.get_journal().record(
        info.pop("document"), "vw_insert_symbol", [{"op": "created", "uuid": info["uuid"]}]
    )
    return info


@mcp.tool(annotations=WRITE)
def vw_revert_last() -> dict:
    """Undo exactly the last MCP write in the active document: deletes what
    a tool created, restores objects a tool changed or deleted (geometry,
    attributes, class, name, group and stacking position) and restores
    record fields. Call repeatedly to step further back. (Vectorworks' own
    Cmd-Z merges consecutive MCP edits.) Raw vw_exec_python edits can't be
    reverted this way. Restored objects get new UUIDs (returned in mapping)."""
    journal = app.get_journal()
    document = vw("current_document")
    try:
        entry = journal.pop(document)
    except JournalError as e:
        raise ToolError(str(e)) from e
    try:
        size = sum(1 + (len(op.get("before") or []) + len(op.get("after") or [])
                        if op.get("op") == "replaced" else 0) for op in entry["ops"])
        result = app.vw2d("revert2d", ops=entry["ops"], timeout=max(120.0, 0.1 * size))
    except ToolError as e:
        if "still working on this request" in str(e):
            # Vectorworks has taken it and finishes it: putting the entry back
            # would let a second revert restore the same objects twice
            raise ToolError("The revert of %s is still running in Vectorworks (large step). Wait until "
                            "Vectorworks is responsive again and check the drawing; this step is no longer "
                            "in the revert journal." % entry["tool"]) from e
        journal.record(entry["document"], entry["tool"], entry["ops"])  # never ran: put it back
        raise
    journal.remap(document, result.get("mapping") or {})
    return {"reverted_tool": entry["tool"], **result}
