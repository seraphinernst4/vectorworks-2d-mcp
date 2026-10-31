# `vs.*` notes (Vectorworks 2026, macOS, live-tested)

Findings from building and using this server against Vectorworks 2026
Update 3 (build 846827). Embedded Python is **3.9.2**. Each run of the
menu command gets a fresh Python state (module globals don't persist).

## General

- `vs.GetName(h)` returns `"none"` for unnamed objects. A real object named
  "none" is the one `vs.GetObject("none")` returns, though that call can
  also match the class **None**, so compare handles.
- Layer names: `vs.GetLName(layer)`. `GetName` returns `""` for layers.
- Handles print as integers and are only valid during one script run. Use
  `vs.GetObjectUuid(h)` / `vs.GetObjectByUuid(uuid)` across calls.
- A nil handle compares as `str(h) == "0"`.
- `vs.ForEachObject(fn, "(ALL)")`, `"(T=RECT)"`, `"(PON='Lighting Device')"`,
  `"(T=86)"` (plug-in objects) all work.
- `vs.GetUnits()` → `(fraction, display, format, upi, name, sqName)`, e.g.
  `(25, 3, 2, 1.0, '"', ' sq ft')` for feet-and-inches documents.
- Most `vs` functions carry a docstring with a `Definition:` signature;
  `LDevice_*` don't.
- A call with the wrong number or type of arguments doesn't raise. It
  queues a modal **Script Errors** dialog shown when the script ends.
- `vs.NameUndoEvent(...)` neither names the Undo menu item nor stops
  Vectorworks merging consecutive scripted menu-command runs into one undo.

## Spotlight

- Lighting Device = plug-in object; parameters via
  `vs.GetRField(h, "Lighting Device", field)` / `SetRField`, then
  `vs.ResetObject(h)`. The reset takes effect when the script ends, so read
  back in a separate run.
- `UniverseAddress` (`"2/101"`) or `Absolute Address` are authoritative;
  `Universe` / `DMX Address` written on their own are overwritten on reset.
  Absolute = (universe-1)*512 + address.
- Useful fields: Unit Number, Channel, Dimmer, Purpose, Position, Inst Type,
  Wattage, Color, Template / Template2 (gobos), Focus, Circuit Number/Name,
  Symbol Name, Device Type, Num Channels, Fixture Mode, GDTF Fixture Name,
  User Field 1-6. Unit numbers repeat per position and may be blank.
- Positions: `Light Position Obj` (name in `Position Name`), `Lighting Pipe`,
  `Lighting Pipe Ladder` (`Name`). Devices reference positions by name.
- `LDevice_GetParamStr(h, 0, name)` is the wrong signature (Script Errors,
  returns `""`). Real signature unknown.

## Drawing and 3D

- Walls from `vs.Wall(...)` have one component; `SetWallThickness` and
  `SetWallHeights` silently do nothing (even after `ConvertToUnstyledWall`).
  Use `vs.SetComponentWidth(w, 1, t)` for thickness and
  `vs.SetWallCornerHeights(w, h, 0, h, 0)` for height.
- Doors: `vs.CreateCustomObjectN('Door', pt, rot, False)` (no dialog). Set
  both `Width` and `DoorWidth` (Width alone reverts). Local frame:
  `SwingDirection` Out → local +y, In → local -y; `SwingHingeSide` Right →
  hinge at local -x, Left → +x. `vs.GetBBox` on doors is unreliable; verify
  via the arc inside `FInGroup` (`HCenter` / `GetArc`).
- Extruded circles (`ArcByCenter` inside `BeginXtrd`) render as octagons at
  low 3D conversion resolution; build round things from 48-gon polygons.
- Top/Plan view: `vs.DoMenuTextByName('Standard Views', 1)`. Zoom to objects:
  select them, then `vs.DoMenuTextByName('Fit To Objects', 0)`.

## Screenshots (outside Vectorworks)

VectorScript can't save the viewport to a file (`ExportImageFile` only
exports existing image objects). From macOS: get the document window's id
with JXA `$.CGWindowListCopyWindowInfo` (owner contains "Vectorworks",
layer 0, title = document name), then `screencapture -x -o -l <id> out.png`.
That works even when the window is covered. It needs Screen Recording
permission for the calling app.

## AppleScript

See `architecture.md`: only `run VectorScript` calling
`DoMenuTextByName('MCP Run Job', 0)` is safe; `run Python script` writes,
and any script sent during launch or a modal dialog, crash Vectorworks.
The dictionary has no usable document list (`every document` → `missing value`).

## 2D drawing (Vectorworks 2026 Update 7, German UI, live-tested for the vw2d tools)

- Coordinates go through `vs` in document units; Vectorworks stores mm
  internally, so a value can come back 1 ulp off (39 of 43 random points
  were bit-exact, the rest within 2e-15 m). Treat 1 nm as "identical".
- Polyline vertex types: 0 corner, 1 bezier, 2 cubic, 3 arc, 4 radius.
  `GetPolylineVertex` -> `(p, type, radius)`, radius in document units.
- **Arc vertex (3) = fillet.** The vertex is a control point; the corner is
  replaced by an arc tangent to both edges. Tangent distance
  d = r·(1+cos φ)/sin φ, clamped to the available edge length; radius 0 or
  too big = largest that fits; an edge between two arc vertices gives each
  half. Matches `ConvertToPolygon` to 1e-14 m (`geom/curves.py`). Arcs at
  the ends of an open polyline are dropped to corners.
- **Radius vertex (4)** with radius 0 draws an arc *through* the vertex
  (point on arc); evaluated via Vectorworks' polygonization only.
- `ArcTo(p, r)` takes r in document units (unlike `InsertVertex`, which
  wants mm for arcs).
- Rectangles: `Get2DPt(h, 1..4)` are the (rotated) corners; `HAngle`,
  `HWidth`, `HHeight`, `HCenter` give the true size and rotation.
- Arcs: `Get2DPt(h, 1)` centre, `2` start point, `3` end point; `GetArc` ->
  (start, sweep). A full circle is an arc with sweep 360.
- `ClipSurfaceN(a, b)`: deletes `a`, keeps `b`, result keeps a's class,
  attributes and name; a tool fully inside makes a polyline with a hole.
  `AddSurface(a, b)` deletes both, result keeps a's attributes.
  `IntersectSurface` keeps both. All of them add ~1e-15 noise to vertices
  (27 -> 26.999999999999996); vwlib2d snaps it back to input coordinates.
- `AddHole(outer, hole)` keeps outer's UUID, leaves the hole template
  (delete it) and may reverse the vertex order of the outline.
- `ConvertToPolyline(rect)` replaces the object (new UUID).
- `OffsetPolyN(h, d, False)`: positive d grows a counter-clockwise outline.
  The result goes on the **active** layer, in the active class with default
  attributes (not the original's): make the original's layer active first,
  then `SetClass` + copy the attributes. Its coordinates are exact – snap
  candidates must be exact decimals too (2.615 - 0.01 in float is
  2.6050000000000004).
- `DoMenuTextByName("Fit To Objects", 0)` zooms to the selection; the name
  is internal, it works with the German UI.
- Stacking: `FInLayer` -> `NextObj` runs bottom to top; new objects go on
  top; `HMoveBackward(h, False)` moves one step down.
- Solid fill uses the fill **background** color (default object: fore
  black, back white, shows white). Set both.
- `SetVectorFill(h, name)` needs the hatch in the document:
  `BuildResourceList(66, -105/-131/..., "")` + `ImportResourceToCurrentFile`.
  Line types: type 96, folder 174, then `SetLSN(h, -Name2Index(name))`.
  German default content names, e.g. "Beton  SRF" (two spaces),
  "ISO-02 Strichlinie".
- `SetCLOpacityN(class, fill, pen)` (fill first!), `SetClPenFore(class,
  (r, g, b))` accepts a 16-bit tuple.
- `TextSize/TextJust/TextVerticalAlign` before `CreateText` keep the origin;
  `SetTextJustN` afterwards moves it. Just 1/2/3 = left/center/right,
  valign 1 top, 3 center, 4 baseline, 5 bottom.
- `LinearDim(p1, p2, offset, type, 0, 0, 0)`: type 0 horizontal, 1
  vertical, 4 aligned; dimensions land in the document's dimension class
  ("Bemaßung"). `CreateChainDimension(h1, h2)` returns a plug-in object and
  accepts a chain as h1.
- `NameClass` / `Layer` create and *activate*; restore the previous active
  class/layer afterwards.
