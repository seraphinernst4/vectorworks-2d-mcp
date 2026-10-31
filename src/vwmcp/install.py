"""Install the in-app bridge into Vectorworks' user Plug-ins folders."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from vwmcp import app

MENU_SCRIPT = "from vwmcp import vwmcp_bridge\nvwmcp_bridge.run_pending()\n"
INIT_TEXT = '"""Keystone for Vectorworks bridge package (see vwmcp_bridge.py)."""\n'
MIN_YEAR = 2026


def support_dir(platform: str = sys.platform, home: Path | None = None,
                appdata: Path | None = None) -> Path:
    if platform == "darwin":
        return (home or Path.home()) / "Library/Application Support/Vectorworks"
    if platform == "win32":
        return (appdata or Path(os.environ["APPDATA"])) / "Nemetschek/Vectorworks"
    raise SystemExit(f"Vectorworks does not run on {platform}")


def plugin_dir(
    vw_version: str = "2026",
    platform: str = sys.platform,
    home: Path | None = None,
    appdata: Path | None = None,
) -> Path:
    return support_dir(platform, home, appdata) / vw_version / "Plug-ins" / "vwmcp"


def installed_versions(platform: str = sys.platform, home: Path | None = None) -> list[str]:
    """Vectorworks versions (>= 2026) that have run at least once on this
    computer (their user folder exists), newest first."""
    base = support_dir(platform, home)
    if not base.is_dir():
        return []
    years = [p.name for p in base.iterdir()
             if re.fullmatch(r"20\d\d", p.name) and int(p.name) >= MIN_YEAR and p.is_dir()]
    return sorted(years, reverse=True)


def _files() -> dict[str, str]:
    files = {"__init__.py": INIT_TEXT, "vwmcp_bridge.py": app.bundled_bridge_source()}
    index = app.bundled_index_text()
    if index:
        files["vs_index.json"] = index
    return files


def _write(target: Path, files: dict[str, str]) -> bool:
    """Write files that differ (atomically). True if anything changed."""
    target.mkdir(parents=True, exist_ok=True)
    changed = False
    for name, text in files.items():
        dest = target / name
        try:
            if dest.is_file() and dest.read_text(encoding="utf-8") == text:
                continue
        except (OSError, UnicodeDecodeError):
            pass
        tmp = target / f".{name}.{os.getpid()}.tmp"
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, dest)
        changed = True
    return changed


def install_plugin(target: Path) -> str:
    _write(target, _files())
    return (
        f"Installed vwmcp bridge {app.EXPECTED_BRIDGE_VERSION} to {target}\n\n"
        "If you haven't yet, create the 'MCP Run Job' menu command once in Vectorworks "
        "(Tools > Plug-ins > Plug-in Manager > New > Command, Language: Python) with:\n\n"
        + MENU_SCRIPT
    )


def ensure_plugin(vw_version: str | None = None, home: Path | None = None,
                  platform: str = sys.platform) -> str | None:
    """Install or update the bridge (and the signature index) in every
    Vectorworks version on this computer, or only in vw_version if given.
    Used by the Claude Desktop extension at every start. Writes only what
    changed; returns a message, or None when everything is up to date."""
    versions = installed_versions(platform, home)
    if vw_version and re.fullmatch(r"20\d\d", vw_version):
        versions = [v for v in versions if v == vw_version]
        if not versions:
            return f"Vectorworks {vw_version} not found ({support_dir(platform, home)}); bridge not installed"
    if not versions:
        return f"No Vectorworks {MIN_YEAR} or newer found in {support_dir(platform, home)}; bridge not installed"
    files = _files()
    done = [v for v in versions if _write(plugin_dir(v, platform=platform, home=home), files)]
    if not done:
        return None
    return f"Installed vwmcp bridge {app.EXPECTED_BRIDGE_VERSION} for Vectorworks {', '.join(done)}"
