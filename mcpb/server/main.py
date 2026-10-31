"""Keystone for Vectorworks – entry point of the Claude Desktop extension.

Claude Desktop runs this with uv. The server code (package vwmcp) sits next
to this file; at every start the bridge in Vectorworks' Plug-ins folder is
installed or updated in every Vectorworks version found (VWMCP_AUTO_BRIDGE).
Which Vectorworks to talk to is detected (the running one); a fixed version
can be set in the extension settings.
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

version = (os.environ.get("VWMCP_VW_VERSION") or "").strip()
if re.fullmatch(r"20\d\d", version):  # a fixed version from the settings
    os.environ["VWMCP_APP"] = f"Vectorworks {version}"
else:  # empty or an unsubstituted ${...}: use whichever Vectorworks runs
    os.environ.pop("VWMCP_VW_VERSION", None)
    os.environ.pop("VWMCP_APP", None)
trigger = (os.environ.get("VWMCP_TRIGGER") or "").strip().lower()
if trigger not in ("direct", "menu", "auto"):  # empty or an unsubstituted ${...}
    os.environ.pop("VWMCP_TRIGGER", None)   # default: direct
os.environ.setdefault("VWMCP_AUTO_BRIDGE", "1")

from vwmcp.server import main  # noqa: E402

sys.argv = sys.argv[:1]
main()
