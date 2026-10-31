"""vw2d_organize: sort a drawing's objects onto classes and layers by rules."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from vwmcp import app
from vwmcp import organize as O
from vwmcp.app import WRITE, mcp
from vwmcp.tools.draw2d import _journal

ORGANIZE_TIMEOUT = 600.0
READ_LIMIT = 200000


@mcp.tool(annotations=WRITE)
def vw2d_organize(
    rules: list[dict[str, Any]] | None = None,
    class_map: dict[str, str] | None = None,
    layer_map: dict[str, str] | None = None,
    layer: str | None = "*",
    class_name: str | None = None,
    selection: bool = False,
    uuids: list[str] | None = None,
    include_groups: bool = True,
    dry_run: bool = True,
) -> dict:
    """Sort objects onto classes and layers by rules - e.g. after a DWG
    import, or to move everything out of the none class. Dry run first
    (default): reports what each rule would change and which objects no rule
    catches (grouped by kind/class/layer/pen/line weight, so you can write
    the missing rules). Then call again with dry_run=False: all changes in
    one step, undone together with vw_revert_last.

    rules: [{"match": {...}, "class": "Wand-Tragend", "layer": "EG",
    "by_class": true}, ...] - for each target (class, layer, by_class) the
    first matching rule that sets it wins. Match keys:
    class / layer / name / symbol (glob, case-insensitive, e.g. "A-WALL*"),
    kind ("line", "polygon", "text", "dimension", "symbol", "group", ...),
    text (regex), pen / fill_color ("#rrggbb"), fill ("none" | "solid" |
    "hatch"), hatch / line_style (names), line_weight_mm (number - 0.5 also
    matches Vectorworks' stored 0.508 - or [min, max]), area_m2 / size_mm
    ([min, max], null = open end), closed, in_group, not {...}.
    by_class: also set every graphic attribute to 'by class'.
    class_map {"old*": "New"} / layer_map: shortcuts for rules that only
    match a class / layer (applied before `rules`).
    Scope: layer ("*" = all design layers, None = active layer), class_name,
    selection or uuids. Objects inside groups get classes too
    (include_groups); layers only move whole top-level objects. Classes and
    layers are created if missing (with default attributes - set them with
    vw2d_setup)."""
    all_rules = O.class_map_rules(class_map or {}) + O.layer_map_rules(layer_map or {}) + list(rules or [])
    try:
        O.validate(all_rules)
    except O.RuleError as e:
        raise ToolError(str(e)) from e
    data = app.vw2d("read_objects", timeout=ORGANIZE_TIMEOUT, uuids=uuids or None, layer=layer,
                    cls=class_name, selection=bool(selection), geometry=O.needs_geometry(all_rules),
                    allow_temp=False, limit=READ_LIMIT, bbox=None)
    mpu = data["units"]["mm_per_unit"]
    records = data["objects"]
    if class_name:  # the read also returns groups of other classes (as containers): leave them alone
        records = [r for r in records if r.get("class") == class_name]
    p = O.plan(records, all_rules, mpu, include_groups=include_groups)
    moves = p.pop("_moves")
    out: dict[str, Any] = {"document": data["document"], "dry_run": dry_run, **p}
    if data.get("truncated"):
        out["warning"] = "more than %d objects: only the first were considered - narrow the scope" % READ_LIMIT
    targets: dict[str, int] = {}
    for m in moves:
        key = " / ".join(x for x in ("class " + m["class"] if m["class"] else "",
                                     "layer " + m["layer"] if m["layer"] else "",
                                     "by class" if m["by_class"] else "") if x)
        targets[key] = targets.get(key, 0) + len(m["uuids"])
    out["targets"] = targets
    if dry_run or not moves:
        if dry_run and moves:
            out["next"] = "call again with dry_run=false to apply (one step, revertable)"
        return out
    res = app.vw2d("organize_apply", timeout=ORGANIZE_TIMEOUT, moves=moves)
    _journal(res["document"], "vw2d_organize", res["ops"])
    out["changed"] = res["changed"]
    return out
