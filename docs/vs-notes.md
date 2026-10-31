# `vs.*` notes (Vectorworks 2026, macOS, live-tested)

Findings from building and using this server against Vectorworks 2026
Update 3 (build 846827) and Update 7 (31.7.0, build 879357). Embedded
Python is **3.9.2** (Vectorworks 2027: 3.14). **Python state persists
between menu-command runs** (checked on Update 7: `sys` attributes and
`sys.modules` survive), so imported modules like the bridge stay loaded
until Vectorworks restarts or something reloads them.

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

## Findings 31.10.2026 (Update 7, live)

- `vs.GetObject(name)` returns nil for unknown names here (no type-0
  dummy), but names are one namespace: `GetObject("Keine")` returns the
  **class** (type 94). Check `GetTypeN` (16 symbol definition, 94 class,
  31 layer) before trusting the handle (`vwlib.named`).
- `FInGroup` of an emptied group returns nil here; still walk groups via
  `vwlib.children` (parent check + bound), as other versions differ.
- `ArcByCenter(x, y, r, 0, 360)` makes a full circle (type 6) and stores
  centre and start point bit-exactly at any layer scale; `Oval` makes an
  oval (type 4). Arc radius from start - centre picks up float noise
  (20.6 - 20.0 = 0.6000000000000014): read full circles as `HWidth / 2`.
- `vs.Arc(p1, p2, start, sweep)`: the last value is the sweep
  (`Arc(..., 30, 90)` -> `GetArc` = (30, 90)).
- `ClassList(0)` is `""`; classes are `ClassList(1..ClassNum())`.
- `vs.SetLayerScale` doesn't exist; use `vs.SetLScale(layer, scale)`.
- From a process started by Vectorworks, BSD `pgrep` hides Vectorworks
  (it skips its own ancestors); use `ps -axo pid=,etime=,command=`.
- Round trip of one request through osascript + menu command: ~0.19 s,
  of which ~0.1 ms runs in Vectorworks. Compiling vwlib + vwlib2d costs
  ~10 ms per call unless cached (bridge 0.2.0 caches by sha256: 0.7 ms).
- While Vectorworks runs a script, Apple Events queue up; a job that is
  cancelled (renamed to .cancelled) before the queued event runs is never
  executed (live test `busy_cancel`).

## Symbols and assets (31.10.2026, Update 7, live)

- `BeginSym(name)` … `EndSym()`: components keep the coordinates they were
  drawn at; the insertion point is the user origin (0, 0 here). Component
  layer is nil (`GetLayer` of a symbol component) - guard `GetLName`.
- Symbol folders: `NameObject(name); BeginFolderN(16); EndFolder()` makes an
  empty folder (type 92); `InsertSymbolInFolder(folder, symdef)` moves a
  definition into it; `FInFolder` / `NextObj` walk it.
- `FInGroup` works on symbol definitions as well (same as `FInSymDef`), and
  the components' parent is the definition (type 16).
- `GetObjectUuid` / `GetObjectByUuid` work for symbol definitions and
  folders, too.
- `Count("(S='name')")` counts the instances of a symbol.
- `SetSymbolOptionsN(name, 0, 4, class)` sets the default class shown when a
  user places the symbol from the resource manager.
- `GetSymRot` reads 29.999999999999996 for a symbol placed at 30°;
  location is exact.
- **`vs.Mirror(h, False, p1, p2)` on a symbol instance does not mirror the
  drawing**: the bounding box stays, `IsObjectFlipped` becomes true and
  `GetSymLoc`/`GetSymRot`/`GetEntityMatrix` report nonsense (e.g. location
  (2000, 0) for an instance at (1000, 2000), a 3D flip of 180° about x).
  Keystone never flips instances; left/right variants are separate,
  exactly mirrored symbol definitions ("… gespiegelt").
- Class line weights are stored in mils: 0.18 mm is stored as 7 mils
  (0.1778 mm), 0.13 mm as 5 mils (0.127 mm) - what Vectorworks shows as
  0.18 / 0.13.
- Python inside Vectorworks 2026 opens files as **ASCII** by default:
  `open(path).read()` of a UTF-8 file with umlauts raises
  `UnicodeDecodeError`. Always pass `encoding="utf-8"` (the bridge does).
- Placing an asset (new symbol definition with 3-13 components + instance,
  classes, folder) takes ~0.5 s per call including the round trip.

## Speed in big drawings (31.10.2026, 20 000 objects in one layer, live)

| Call | Time |
|---|---|
| `GetObjectByUuid` | 4.6 ms (searches the document: quadratic in loops - use one `ForEachObject` walk) |
| `Layer(name)` (switch the active layer) | 12.8 ms (switch once per run, not per object) |
| `NumObj(layer)` | 1.1 ms |
| `HMoveForward/Backward` (one step or to front/back) | ~12 µs |
| `Rect` + `LNewObj` | 16 µs |
| `SetClass` | 7.5 µs |
| `SetParent` (lands in front) | 1 µs |
| `NextObj` / `PrevObj` / `GetParent` / `GetObjectUuid` | < 1 µs |

Lists run from the back (first object, `FInLayer`) to the front (last; new
objects are appended there). `HMoveBackward(h, False)` moves one step
towards the first object, `HMoveBackward(h, True)` to the first.
