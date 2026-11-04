# vectorworks-2d-mcp: guide for Claude Code sessions

Mac-native MCP server for exact, well-organised 2D drawing in Vectorworks.
Fork of https://github.com/giorgiomoroder/vectorworks-mcp (MIT), repo
https://github.com/seraphinernst4/vectorworks-2d-mcp. Status: **v0.7.0
beta**, tested on Vectorworks 2026 Update 7 (German) on macOS; Vectorworks
2027 prepared (Python 3.14), not live-tested. Read `docs/architecture.md`,
`docs/api-pitfalls.md` and `docs/vs-notes.md` before changing anything that
touches Vectorworks.

## 2D layer (this fork)

- `src/vwmcp/vwlib2d.py`: runs **inside** Vectorworks after vwlib (Python 3.9,
  stdlib only). Reads exact geometry, draws, edits, booleans, snapshots +
  `revert2d`. Every write returns journal ops: `created`, `replaced`
  (snapshots before + uuids after), `attrs`, `fields`.
- `src/vwmcp/geom/curves.py`: exact segment math incl. the Vectorworks
  arc-vertex (fillet) model, also 3.9-compatible. `shapes.py` turns records
  into shapes, `checks.py` finds issues, `fixes.py` plans corrections,
  `specs.py` validates draw requests (Decimal units), `render.py` draws the
  check image.
- `src/vwmcp/tools/draw2d.py`: the `vw2d_*` MCP tools. `calls.call2d` sends
  vwlib + vwlib2d with each call; results come back as a JSON string.
- Live-test VW-side changes on a throwaway document (the tests only cover
  server-side logic plus a fake for the tool flow).

## Profile, imports and clean-up (0.7)

- `src/vwmcp/profile.py`: the user's profile (settings + own rules,
  `~/.vwmcp/profile.json`). `app.build_instructions()` puts its rule lines
  into the server instructions; an explicit request in the chat always wins.
  The extension launcher answers `initialize` from static.json, so
  `mcpb/server/profile.js` swaps the default profile block for the user's
  one (from `profile.table()`, written into static.json by
  `scripts/dump_static.py`); `tests/test_profile_js.py` keeps both texts
  identical. Change a rule text only in `profile.SETTINGS`.
- `src/vwmcp/tools/cleanup.py`: `vw2d_profile`, `vw2d_import` (DWG/DXF via
  `vwlib2d.import_dwg` = `ImportDXFDWGFile(path, True)`, no dialog; journal op
  `layers_created`), `vw2d_join` (`geom/join.py`, VW side `join_pieces`),
  `vw2d_simplify` (`geom/simplify.py`, Douglas-Peucker on the original
  points, via `replace_geometry`), `vw2d_group` (`group_objects`, journal op
  `grouped` -> `HUngroup`). `dxfscan.py` counts DXF entities;
  `geom/inventory.py` is the stock-taking report.
- Known losses (tested with Rhino 8 / Vectorworks 2026): Vectorworks drops
  rational splines from DWG/DXF; Rhino writes no splines into R12 DXF.
  Rhino export (DWG 2018) with splines/polycurves as polylines brings
  everything.
- `vw2d_check(preset="drawing")` for Make2D/VectorView/DWG drawings
  (`run_checks(overlap_fills_only=True)`: nested unfilled outlines are no
  overlap; duplicates still count).
- `geom/simplify.py` keeps neighbours seamless: points within 5 % of the
  tolerance are one cluster; a shared cluster kept by one outline is kept
  by all (iterated to agreement), a kept point lying on a neighbour's edge
  (T-junction) keeps that edge, and each stretch between kept points is
  Douglas-Peucker'd again (`_constrained`), so the tolerance holds. Objects
  that aren't simplified keep every point (`context` = the rest of the
  layer when the scope is narrower). Live (VectorView section, 557 fills):
  61 389 -> 18 109 points, 0 overlap errors; each outline on its own: 126.
- 'By class' keeps the look: `vwlib2d.keep_looks` (from `set_attributes`
  and `organize_apply`) gives a class the common look of its objects when
  they all agree (fill doesn't count for lines) and no other object follows
  the class yet; journal op `class_look` restores the class on revert.
- Revert must be exact: `make_poly(..., exact=True)` (only `_rebuild`) also
  draws Bezier (1), cubic (2) and radius (4) vertices - CurveTo /
  CurveThrough / ArcTo, then `_exact_vertex_types` sets what Vectorworks
  doesn't (radius vertices). `_check_restorable` refuses a revert before it
  changes anything. Drawing tools still take corners and arcs only.
- Live E2E: `scripts/e2e_live.py <phases>` on the Mac with the extension's
  Python, journal/profile isolated in `$VWMCP_E2E_OUT`; `exact` compares a
  full fingerprint (geometry, vertex types, attributes, order) before and
  after revert. Start it detached and don't send Vectorworks requests while
  it runs.

## Planning (0.6, Raumpilot knowledge)

- `src/vwmcp/planning/`: `kb.py` (loads `data/*.json`: elements, clearances
  incl. the VDI 6000 side-distance matrix, rooms, layouts, rules), `engine.py`
  (rooms in cm, element frame = insertion at the middle of the wall side,
  object toward -y; movement areas, checks, layout generator), `symbols.py`
  (Raumpilot-style symbol geometry per element kind, mm), `render.py`
  (preview PNGs), `style.py` (Raumpilot style as base layer, user styles and
  own symbols in `~/.vwmcp/styles.json`).
- `src/vwmcp/tools/planning.py`: `vw2d_knowledge`, `vw2d_plan_room`,
  `vw2d_check_room`, `vw2d_place_element`, `vw2d_style`. Drawing goes through
  `vwlib2d.place_elements` (one undo step, all-or-nothing).
- `planning/data/*.json` is **generated**: `python tools/build_planning_kb.py
  <notes dir>` from per-section notes (JSON, see the script docstring). Book
  layouts that fail their own checks are marked `reference_only`. Don't edit
  the data by hand; fix the notes or the curated overrides in the script.
- **Copyright:** the Raumpilot books are the user's reference copies. Store
  only facts (dimensions, rules) in own words with book + printed page
  (= PDF page - 2); never verbatim text, tables or drawings.

## Layout

- `vw-plugin/vwmcp/vwmcp_bridge.py`: in-app executor (atomic claim, checked
  `vs` proxy, lib cache, self-reload). Installed with `vs_index.json` to
  `~/Library/Application Support/Vectorworks/<year>/Plug-ins/vwmcp/` for every
  year >= 2026. Started directly by the trigger (VectorScript `PythonExecute`,
  default since 0.5) or by the user-created **MCP Run Job** menu command
  (mode `menu`). Stdlib only,
  Python 3.9 + 3.14. Bump `BRIDGE_VERSION` and `app.EXPECTED_BRIDGE_VERSION`
  together whenever the job/result format changes.
- `src/vwmcp/data/vs_index.json`: signature index (build with
  `tools/build_vs_index.py`, see its docstring); used by the bridge's
  argument check and by `vw_vs_lookup`.
- `src/vwmcp/client.py`: job folder protocol (`~/.vwmcp/jobs`, `results`,
  `lib`): claim timeout 15 s → `.cancelled`, result timeout 600 s,
  re-trigger, housekeeping, timings, log.
- `src/vwmcp/trigger.py`: macOS trigger: `osascript` → `run VectorScript` →
  `PythonExecute` (mode `direct`, default since 0.5; whole live suite passed)
  or `DoMenuTextByName('MCP Run Job', 0)` (mode `menu`; `auto` = menu, then
  direct). Finds the running Vectorworks with `ps`. Guards:
  not running, launched <20 s, at most 3 pending Apple Events.
- `src/vwmcp/vwlib.py`: helper library executed **inside** Vectorworks, sent with every
  typed tool call. **Must stay Python 3.9** (VW 2026 embeds 3.9.2): no `match`,
  no `X | Y`, stdlib only. `tests/test_calls.py` parses it with `feature_version=(3, 9)`.
- `src/vwmcp/journal.py`: per-call revert journal (`~/.vwmcp/journal.jsonl`).
- `src/vwmcp/update.py`: update check against `server.json` on GitHub `main`
  (cached 6 h, background thread at start; `vw_ping` reports it; since 0.6.5
  `app.KeystoneServer.call_tool` appends `update.notice()` to every tool
  result while `update.pending()` - cache only, never blocking - knows a
  newer version, so Claude insists on updating) and
  `vw_update` (downloads the newest `.mcpb`, checks its manifest, opens it so
  Claude Desktop shows the install dialog). Bump `server.json` with every
  release - it is what installed copies compare against.
- `src/vwmcp/tools/generic.py`, `tools/spotlight.py`: MCP tools (mcp 2.x `MCPServer`,
  the renamed FastMCP). `app.py` holds the server, `vw()` helper, annotations.
- `src/vwmcp/install.py` + `server.py`: `vwmcp` (stdio server), `vwmcp install-plugin`.
- `scripts/install-macos.sh`: one-line installer (uv, bridge, Claude Desktop + Code config).
  `scripts/install-windows.ps1` is experimental and untested.

## Hard rules (learned by crashing the user's Vectorworks)

- **Never** send `run Python script` over AppleScript. Writes, or any call during
  launch / a modal dialog, segfault Vectorworks 2026. The only Apple Event the
  server sends is the short VectorScript hop (`PythonExecute` of the bridge, or
  the menu command in mode `menu`).
- **Never** poll Vectorworks in a loop with probes. Announce each live test to the
  user, send one call at a time, and keep VectorScript in a file (shell quoting
  broke it once).
- The VW process is `…/Vectorworks 2026.app/Contents/MacOS/Vectorworks`; check
  crash reports in `~/Library/Logs/DiagnosticReports/` (`Vectorworks-*.ips`)
  as ground truth for crashes.
- A wrong `vs.*` signature shows a modal **Script Errors** dialog instead of
  raising; the user must click OK before anything else runs. Check signatures
  with `vw_vs_lookup` (live docstrings) first.
- Vectorworks merges consecutive scripted runs of MCP Run Job into one Cmd-Z.
  Per-call undo is `vw_revert_last` (journal). Don't promise Cmd-Z granularity.
- Test writes on throwaway documents only; ask the user which files are safe.

## Development

```bash
uv sync
uv run pytest -q          # ~150 tests; fake executor runs the real bridge code
uv run vwmcp              # stdio server
uv build                  # wheel must contain vwmcp/_plugin/vwmcp_bridge.py
```

- TDD for server-side logic (protocol, trigger, journal, verification, reports).
  Code that runs inside Vectorworks is validated **live** instead (a fake `vs`
  only encodes our assumptions); record findings in `docs/vs-notes.md`.
- Live checks without the MCP client: `from vwmcp.client import Bridge;
  from vwmcp.calls import call; call(Bridge(timeout=30), "document_info")`.
- Full live test: `python scripts/live_test.py` on the Mac (protocol,
  argument check, pitfalls, regression). It can be started from inside
  Vectorworks (`subprocess.Popen(..., start_new_session=True)`) so it
  runs after the starting job returns; don't send other requests while
  it runs. Bridge/lib code for Python 3.9 and 3.14:
  `uv run --no-project --python 3.9 --with pytest python -m pytest tests/test_bridge.py`.
- Full MCP round trip: spawn `uv run --project . vwmcp` with
  `mcp.client.stdio.stdio_client` and call tools.
- Commits use the user's global git identity (giorgiomoroder). Don't push
  without the user's OK.

## Roadmap / TODO

Next: **what Michael actually draws** (stated 2026-10-16): truss, lighting
fixtures, stage parts, band members and instruments. These are mostly
Spotlight plug-in objects and library symbols, so prefer native objects over
hand-built geometry (walls/extrudes are secondary). Investigate each live
on a throwaway doc and record findings in `docs/vs-notes.md`:
- [ ] **Library access.** Browse and import symbols and resources from Vectorworks
      libraries and Resource Manager favorites into the document (fixtures, truss,
      musicians, backline, stage decks), not just symbols already in the file.
      Find the right `vs` calls (resource lists / import) live.
- [ ] **Truss.** Place straight truss runs, corners and towers (Spotlight truss
      objects / Braceworks-ready symbols) by length, height and type; list existing truss.
- [ ] **Lighting fixtures.** Insert Lighting Devices from a fixture symbol or GDTF,
      hang them on a position or truss at a spacing, set Unit Number, Channel and
      Purpose on insert (journaled), auto-number by position.
- [ ] **Stage parts.** Stage decks/platforms/risers (Spotlight stage objects),
      stairs, soft goods (borders, legs, drapes; `Soft Goods` PIO exists), at sizes.
- [ ] **Band members and instruments.** Place people and backline symbols (drum
      riser, amps, keys, mic stands) on the stage plot at positions, named and classed.
- [ ] **`vw_screenshot`**: capture the Vectorworks document window (CGWindowList
      window id via JXA + `screencapture -x -o -l <id>`; needs Screen Recording
      permission) and return it as an image so Claude can check its work.
- [ ] Generic helpers these need: `vw_create_layer`/`class`, `vw_delete_objects`,
      `vw_move_objects`, `vw_set_view` (Top/Plan, iso, fit to objects). All journaled.
- [ ] Journal raw Python better: optional "created objects" capture (diff UUIDs
      before/after) so `vw_revert_last` can delete what `vw_exec_python` drew.
- [ ] Maybe: import a 3D model file (e.g. a Blender FBX/OBJ export) as a symbol
      so custom pieces modeled elsewhere can be placed like library items.
- [ ] Lower priority: architecture drawing tools (walls via `SetComponentWidth` /
      `SetWallCornerHeights`, doors), only if venue shells are needed often.

Beta hardening:
- [ ] Gather tester reports (issue forms: bug + "works on my setup", label `compatibility`).
- [ ] Other VW versions: add majors to `app.TESTED_VW_MAJORS` only after live testing;
      `VWMCP_APP` env var covers the app name.
- [ ] Non-English VW: menu category names differ in README steps; check that
      `DoMenuTextByName` works with the English command name we create.
- [ ] Windows trigger (none today; user runs MCP Run Job by hand). Candidate:
      a dialog with `RegisterDialogForTimerEvents` as an explicit "listen" mode.
- [ ] `LDevice_*` functions: find real signatures (currently avoided).
- [x] Clean up stale `~/.vwmcp/results/*.json` from jobs whose server timed out (0.4 housekeeping).
- [ ] Undo menu label: `vs.NameUndoEvent` has no visible effect; investigate.

Distribution:
- [ ] PyPI release (`vwmcp`; check the name is free), then MCP registry
      (`server.json` + `mcp-name` comment in README are ready).
- [ ] CI (GitHub Actions: `uv run pytest` on macOS + Linux).
- [ ] Stretch from the original brief: ConnectCAD signal/cable tools,
      Vision/MVR export, GDTF fixture mapping.

## Design decisions already made (don't relitigate)

- Transport: job folder + AppleScript→VectorScript hop (user chose it after the
  direct-Python crash findings). Since 0.5 the VectorScript runs the bridge via
  `PythonExecute` (mode `direct`, no menu command to set up); the menu command
  stays available (mode `menu`). Never `run Python script`. No in-app polling loop.
- Bridge location: user Plug-ins subfolder `Plug-ins/vwmcp/` (user approved).
- `vw_undo_mark` from the brief replaced by `vw_revert_last` + journal (user approved).
- Objects are addressed by UUID (`GetObjectUuid` / `GetObjectByUuid`), never handles.
- Lighting Device address edits go through `UniverseAddress`; write tools re-read
  in a second request and report `not_applied`.
