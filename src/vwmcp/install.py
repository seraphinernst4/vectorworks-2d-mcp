"""Install the in-app bridge into a Vectorworks user Plug-ins folder."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from vwmcp import app

MENU_SCRIPT = "from vwmcp import vwmcp_bridge\nvwmcp_bridge.run_pending()\n"


def plugin_dir(
    vw_version: str = "2026",
    platform: str = sys.platform,
    home: Path | None = None,
    appdata: Path | None = None,
) -> Path:
    if platform == "darwin":
        home = home or Path.home()
        base = home / "Library/Application Support/Vectorworks"
    elif platform == "win32":
        appdata = appdata or Path(os.environ["APPDATA"])
        base = appdata / "Nemetschek/Vectorworks"
    else:
        raise SystemExit(f"Vectorworks does not run on {platform}")
    return base / vw_version / "Plug-ins" / "vwmcp"


def install_plugin(target: Path) -> str:
    target.mkdir(parents=True, exist_ok=True)
    (target / "__init__.py").write_text(
        '"""vectorworks-mcp bridge package (see vwmcp_bridge.py)."""\n', encoding="utf-8"
    )
    (target / "vwmcp_bridge.py").write_text(app.bundled_bridge_source(), encoding="utf-8")
    return (
        f"Installed vwmcp bridge {app.EXPECTED_BRIDGE_VERSION} to {target}\n\n"
        "If you haven't yet, create the 'MCP Run Job' menu command once in Vectorworks "
        "(Tools > Plug-ins > Plug-in Manager > New > Command, Language: Python) with:\n\n"
        + MENU_SCRIPT
    )


def ensure_plugin(vw_version: str = "2026", home: Path | None = None,
                  platform: str = sys.platform) -> str | None:
    """Install or update the bridge if Vectorworks <vw_version> exists on this
    Mac (used by the Claude Desktop extension at every start). Writes only
    when something changed; returns a message, or None when up to date."""
    target = plugin_dir(vw_version, platform=platform, home=home)
    if not target.parent.parent.is_dir():  # ~/Library/Application Support/Vectorworks/<ver>
        return f"Vectorworks {vw_version} not found ({target.parent.parent}); bridge not installed"
    src = app.bundled_bridge_source()
    cur = target / "vwmcp_bridge.py"
    if cur.is_file() and (target / "__init__.py").is_file() and cur.read_text(encoding="utf-8") == src:
        return None
    return install_plugin(target).splitlines()[0]
