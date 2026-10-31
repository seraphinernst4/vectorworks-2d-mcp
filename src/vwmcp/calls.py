"""Call vwlib helpers inside Vectorworks through the bridge."""

from __future__ import annotations

import json
from importlib import resources

from vwmcp.client import Bridge

VWLIB_SOURCE = resources.files("vwmcp").joinpath("vwlib.py").read_text(encoding="utf-8")
VWLIB2D_SOURCE = resources.files("vwmcp").joinpath("vwlib2d.py").read_text(encoding="utf-8")

# Reloads an older bridge module that Vectorworks still holds in memory after
# the file in Plug-ins was updated (Vectorworks keeps Python state between runs).
RELOAD_CODE = """
import sys, importlib
_m = sys.modules.get("vwmcp.vwmcp_bridge")
result = None
if _m is not None:
    _m = importlib.reload(_m)
    result = getattr(_m, "BRIDGE_VERSION", None)
"""


class VWError(RuntimeError):
    """Python raised inside Vectorworks."""

    def __init__(self, message: str, traceback: str = "", stdout: str = ""):
        super().__init__(message)
        self.traceback = traceback or ""
        self.stdout = stdout or ""


def raise_for_result(res: dict) -> dict:
    if not res.get("ok"):
        raise VWError(res.get("error") or "unknown error", res.get("traceback"), res.get("stdout"))
    return res


def _run_with_libs(bridge: Bridge, names_sources: list[tuple[str, str]], body: str,
                   timeout: float | None = None) -> dict:
    """Run body with helper libraries loaded first: by reference (compiled once
    per Vectorworks session) when the bridge supports it, else inline."""
    if bridge.supports_libs():
        libs = [bridge.lib_ref(n, s) for n, s in names_sources]
        return bridge.run(body, timeout=timeout, libs=libs)
    code = "\n".join(s for _, s in names_sources) + "\n" + body
    res = bridge.run(code, timeout=timeout)
    maybe_reload(bridge)
    return res


def maybe_reload(bridge: Bridge) -> None:
    """If Vectorworks answered with an older bridge than the one installed
    with this server, reload it once (no Vectorworks restart needed)."""
    from vwmcp import app  # late: app imports this module

    if bridge.supports_libs() or getattr(bridge, "_reload_tried", False):
        return
    if bridge.bridge_version in (None, app.EXPECTED_BRIDGE_VERSION):
        return
    bridge._reload_tried = True
    try:
        res = bridge.run(RELOAD_CODE, timeout=30, check=False)
        if res.get("ok") and res.get("value"):
            bridge.bridge_version = res["value"]
            bridge.log(f"reloaded the bridge in Vectorworks: now {res['value']}")
    except Exception as e:  # noqa: BLE001 - an update attempt must not break the call
        bridge.log(f"bridge reload failed: {e}")


def call(bridge: Bridge, fn: str, **kwargs):
    """Run vwlib.<fn>(**kwargs) in Vectorworks and return its value."""
    # json.dumps -> repr gives a Python string literal, safe for any content.
    body = (
        "import json as _json\n"
        f"result = {fn}(**_json.loads({json.dumps(kwargs)!r}))\n"
    )
    res = _run_with_libs(bridge, [("vwlib", VWLIB_SOURCE)], body)
    return raise_for_result(res)["value"]


def call2d(bridge: Bridge, fn: str, timeout: float | None = None, **kwargs):
    """Run vwlib2d.<fn>(**kwargs) (with vwlib loaded first) in Vectorworks.
    Results travel as a JSON string so floats round-trip bit-exactly."""
    body = (
        "import json as _json\n"
        f"result = _json.dumps({fn}(**_json.loads({json.dumps(kwargs)!r})))\n"
    )
    res = _run_with_libs(bridge, [("vwlib", VWLIB_SOURCE), ("vwlib2d", VWLIB2D_SOURCE)], body,
                         timeout=timeout)
    value = raise_for_result(res)["value"]
    return json.loads(value) if isinstance(value, str) else value
