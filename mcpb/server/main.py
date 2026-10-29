"""Keystone for Vectorworks – entry point of the Claude Desktop extension.

Claude Desktop runs this with uv. The server code (package vwmcp) sits next
to this file; at every start the bridge in Vectorworks' Plug-ins folder is
installed or updated (VWMCP_AUTO_BRIDGE).
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

version = (os.environ.get("VWMCP_VW_VERSION") or "").strip()
if not re.fullmatch(r"20\d\d", version):  # empty or an unsubstituted ${...}
    version = "2026"
os.environ["VWMCP_VW_VERSION"] = version
os.environ.setdefault("VWMCP_APP", f"Vectorworks {version}")
os.environ.setdefault("VWMCP_AUTO_BRIDGE", "1")

from vwmcp.server import main  # noqa: E402

sys.argv = sys.argv[:1]
main()
