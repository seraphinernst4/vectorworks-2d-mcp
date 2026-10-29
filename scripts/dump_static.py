"""Write the handshake answers of the real server to static.json.

The extension launcher (mcpb/server/index.js) answers initialize and the
first tools/list from this file at once, while the Python server starts.
Run by scripts/build-mcpb.sh:  python scripts/dump_static.py <src> <out.json>
"""

import json
import os
import subprocess
import sys

from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS

src, out = sys.argv[1], sys.argv[2]
env = {k: v for k, v in os.environ.items() if not k.startswith("VWMCP_")}
code = f"import sys; sys.path.insert(0, {os.path.abspath(src)!r}); sys.argv = ['vwmcp']; from vwmcp.server import main; main()"
proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, text=True)


def call(msg):
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    if "id" not in msg:
        return None
    for line in proc.stdout:
        reply = json.loads(line)
        if reply.get("id") == msg["id"] and "method" not in reply:
            if "error" in reply:
                raise SystemExit(f"{msg['method']}: {reply['error']}")
            return reply["result"]
    raise SystemExit(f"server closed before answering {msg['method']}")


try:
    init = call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": HANDSHAKE_PROTOCOL_VERSIONS[-1], "capabilities": {},
        "clientInfo": {"name": "build", "version": "0"}}})
    call({"jsonrpc": "2.0", "method": "notifications/initialized"})
    tools = call({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
finally:
    proc.stdin.close()
    proc.wait(timeout=20)

init.pop("protocolVersion", None)
data = {"protocolVersions": list(HANDSHAKE_PROTOCOL_VERSIONS), "initialize": init, "tools": tools["tools"]}
with open(out, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
print(f"static.json: {init['serverInfo']['name']} {init['serverInfo'].get('version')}, {len(tools['tools'])} tools")
