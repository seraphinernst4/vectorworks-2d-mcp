# Claude configuration

The installer does this for you. To do it by hand:

## Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(Windows: `%APPDATA%\Claude\claude_desktop_config.json`) and add a
`vectorworks` entry under `mcpServers`:

```json
{
  "mcpServers": {
    "vectorworks": {
      "command": "/opt/homebrew/bin/uvx",
      "args": ["--from", "https://github.com/seraphinernst4/vectorworks-2d-mcp/archive/refs/heads/main.zip", "vwmcp"]
    }
  }
}
```

Use the full path to `uvx` (`which uvx` in Terminal); Claude Desktop doesn't
see your shell's PATH. Common locations: `/opt/homebrew/bin/uvx` (Homebrew),
`~/.local/bin/uvx` (uv installer; write it out as `/Users/<you>/.local/bin/uvx`).
Restart Claude Desktop afterwards.

## Claude Code

```bash
claude mcp add --scope user vectorworks -- uvx --from https://github.com/seraphinernst4/vectorworks-2d-mcp/archive/refs/heads/main.zip vwmcp
```

Or in a project's `.mcp.json`:

```json
{
  "mcpServers": {
    "vectorworks": {
      "command": "uvx",
      "args": ["--from", "https://github.com/seraphinernst4/vectorworks-2d-mcp/archive/refs/heads/main.zip", "vwmcp"]
    }
  }
}
```

## From a local checkout

Replace the `uvx` command with:

```json
"command": "uv",
"args": ["run", "--project", "/path/to/vectorworks-mcp", "vwmcp"]
```

## Environment variables

Set these in the server's `env` block if you need them:

| Variable | Default | Meaning |
|---|---|---|
| `VWMCP_APP` | `Vectorworks 2026` | Application name AppleScript talks to |
| `VWMCP_HOME` | `~/.vwmcp` | Job, result, journal and log folder |
| `VWMCP_TIMEOUT` | `30` | Seconds to wait for Vectorworks per request |
| `VWMCP_MIN_UPTIME` | `30` | Seconds after launch before Claude may send requests |

The bridge in Vectorworks always uses `~/.vwmcp`; if you change
`VWMCP_HOME`, change the menu command to
`vwmcp_bridge.run_pending("/your/folder")` too.
