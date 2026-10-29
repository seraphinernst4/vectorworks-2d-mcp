"""Call vwlib helpers inside Vectorworks through the bridge."""

from __future__ import annotations

import json
from importlib import resources

from vwmcp.client import Bridge

VWLIB_SOURCE = resources.files("vwmcp").joinpath("vwlib.py").read_text(encoding="utf-8")
VWLIB2D_SOURCE = resources.files("vwmcp").joinpath("vwlib2d.py").read_text(encoding="utf-8")


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


def call(bridge: Bridge, fn: str, **kwargs):
    """Run vwlib.<fn>(**kwargs) in Vectorworks and return its value."""
    # json.dumps -> repr gives a Python string literal, safe for any content.
    code = (
        f"{VWLIB_SOURCE}\n"
        "import json as _json\n"
        f"result = {fn}(**_json.loads({json.dumps(kwargs)!r}))\n"
    )
    return raise_for_result(bridge.run(code))["value"]


def call2d(bridge: Bridge, fn: str, timeout: float | None = None, **kwargs):
    """Run vwlib2d.<fn>(**kwargs) (with vwlib loaded first) in Vectorworks.
    Results travel as a JSON string so floats round-trip bit-exactly."""
    code = (
        f"{VWLIB_SOURCE}\n{VWLIB2D_SOURCE}\n"
        "import json as _json\n"
        f"result = _json.dumps({fn}(**_json.loads({json.dumps(kwargs)!r})))\n"
    )
    value = raise_for_result(bridge.run(code, timeout=timeout))["value"]
    return json.loads(value) if isinstance(value, str) else value
