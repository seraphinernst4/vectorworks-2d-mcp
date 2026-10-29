"""Entry point: `vwmcp` runs the MCP server (stdio); `vwmcp install-plugin`
installs the in-app bridge."""

from __future__ import annotations

import argparse
import os

from vwmcp import install
from vwmcp.app import mcp
from vwmcp.tools import draw2d, generic  # noqa: F401  (registers tools)

if os.environ.get("VWMCP_SPOTLIGHT"):  # lighting tools from upstream, off by default
    from vwmcp.tools import spotlight  # noqa: F401


def main() -> None:
    parser = argparse.ArgumentParser(prog="vwmcp", description=__doc__)
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("install-plugin", help="copy the bridge into Vectorworks' Plug-ins folder")
    p.add_argument("--vw-version", default="2026")
    p.add_argument("--target", help="explicit folder (default: user Plug-ins/vwmcp)")
    args = parser.parse_args()

    if args.cmd == "install-plugin":
        from pathlib import Path

        target = Path(args.target) if args.target else install.plugin_dir(args.vw_version)
        print(install.install_plugin(target))
        return
    mcp.run("stdio")


if __name__ == "__main__":
    main()
