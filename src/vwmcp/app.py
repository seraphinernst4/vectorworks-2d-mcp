"""Shared MCP server instance and bridge access for tool modules."""

from __future__ import annotations

import base64
import functools
from importlib import resources
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, Icon, TextContent, ToolAnnotations

from vwmcp import calls, update
from vwmcp.client import Bridge, BridgeError
from vwmcp.journal import Journal

EXPECTED_BRIDGE_VERSION = "0.2.1"
# Vectorworks major versions this release was tested against (31 = 2026).
TESTED_VW_MAJORS = {31: "2026"}
# Prepared but not yet tested live (32 = 2027: Python 3.14, same vs API + new functions).
PREPARED_VW_MAJORS = {32: "2027"}

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
DELETE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)

_HEAD = """\
Tools for a live Vectorworks session. Every call runs inside the user's open
document (Keystone starts its bridge through AppleScript/VectorScript).

UPDATES - insist on them: while a newer Keystone is out, every tool result
ends with a "KEYSTONE UPDATE REQUIRED" note (vw_ping: "update_available").
Then, before you continue the user's task, stop and tell the user that a new
version is available and that they should update now; insist on it and offer
to do it right away with vw_update (it finds the newest version, downloads
the extension file to Downloads and opens Claude's install dialog; the user
confirms "Aktualisieren"; nothing to do in Vectorworks). Don't just mention
it in passing. Only if the user explicitly says not now, carry on - and
remind them at the end of every reply until they have updated. Asked for the
newest version, use vw_update too.

THE USER DECIDES. Keystone works for many people with different habits.
What the user asks for in the chat always wins over every rule below - if
they ask to simplify, simplify; to keep everything, keep everything; to
leave overlaps, leave them. The rules below are this user's profile
(vw2d_profile shows and changes it; when the user corrects how you work and
wants it that way from now on, offer to save it there, also as one of their
own rules). When a request is unclear about something that changes a lot of
geometry, ask once instead of guessing.

PROFILE (current settings):
"""

_BODY = """
EXACT 2D DRAWING (vw2d_* tools):
- Exactness first. Give coordinates in document units (or pass unit="mm"/"m";
  conversion and relative point chains use exact decimal arithmetic). Never
  eyeball or round. Every drawing tool reads back what Vectorworks stored
  and reports the deviation; it must be 0 (exact).
- Organisation: design layers separate floors/drawing areas (with the right
  scale); classes separate building parts (walls, openings, stairs,
  furniture, dimensions, text...). Check the existing structure first
  (vw2d_structure) and follow it; create missing classes/layers with
  vw2d_setup instead of drawing into the wrong ones.
- Workflow: 1) vw2d_structure / vw2d_read to understand the drawing,
  2) draw/edit, 3) check and fix as the profile's check mode says,
  4) show the result (vw2d_preview, vw2d_select) so the user sees it.
- Work in the document that is open; 2D tasks stay 2D.
- Sorting a drawing onto classes/layers: vw2d_structure, then vw2d_organize
  with rules as a dry run (match by class, kind, size, area, bbox...), show
  what would change and what stays unmatched, refine, then apply.

IMPORTED AND GENERATED DRAWINGS (DWG/DXF from Rhino Make2D, VectorView,
ArchiCAD, AutoCAD; sections, elevations, isometries):
- Import with vw2d_import (no dialog) or take stock of an imported layer with
  vw2d_import(layer=...): what arrived, what is missing, how fragmented it
  is, and what to do next. Tell the user what Vectorworks dropped:
  Vectorworks leaves out rational splines (circles, ellipses and arcs that
  Rhino writes as NURBS) when importing DWG/DXF, and R12 DXF loses all
  splines; the fix is to export from Rhino again with splines (and
  polycurves) as polylines.
- Such drawings are thousands of loose pieces by nature; outlines on fills
  and gaps between fragments are normal there. Check them with
  vw2d_check(preset="drawing") instead of fixing every reported gap.
- Cleaning up: vw2d_organize (classes, e.g. by bbox for one curtain),
  vw2d_join (pieces into polylines), vw2d_simplify (fewer points within a
  tolerance; dry run with before/after first), vw2d_group, line weights via
  class attributes (vw2d_setup) or vw2d_set_attributes. Work area by area on
  big drawings and keep the user in the loop.
- Never do bulk edits of the user's drawing with vw_exec_python (they can't
  be reverted); if a typed tool can't do it, say so and ask.

PLANNING (Raumpilot knowledge: what space things need, standard situations):
- Before planning a room or placing furniture/sanitary/kitchen elements, ask
  vw2d_knowledge (element, room, query or topic): Stellflächen, movement areas
  (Bewegungsflächen, larger depth when objects face each other), side
  distances (VDI 6000), heights, barrier-free/wheelchair values (DIN 18040),
  room sizes and rules for stairs, doors, corridors, lifts, parking, escape
  routes, workplaces, schools - each with its source page. Quote the values
  and sources instead of guessing.
- A room (bath, WC, shower room, kitchen, dining ...): vw2d_plan_room with the
  interior size, the door and mode (standard / barrierefrei /
  rollstuhlgerecht). It fits the book's standard situations and generates
  more, all checked; show the preview and the problems, let the user choose,
  then draw with draw=true and the room's corner as origin. A room that is
  already drawn: vw2d_check_room. Single elements: vw2d_place_element (with
  wall="above|left|below|right" so it faces into the room).
- Representation is the user's: vw2d_style shows/sets the drawing style.
  Keystone's Raumpilot look is only the default first layer; when the user
  wants their office's look, save a style (classes, colours, movement areas
  on/off) and map elements to the office's own symbols (optionally from a
  library file). Never change a user's style without being asked.
- Elevations/sections of furniture, sanitary objects, trees, people and
  cars: vw2d_assets / vw2d_place_asset (view "front" / "side").

GENERAL
- Vectorworks merges back-to-back MCP edits into one Cmd-Z step, so to undo
  exactly one tool call use vw_revert_last (works for all typed write tools).
- Objects are identified by UUID (stable across calls); never cache handles.
  Edits that rebuild an object return its new UUID.
- Vectorworks embeds Python 3.9 (2026) or 3.14 (2027): code sent to
  vw_exec_python must be 3.9-compatible. vw_vs_lookup gives exact
  signatures instantly; wrong vs.* calls come back as VSArgumentError
  (fix the call) instead of blocking Vectorworks with a dialog.
- Prefer typed tools over raw Python (raw edits can't be reverted).
- A request Vectorworks doesn't take within 15 s (dialog open, busy) is
  cancelled and never runs later; ask the user to close the dialog and
  retry. Long jobs that Vectorworks has started are waited for.
"""


def build_instructions(prof: dict | None = None) -> str:
    """Server instructions with the user's current profile."""
    from vwmcp import profile

    try:
        block = profile.instructions_block(prof)
    except Exception:  # noqa: BLE001 - a broken profile file must never stop the server
        block = profile.instructions_block(profile.defaults())
    return _HEAD + block + "\n" + _BODY


INSTRUCTIONS = build_instructions()

DISPLAY_NAME = "Keystone for Vectorworks"
HOMEPAGE = "https://github.com/seraphinernst4/vectorworks-2d-mcp"


def _version() -> str:
    from vwmcp import __version__

    return __version__


def _icons() -> list[Icon]:
    """The Keystone icon, inline (shown by clients that display server icons)."""
    try:
        png = resources.files("vwmcp").joinpath("assets", "icon.png").read_bytes()
    except (FileNotFoundError, OSError):
        return []
    return [Icon(src="data:image/png;base64," + base64.b64encode(png).decode("ascii"),
                 mime_type="image/png", sizes=["256x256"])]


class KeystoneServer(MCPServer):
    """MCPServer that appends the update note to every tool result while a
    newer Keystone is out (from the cache only - never waits for GitHub)."""

    QUIET = frozenset({"vw_update", "vw_ping"})  # they report the update themselves

    async def call_tool(self, name, arguments, context=None):
        result = await super().call_tool(name, arguments, context)
        if name in self.QUIET or not isinstance(result, CallToolResult):
            return result
        try:
            p = update.pending()
        except Exception:  # noqa: BLE001 - a tool result must never fail because of the update check
            return result
        if not p:
            return result
        note = TextContent(type="text", text=update.notice(p))
        return result.model_copy(update={"content": [*result.content, note]})


mcp = KeystoneServer(DISPLAY_NAME, title=DISPLAY_NAME, instructions=INSTRUCTIONS, website_url=HOMEPAGE,
                     icons=_icons() or None, version=_version())


@functools.cache
def get_bridge() -> Bridge:
    return Bridge()


@functools.cache
def get_journal() -> Journal:
    return Journal(get_bridge().root / "journal.jsonl")


def vw(fn: str, **kwargs):
    """Call a vwlib helper, mapping failures to MCP tool errors."""
    try:
        return calls.call(get_bridge(), fn, **kwargs)
    except calls.VWError as e:
        raise ToolError(f"{e}\n\n{e.traceback}".strip()) from e
    except BridgeError as e:
        raise ToolError(str(e)) from e


def vw2d(fn: str, timeout: float | None = None, **kwargs):
    """Call a vwlib2d helper (2D tools), mapping failures to MCP tool errors."""
    try:
        return calls.call2d(get_bridge(), fn, timeout=timeout, **kwargs)
    except calls.VWError as e:
        raise ToolError(f"{e}\n\n{e.traceback}".strip()) from e
    except BridgeError as e:
        raise ToolError(str(e)) from e


def bundled_index_text() -> str | None:
    """The vs.* signature index shipped with this server (data/vs_index.json)."""
    try:
        return resources.files("vwmcp").joinpath("data", "vs_index.json").read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None


@functools.cache
def vs_index() -> dict:
    """Parsed signature index: {name: {a, t, r, c, d, v?}}."""
    import json

    text = bundled_index_text()
    return json.loads(text)["f"] if text else {}


def bundled_bridge_source() -> str:
    """The in-app bridge shipped with this server (wheel or source checkout)."""
    packaged = resources.files("vwmcp").joinpath("_plugin", "vwmcp_bridge.py")
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    repo = Path(__file__).resolve().parents[2] / "vw-plugin" / "vwmcp" / "vwmcp_bridge.py"
    return repo.read_text(encoding="utf-8")
