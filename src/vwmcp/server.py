"""Entry point: `vwmcp` runs the MCP server (stdio); `vwmcp install-plugin`
installs the in-app bridge."""

from __future__ import annotations

import argparse
import os

from vwmcp import install
from vwmcp.app import mcp
from vwmcp.tools import assets, draw2d, generic, organize  # noqa: F401  (registers tools)

if os.environ.get("VWMCP_SPOTLIGHT"):  # lighting tools from upstream, off by default
    from vwmcp.tools import spotlight  # noqa: F401


def main() -> None:
    parser = argparse.ArgumentParser(prog="vwmcp", description=__doc__)
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("install-plugin", help="copy the bridge into Vectorworks' Plug-ins folder")
    p.add_argument("--vw-version", default=None, help="e.g. 2027 (default: every version found)")
    p.add_argument("--target", help="explicit folder (default: user Plug-ins/vwmcp)")
    args = parser.parse_args()

    if args.cmd == "install-plugin":
        from pathlib import Path

        if args.target:
            print(install.install_plugin(Path(args.target)))
        else:
            print(install.ensure_plugin(args.vw_version) or "Bridge is up to date.")
            print("\nNothing else to set up in Vectorworks (the menu command 'MCP Run Job' is only "
                  "needed with VWMCP_TRIGGER=menu).")
        return
    if os.environ.get("VWMCP_AUTO_BRIDGE"):
        # Claude Desktop extension: keep the bridge in Vectorworks up to date.
        import sys

        try:
            # every installed version (the version setting only picks which
            # Vectorworks to talk to; an upgrade to 2027 must get the bridge too)
            msg = install.ensure_plugin(None)
            if msg:
                print(msg, file=sys.stderr)
        except (Exception, SystemExit) as e:  # never block the server
            print(f"bridge install skipped: {e}", file=sys.stderr)
    mcp.run("stdio")


if __name__ == "__main__":
    main()
