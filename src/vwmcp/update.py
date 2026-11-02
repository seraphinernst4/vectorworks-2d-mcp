"""Is there a newer Keystone, and get it.

The released version is the one on GitHub's main branch (server.json). The
answer is cached in ~/.vwmcp/update.json and asked again after a few hours
(a background thread at server start keeps it fresh, so vw_ping stays fast).
Downloads go through curl (macOS' own certificates; Python builds without
system certificates fail on HTTPS), with urllib as a fallback. Set
VWMCP_UPDATE_CHECK=0 to switch the check off. Nothing about the user or the
drawing is sent: it is a plain download of two public files.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from vwmcp import __version__

REPO = "seraphinernst4/vectorworks-2d-mcp"
VERSION_URL = f"https://raw.githubusercontent.com/{REPO}/main/server.json"
MCPB_URL = f"https://github.com/{REPO}/raw/main/release/Keystone-for-Vectorworks.mcpb"
MAX_AGE_S = 6 * 3600
FILE_NAME = "Keystone-for-Vectorworks-{version}.mcpb"
PACKAGE_NAME = "keystone-vectorworks"  # manifest name of the extension


def _home() -> Path:
    return Path(os.environ.get("VWMCP_HOME", Path.home() / ".vwmcp"))


def _cache_path() -> Path:
    return _home() / "update.json"


def enabled() -> bool:
    return (os.environ.get("VWMCP_UPDATE_CHECK") or "1").strip().lower() not in ("0", "off", "false", "no")


def parse(v: str | None) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])


def newer(latest: str | None, current: str = __version__) -> bool:
    return bool(latest) and parse(latest) > parse(current)


def _fetch(url: str, dest: Path | None = None, timeout: float = 5.0) -> bytes | None:
    """GET url (to dest, or returned as bytes). Raises OSError on failure."""
    curl = shutil.which("curl")
    if curl:
        cmd = [curl, "-fsSL", "--max-time", str(int(max(timeout, 1)))] + (["-o", str(dest)] if dest else []) + [url]
        r = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
        if r.returncode != 0:
            raise OSError("download failed (%s): %s" % (r.returncode, r.stderr.decode("utf-8", "replace")[:200]))
        return None if dest else r.stdout
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (fixed https URL)
        data = resp.read()
    if dest:
        dest.write_bytes(data)
        return None
    return data


def check(force: bool = False, timeout: float = 4.0) -> dict:
    """{"current", "latest", "update_available", "checked"} - from the cache
    when it is fresh, else asked on GitHub. Never raises."""
    out: dict = {"current": __version__}
    if not enabled():
        out["disabled"] = True
        return out
    path = _cache_path()
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = {}
    if not force and cached.get("latest") and time.time() - float(cached.get("checked_ts", 0)) < MAX_AGE_S:
        latest = cached["latest"]
    else:
        try:
            latest = json.loads(_fetch(VERSION_URL, timeout=timeout) or b"{}").get("version")
            if not latest:
                raise ValueError("no version in server.json")
            cached = {"latest": latest, "checked_ts": time.time()}
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(cached), encoding="utf-8")
            except OSError:
                pass
        except Exception as e:  # noqa: BLE001 - offline, GitHub down: just say so
            out["error"] = "could not ask GitHub for the newest version: %s" % str(e)[:160]
            latest = cached.get("latest")
    out["latest"] = latest
    out["update_available"] = newer(latest)
    if cached.get("checked_ts"):
        out["checked"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(cached["checked_ts"])))
    return out


def refresh_in_background() -> None:
    """Ask once at server start, without delaying it."""
    if enabled():
        threading.Thread(target=check, kwargs={"timeout": 8.0}, daemon=True, name="keystone-update").start()


def downloads_dir() -> Path:
    d = Path.home() / "Downloads"
    return d if d.is_dir() else _home()


def download(dest_dir: Path | None = None, timeout: float = 180.0) -> dict:
    """Fetch the newest extension file, check it is a Keystone package and
    which version it is; returns {"file", "version", "bytes"}."""
    dest_dir = Path(dest_dir) if dest_dir else downloads_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / ".Keystone-for-Vectorworks.download"
    try:
        _fetch(MCPB_URL, tmp, timeout=timeout)
        with zipfile.ZipFile(tmp) as z:
            man = json.loads(z.read("manifest.json").decode("utf-8"))
        if man.get("name") != PACKAGE_NAME:
            raise OSError("the downloaded file is not the Keystone for Vectorworks extension")
        version = str(man.get("version") or "")
        if not version:
            raise OSError("the downloaded extension has no version")
        final = dest_dir / FILE_NAME.format(version=version)
        os.replace(tmp, final)
        return {"file": str(final), "version": version, "bytes": final.stat().st_size}
    except (zipfile.BadZipFile, KeyError, ValueError) as e:
        raise OSError("the downloaded file is not a valid extension: %s" % e) from None
    finally:
        if tmp.exists():
            tmp.unlink()


def open_installer(path: str) -> bool:
    """Open the .mcpb with Claude Desktop (macOS): it shows its install /
    update dialog. False where that isn't possible."""
    if sys.platform != "darwin":
        return False
    r = subprocess.run(["open", path], capture_output=True, timeout=30)
    return r.returncode == 0
