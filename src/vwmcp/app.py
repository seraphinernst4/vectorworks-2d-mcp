"""Shared MCP server instance and bridge access for tool modules."""

from __future__ import annotations

import functools
from importlib import resources
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from vwmcp import calls
from vwmcp.client import Bridge, BridgeError
from vwmcp.journal import Journal

EXPECTED_BRIDGE_VERSION = "0.1.0"
# Vectorworks major versions this release was tested against (31 = 2026).
TESTED_VW_MAJORS = {31: "2026"}

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
DELETE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)

INSTRUCTIONS = """\
Tools for a live Vectorworks session. Every call runs inside the user's open
document via the "MCP Run Job" menu command.

EXACT 2D DRAWING (vw2d_* tools) - rules for architectural drawings:
- Exactness first. Give coordinates in document units (or pass unit="mm"/"m";
  conversion and relative point chains use exact decimal arithmetic). Never
  eyeball or round. Arcs are true arcs (path segments with arc_to), not
  segmented polylines. Every drawing tool reads back what Vectorworks stored
  and reports the deviation; it must be 0 (exact).
- Adjacent areas share their edges exactly: no gaps, no overlaps, no
  stacked fills, no double lines. Parts that belong together (one ring, one
  slab) are ONE closed area (use vw2d_boolean union), not several pieces.
  Never lose edges when cleaning up (keep every step/edge of the source).
- Organisation: every object goes into a meaningful class (never the
  none class "Keine"/"None"); graphic attributes come from the class (the
  default), explicit attributes only when needed. Design layers separate
  floors/drawing areas (with the right scale); classes separate building
  parts (walls, openings, stairs, furniture, dimensions, text...). Check the
  existing structure first (vw2d_structure) and follow it; create missing
  classes/layers with vw2d_setup instead of drawing into the wrong ones.
- Workflow: 1) vw2d_structure / vw2d_read to understand the drawing,
  2) draw/edit, 3) vw2d_check (tolerance_mm ~1, compare per layer, ignore
  classes that overlap on purpose), 4) vw2d_fix (snap, close, clean,
  duplicates, overlaps) and check again until there are no errors,
  5) show the check image / vw2d_select so the user sees the result.
- Work in the document that is open; 2D tasks stay 2D.

GENERAL
- Vectorworks merges back-to-back MCP edits into one Cmd-Z step, so to undo
  exactly one tool call use vw_revert_last (works for all typed write tools).
- Objects are identified by UUID (stable across calls); never cache handles.
  Edits that rebuild an object return its new UUID.
- Vectorworks embeds Python 3.9: code sent to vw_exec_python must be 3.9-
  compatible. Use vw_vs_lookup to find vs.* functions and signatures.
- Prefer typed tools over raw Python (raw edits can't be reverted).
"""

mcp = MCPServer("vectorworks", instructions=INSTRUCTIONS)


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


def bundled_bridge_source() -> str:
    """The in-app bridge shipped with this server (wheel or source checkout)."""
    packaged = resources.files("vwmcp").joinpath("_plugin", "vwmcp_bridge.py")
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    repo = Path(__file__).resolve().parents[2] / "vw-plugin" / "vwmcp" / "vwmcp_bridge.py"
    return repo.read_text(encoding="utf-8")
