# Vectorworks MCP

<!-- mcp-name: io.github.giorgiomoroder/vectorworks-mcp -->

> **Beta.** Tested on Vectorworks 2026 (Update 3, English) with Spotlight on
> macOS only. Other versions, languages and Windows may not work yet. Save
> your file before asking for edits, and please
> [report what you find](https://github.com/giorgiomoroder/vectorworks-mcp/issues/new/choose).
> Working under an NDA? Read [Your drawing data](#your-drawing-data-and-ndas) first.

Let Claude work inside your open Vectorworks drawing. Spotlight first:
read and bulk-edit Lighting Devices, check positions, pull paperwork, and
run any Vectorworks Python when you need more.

- **Mac-native.** Pure Python, no C++ SDK, no compiling. Built and tested on
  macOS with Vectorworks 2026.
- **Spotlight-first.** Channels, addresses, purposes, colors, gobos, positions.
- **Safe edits.** Every bulk edit is validated before anything changes, and
  `vw_revert_last` undoes exactly the last thing Claude did.

> Example asks, once it's set up:
> - "List every device on the 1E with its channel and color."
> - "Which devices have no position? Which position names have no position object?"
> - "Renumber channels on FOH TRUSS 101 to 127 in unit-number order."
> - "Move FOH TRUSS units 1–6 to universe 2, starting at address 1."
> - "Give me a channel hookup as CSV on my Desktop."
> - "Undo that last change."

---

## Install (about 10 minutes, macOS)

You need: Vectorworks 2026, and [Claude Desktop](https://claude.ai/download)
and/or [Claude Code](https://claude.com/claude-code).

### 1. Run the installer (2 minutes)

Open **Terminal** (Applications > Utilities) and paste:

```bash
curl -LsSf https://raw.githubusercontent.com/giorgiomoroder/vectorworks-mcp/main/scripts/install-macos.sh | bash
```

It installs [uv](https://docs.astral.sh/uv/) if needed, copies the small
bridge script into your Vectorworks Plug-ins folder
(`~/Library/Application Support/Vectorworks/2026/Plug-ins/vwmcp/`), and adds
Vectorworks to Claude Desktop and Claude Code. Your existing Claude Desktop
settings are kept (a backup is saved next to them).

### 2. Create the "MCP Run Job" menu command in Vectorworks (once, 2 minutes)

Claude reaches into Vectorworks through one menu command. Vectorworks only
allows plug-ins like this to be made in its Plug-in Manager:

1. **Tools ▸ Plug-ins ▸ Plug-in Manager…** ▸ **Custom Plug-ins** tab ▸ **New…**
2. Choose **Command**, name it exactly **`MCP Run Job`**, click **OK**.
3. With it selected, click **Edit Script…**, set **Language: Python**, and paste:

   ```python
   from vwmcp import vwmcp_bridge
   vwmcp_bridge.run_pending()
   ```

   Click **OK**, then **Close**.
4. **Tools ▸ Workspaces ▸ Edit Current Workspace…** ▸ **Menus** tab. Find
   **MCP Run Job** in the left list (under *Miscellaneous*) and drag it into
   any menu on the right, e.g. **Tools**. Click **OK**.

Test it: choose **Tools ▸ MCP Run Job**. Nothing visible happens, and no
error appears. That's correct; there's no work queued yet.

### 3. Restart Claude and say hello (1 minute)

Quit and reopen Claude Desktop. With a drawing open in Vectorworks, ask:

> Ping Vectorworks.

The first time, macOS asks whether Claude may control Vectorworks.
Click **Allow**. You should get back your Vectorworks version and the name
of your open drawing.

---

## Tools

| Tool | What it does |
|---|---|
| `vw_ping` | Bridge alive? Vectorworks version, open document, bridge version check |
| `vw_document_info` | Units, active layer/class, counts, selection size |
| `vw_list_layers`, `vw_list_classes` | Layers (design/sheet, visibility) and classes |
| `vw_get_selection` | Selected objects: type, name, class, layer, bbox, records |
| `vw_find_objects` | Find by class, layer, type, plug-in, record field value, name |
| `vw_get_record`, `vw_set_record` | Read/write record fields or plug-in parameters |
| `vw_list_symbols`, `vw_insert_symbol` | Symbol definitions by name/folder; place one at x, y(, z) |
| `vw_list_lighting_devices` | Every Lighting Device: unit, channel, address, purpose, position, type, color, gobo, focus… |
| `vw_set_lighting_device_fields` | Bulk edit by position + unit number, channel, or uuid |
| `vw_list_positions` | Positions with their devices, plus unassigned devices and orphan position names |
| `vw_lighting_device_report` | Paperwork export (CSV or JSON), naturally sorted |
| `vw_revert_last` | Undo exactly the last Claude edit in this drawing (repeatable) |
| `vw_exec_python` | Run any Vectorworks Python (`vs.*`) and get stdout, value, traceback |
| `vw_vs_lookup` | Search the `vs.*` API of *your* Vectorworks, with signatures |

## Your drawing data and NDAs

**What leaves your Mac:** whatever Claude reads from your drawing, plus
whatever you type, is sent to Anthropic as part of your Claude conversation.
That includes fixture lists, channel and address data, position and layer
names, venue and show names, and anything else the tools return. The
`.vwx` file itself is never uploaded, but Claude can read most of what's in
it if you ask.

**What stays local:** the bridge runs entirely on your Mac. Requests and
results pass through `~/.vwmcp/`, and nothing in this project sends data
anywhere except through your Claude app.

**If a project is under NDA or a client confidentiality clause:**

- Check whether your agreement allows project data to be processed by a
  third-party AI service. Many NDAs don't address it, and some forbid it.
  When in doubt, ask the client or producer first.
- Your Claude plan and privacy settings govern how Anthropic stores
  conversations and whether they may be used for model training. Review them
  at [claude.ai/settings](https://claude.ai/settings) and in
  [Anthropic's privacy policy](https://www.anthropic.com/legal/privacy).
  Team and Enterprise plans have different terms from personal plans.
- Limit what Claude sees: ask about one position or layer rather than the
  whole drawing, and prefer report tools that return only the columns you
  need.
- Don't use it on a confidential drawing if you can't share that drawing's
  contents with Anthropic.

## Good to know

- **Undo.** Vectorworks lumps back-to-back Claude edits into a single
  Cmd-Z, so Cmd-Z may undo several of them at once (never your own work).
  To undo exactly one Claude edit, ask Claude to "undo that" (`vw_revert_last`).
  This works for all typed edit tools. Changes made with raw Python
  (`vw_exec_python`) can only be undone with Cmd-Z.
- **Bulk edits are all-or-nothing.** If any device in a bulk edit can't be
  found or any field name is wrong, nothing changes. Unit numbers repeat per
  position, so give the position too.
- **Addresses.** Spotlight treats *UniverseAddress* (e.g. `2/101`) as the
  source of truth. Edits to Universe or DMX Address are applied through it
  automatically, and the tool re-reads each device afterwards and reports
  anything Spotlight overrode.
- **Dialogs block Claude.** If a Vectorworks dialog is open, Claude's request
  waits and then times out with a message saying so. Close the dialog and ask again.
- **Just launched?** Claude waits until Vectorworks has been open ~30 seconds.
  Scripts sent while Vectorworks is still starting can crash it.
- **Log file.** Bridge errors and timeouts are logged to `~/.vwmcp/vwmcp.log`.

## Troubleshooting

When reporting a problem, please use the
[bug report form](https://github.com/giorgiomoroder/vectorworks-mcp/issues/new/choose).

| Symptom | Fix |
|---|---|
| "did not pick up the job" | Open a drawing, close any dialog, and check **MCP Run Job** is in a menu (step 2, part 4). Run it once by hand; it should do nothing silently. |
| "is not running" / "still starting" | Open Vectorworks and wait a few seconds. |
| A "Script Errors" window in Vectorworks | Usually raw Python calling a `vs` function with the wrong arguments. Click OK; ask Claude to check the signature with `vw_vs_lookup`. |
| `vw_ping` warns about bridge version | Re-run the installer (or `uvx --from git+https://github.com/giorgiomoroder/vectorworks-mcp vwmcp install-plugin`). |
| Nothing happens, no macOS permission prompt | System Settings ▸ Privacy & Security ▸ Automation: allow Claude (or Terminal) to control Vectorworks 2026. |
| `vw_ping` warns the Vectorworks version is untested | It may work; save first, check results, and please open an issue either way. |
| Different Vectorworks version | `VW_VERSION=2025` before the installer command, and set `VWMCP_APP="Vectorworks 2025"` in the server's environment. Only 2026 is tested. |

## Manual configuration

See [docs/claude-config.md](docs/claude-config.md) for the Claude Desktop and
Claude Code config blocks, and the environment variables.

## How it works

```
Claude ──MCP──▶ vwmcp server ──writes──▶ ~/.vwmcp/jobs/<id>.json
                     │
                     └─AppleScript: run VectorScript "DoMenuTextByName('MCP Run Job')"
                                          │
Vectorworks ◀────────────────────────────┘
  MCP Run Job ▶ vwmcp_bridge.run_pending(): exec job with `vs` in scope
              ▶ ~/.vwmcp/results/<id>.json ──▶ server ──▶ Claude
```

The in-app bridge is a ~120-line "dumb executor" and rarely changes. All
logic, including the helper library run inside Vectorworks, is sent from the
server with each request. Details and the reasons for this design are in
[docs/architecture.md](docs/architecture.md); `vs.*` findings are in [docs/vs-notes.md](docs/vs-notes.md).

## Windows

Experimental and untested. `scripts/install-windows.ps1` installs the bridge
and registers Claude Desktop, but there's no automatic trigger yet: run
**MCP Run Job** yourself to process each request. Contributions welcome.

## Development

```bash
uv sync
uv run pytest            # bridge round-trips with a fake Vectorworks
uv run vwmcp             # run the server on stdio
```

Code sent into Vectorworks (`src/vwmcp/vwlib.py`, `vw-plugin/`) must stay
**Python 3.9**-compatible: that's what Vectorworks 2026 embeds.

Nothing from the Vectorworks install (stubs, SDK headers, libraries, sample
files) is included. Bring your own licensed Vectorworks.

## Related projects

[vicquick/vwx-mcp](https://github.com/vicquick/vwx-mcp) and
[SRVR-JOE/showcad-mcp](https://github.com/SRVR-JOE/showcad-mcp) (Windows,
native palette), [BhaveshY/vectorworks-mcp](https://github.com/BhaveshY/vectorworks-mcp)
(Windows, architecture), [mako-357/vectorworks-mcp](https://github.com/mako-357/vectorworks-mcp)
(C++ SDK).

## License

MIT
