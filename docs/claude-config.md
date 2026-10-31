# Claude configuration

The easiest way is the Claude Desktop extension
`release/Keystone-for-Vectorworks.mcpb` (double-click, Install). It needs no
configuration and keeps the bridge in Vectorworks up to date. To set the
server up by hand instead:

## Claude Desktop (manual, without the extension)

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(Windows: `%APPDATA%\Claude\claude_desktop_config.json`) and add an entry
under `mcpServers`. Don't use both the extension and a manual entry, or the
tools appear twice.

```json
{
  "mcpServers": {
    "Keystone for Vectorworks": {
      "command": "/opt/homebrew/bin/uvx",
      "args": ["--from", "https://github.com/seraphinernst4/vectorworks-2d-mcp/archive/refs/heads/main.zip", "vwmcp"],
      "env": {"VWMCP_AUTO_BRIDGE": "1"}
    }
  }
}
```

Use the full path to `uvx` (`which uvx` in Terminal); Claude Desktop doesn't
see your shell's PATH. Common locations: `/opt/homebrew/bin/uvx` (Homebrew),
`~/.local/bin/uvx` (uv installer; write it out as `/Users/<you>/.local/bin/uvx`).
Quit Claude Desktop before editing (it rewrites the file when it quits), then
start it again.

## Claude Code

```bash
claude mcp add --scope user keystone-vectorworks -e VWMCP_AUTO_BRIDGE=1 -- uvx --from https://github.com/seraphinernst4/vectorworks-2d-mcp/archive/refs/heads/main.zip vwmcp
```

Or in a project's `.mcp.json`:

```json
{
  "mcpServers": {
    "keystone-vectorworks": {
      "command": "uvx",
      "args": ["--from", "https://github.com/seraphinernst4/vectorworks-2d-mcp/archive/refs/heads/main.zip", "vwmcp"],
      "env": {"VWMCP_AUTO_BRIDGE": "1"}
    }
  }
}
```

## From a local checkout

Replace the `uvx` command with:

```json
"command": "uv",
"args": ["run", "--project", "/path/to/vectorworks-2d-mcp", "vwmcp"]
```

## Environment variables

Set these in the server's `env` block if you need them:

| Variable | Default | Meaning |
|---|---|---|
| `VWMCP_APP` | auto (the running Vectorworks) | Application AppleScript talks to, e.g. `Vectorworks 2027`; a preference – if it isn't running, whichever Vectorworks runs is used |
| `VWMCP_AUTO_BRIDGE` | off (on in the extension) | Install/update the bridge (and signature index) in the Plug-ins folder of **every** Vectorworks 2026+ at every start |
| `VWMCP_VW_VERSION` | empty | Extension setting: a fixed version (`2027`) sets `VWMCP_APP`; the bridge still goes into every version |
| `VWMCP_HOME` | `~/.vwmcp` | Job, result, journal and log folder |
| `VWMCP_CLAIM_TIMEOUT` | `15` | Seconds Vectorworks has to **take** a request (dialog open, busy); then it is cancelled and never runs later |
| `VWMCP_TIMEOUT` | `600` | Seconds to wait for the **result** once Vectorworks has taken the request (stops early if Vectorworks quits) |
| `VWMCP_MIN_UPTIME` | `20` | Seconds after launch before requests are sent (scripts during launch crash Vectorworks 2026) |
| `VWMCP_TRIGGER` | `menu` | `menu`: run the "MCP Run Job" menu command; `direct`: run the bridge via VectorScript `PythonExecute` (no menu command); `auto`: menu, switching to direct if Vectorworks repeatedly answers without taking the job |

The bridge in Vectorworks always uses `~/.vwmcp`; if you change
`VWMCP_HOME`, change the menu command to
`vwmcp_bridge.run_pending("/your/folder")` too.
