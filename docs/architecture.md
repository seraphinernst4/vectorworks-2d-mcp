# Architecture and design notes

## Pieces

- **`vw-plugin/vwmcp/vwmcp_bridge.py`**: installed in the user Plug-ins
  folder of every Vectorworks version >= 2026 (already on Vectorworks'
  `sys.path`), together with `vs_index.json`. `run_pending()` claims the
  oldest job in `~/.vwmcp/jobs/` by atomic rename, runs it with a checked
  `vs` proxy in scope, captures stdout, the last expression (or `result`),
  and any traceback, and writes `~/.vwmcp/results/<id>.json` atomically.
  One job per call. Version-stamped (`BRIDGE_VERSION`); `vw_ping` compares
  it. Vectorworks keeps Python state between runs, so the bridge caches
  the index and compiled helper libraries, and reloads itself when its
  file changes. Stdlib only, Python 3.9 and 3.14.
- **Trigger** (since 0.5 `direct` by default): `osascript` → `run VectorScript`
  → `PythonExecute` imports the bridge (only if importable, with fresh import
  caches) and calls `run_pending()` - nothing to set up in Vectorworks. The
  older `menu` mode runs the "MCP Run Job" menu command (two lines of Python,
  created once by the user in the Plug-in Manager; `.vsm` files can't be
  authored as text).
- **`src/vwmcp/`**: the MCP server.
  - `client.py` writes jobs, fires the trigger, waits for results, withdraws
    unclaimed jobs on timeout (so they never run later by surprise), and
    logs failures to `~/.vwmcp/vwmcp.log`.
  - `trigger.py`: macOS trigger (below).
  - `vwlib.py` / `vwlib2d.py`: helper libraries executed *inside*
    Vectorworks. Sent by reference: written once to `~/.vwmcp/lib/<sha256>.py`,
    compiled once per Vectorworks session by the bridge (bridges before 0.2.0
    get the source inline), so the installed bridge never needs updating
    when tools change. Must stay Python 3.9-compatible.
  - `journal.py`: per-call revert journal.
  - `tools/generic.py`, `tools/spotlight.py`: the MCP tools.

## Job protocol (0.4)

Every step is an atomic rename, so nothing runs twice or half:

1. Server: write `jobs/<id>.tmp`, rename to `jobs/<id>.json`, fire the trigger.
2. Bridge: claim the oldest job by renaming it to `jobs/<id>.working`
   (only one rename can win), run it, write `results/<id>.json.tmp`,
   rename to `results/<id>.json`, delete `.working`.
3. Server: wait up to `claim_timeout` (15 s) for the claim. Not claimed:
   rename `<id>.json` to `<id>.cancelled`. If that rename succeeds, the job
   can never run (a trigger still queued in Vectorworks finds nothing). If
   it fails, Vectorworks has just claimed it: keep waiting.
4. Server: wait up to `timeout` (600 s) for the result of a claimed job.
   Lost triggers are re-fired after 2 s if none is in flight.
5. Housekeeping removes results nobody read, `.cancelled`, orphaned
   `.working`/`.running` older than an hour.

Helper libraries (vwlib, vwlib2d) go by reference: the server writes them
once to `~/.vwmcp/lib/<sha256>.py`, the job lists `{"sha", "path"}`, the
bridge compiles each once per Vectorworks session. Older bridges get the
source inline (and are reloaded once, see `calls.maybe_reload`).

## Checked `vs` proxy (0.4)

`vs_index.json` maps every function to type codes per Python parameter
(H handle, R real, I integer, B boolean, S string, P point, Q 3D point,
V vector, C colour, F callback). Per job the bridge puts a proxy in place
of `vs` (and in `sys.modules["vs"]` for `import vs`); each call is checked
first and a mismatch raises `VSArgumentError` with the signature. Rules
are conservative (never block what Vectorworks could accept): points as
tuple or numbers, a number tuple may span consecutive number parameters,
entries whose parameter count disagrees with Vectorworks' own docstring
are dropped, functions not in the index pass through. A job can switch
the check off with `"check": false`.

## Why the AppleScript → VectorScript hop

Vectorworks has no idle/timer callback for scripts outside of a modal dialog
(`RegisterDialogForTimerEvents` exists, but only for dialogs), so an in-app
polling loop would freeze the UI. Its AppleScript dictionary has
`run Python script` and `run VectorScript`. Findings on Vectorworks 2026
Update 3 (macOS):

| Approach | Result |
|---|---|
| `run Python script` that only reads (doc open) | works |
| `run Python script` that writes (e.g. `vs.Rect`) | **crashes Vectorworks** |
| any `run Python script` while launching / in a modal dialog | **crashes Vectorworks** |
| `run VectorScript` with a write | works |
| `run VectorScript "DoMenuTextByName('MCP Run Job', 0)"` | **works**: full document + undo context |
| `run VectorScript` with `PythonExecute` (bridge import + `run_pending()`) | **works** (2026 U7, whole live suite): same context, no menu command |

So the server only ever sends one short VectorScript (inline since 0.4;
measured: as fast as a file or a compiled script, ~0.2 s to the claim). Guards:
refuse if Vectorworks isn't running or launched < 20 s ago; one request in
flight per server; at most 3 unanswered Apple Events; the claim timeout
decides when a request is given up (usually a dialog is open).

The running Vectorworks is found with `ps` (not `pgrep`, which hides its
own ancestors); a configured version is only a preference.

Trigger modes (`VWMCP_TRIGGER`): `direct` (default since 0.5: VectorScript
`PythonExecute` runs the bridge, no menu command needed; the pattern
Vectorworks documents for AppleScript; it only imports the bridge if it is
installed, so a missing bridge never ends in an error dialog), `menu` (the
"MCP Run Job" menu command, the default up to 0.4), `auto` (menu, switching
to direct when Vectorworks answers but doesn't take the job).

## Undo

Each `MCP Run Job` run is an undo step, but Vectorworks merges consecutive
script-triggered runs: after the first run following a user action, later
runs fold into one Cmd-Z step until the user does something else.
`vs.NameUndoEvent` (even with unique names) changes neither the merging
nor the Undo menu label. Hence `journal.py`: typed write tools record
before-values (or created UUIDs) per call, stamped with the document name,
and `vw_revert_last` replays them precisely. Raw `vw_exec_python` writes
leave a barrier entry the revert won't step past silently.

## Spotlight notes

- Lighting Devices are plug-in objects `PON='Lighting Device'`; their
  parameters are read/written with `GetRField`/`SetRField` on the
  `Lighting Device` record, then `ResetObject`.
- Resets apply when the script ends, so values read back in the same job
  can be stale. Write tools re-read in a second request.
- `UniverseAddress` / `Absolute Address` are authoritative; `Universe` and
  `DMX Address` written alone are overwritten on reset.
- Position objects: `Light Position Obj` (name in `Position Name`),
  `Lighting Pipe`/`Lighting Pipe Ladder`/`Truss` (`Name`). Devices refer to
  positions by name through their `Position` field.
- `LDevice_GetParamStr` and friends exist but have undocumented signatures;
  a wrong call shows a modal "Script Errors" dialog instead of raising.
