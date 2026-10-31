#!/usr/bin/env python3
"""Live test of Keystone against a running Vectorworks (macOS).

    python scripts/live_test.py [--src <repo>/src] [--only protocol,tools]

Uses the real path: job folder -> AppleScript -> "MCP Run Job" -> bridge.
Sends one request at a time; don't work in Vectorworks while it runs
(about 2 minutes) and keep an empty, unsaved test document in front.
Writes ~/.vwmcp/live_report.json and logs to ~/.vwmcp/live_test.log.

Protocol tests:
  ping x10        round-trip times (claim / total / inside Vectorworks)
  libs            helper libraries by reference: first vs. cached call
  busy_cancel     Vectorworks busy (like an open dialog) -> the second request
                  is cancelled after the claim timeout and must never run later
  long_job        a job running longer than the claim timeout is waited for
  arg_check       vs.Rect(0, 0, 1) -> clean error, nothing drawn, no dialog
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(os.environ.get("VWMCP_HOME", Path.home() / ".vwmcp"))
LOG = ROOT / "live_test.log"
REPORT = ROOT / "live_report.json"


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def wait_idle(limit: float = 30.0) -> None:
    """Wait until no job is queued or running (e.g. the job that started us)."""
    end = time.time() + limit
    while time.time() < end:
        busy = [p for p in (ROOT / "jobs").glob("*") if p.suffix in (".json", ".working", ".running")]
        if not busy:
            return
        time.sleep(0.5)


def count_objects(b) -> int:
    code = "_n = []\ndef _c(h):\n    _n.append(1)\nvs.ForEachObject(_c, '(ALL)')\nlen(_n)"
    return b.run(code)["value"]


def protocol_tests(report: dict) -> None:
    from vwmcp import calls
    from vwmcp.client import Bridge, BridgeTimeout

    b = Bridge(root=ROOT)
    # ping x10
    times = []
    for _ in range(10):
        res = b.run("vs.GetFName()")
        assert res["ok"], res
        times.append(dict(b.last_timing))
    report["ping"] = {"bridge_version": b.bridge_version, "runs": times,
                      "median_total_s": sorted(t["total_s"] for t in times)[5]}
    log(f"ping: median {report['ping']['median_total_s']} s, bridge {b.bridge_version}")

    # libs: first call compiles, later calls use the cache
    lt = []
    for _ in range(3):
        st = calls.call2d(b, "structure")
        lt.append(dict(b.last_timing))
    report["libs"] = {"runs": lt, "document": st.get("document")}
    log(f"libs: {[t['vw_s'] for t in lt]} s inside Vectorworks")

    # arg check through the real menu command
    before = count_objects(b)
    res = b.run("vs.Rect(0, 0, 1)")
    after = count_objects(b)
    report["arg_check"] = {"ok": res["ok"], "error": res["error"], "objects_before": before,
                           "objects_after": after,
                           "pass": (not res["ok"]) and "VSArgumentError" in (res["error"] or "") and before == after}
    log(f"arg_check: pass={report['arg_check']['pass']}")

    # busy: A keeps Vectorworks busy 25 s; B must be cancelled and never run
    marker = ROOT / "live_marker_B.txt"
    marker.unlink(missing_ok=True)
    out = {}

    def run_a():
        try:
            out["a"] = Bridge(root=ROOT).run("import time\ntime.sleep(25)\n'A done'", timeout=120)["value"]
        except Exception as e:  # noqa: BLE001
            out["a"] = repr(e)

    ta = threading.Thread(target=run_a)
    ta.start()
    time.sleep(3)
    bb = Bridge(root=ROOT, claim_timeout=10)
    t0 = time.monotonic()
    try:
        bb.run(f"open({str(marker)!r}, 'w').write('B ran')")
        out["b"] = "RAN (unexpected)"
    except BridgeTimeout as e:
        out["b"] = f"cancelled after {time.monotonic() - t0:.1f}s: {e}"
    ta.join()
    time.sleep(10)  # the queued Apple Event for B runs now: it must find nothing
    cancelled = sorted(p.name for p in (ROOT / "jobs").glob("*.cancelled"))
    report["busy_cancel"] = {"a": out.get("a"), "b": out.get("b"), "marker_exists": marker.exists(),
                             "cancelled_files": cancelled[-3:],
                             "pass": out.get("a") == "A done" and out["b"].startswith("cancelled")
                             and not marker.exists()}
    log(f"busy_cancel: pass={report['busy_cancel']['pass']} ({out.get('b', '')[:60]})")

    # long job: claimed at once, runs longer than the claim timeout
    t0 = time.monotonic()
    try:
        v = Bridge(root=ROOT, claim_timeout=10).run("import time\ntime.sleep(18)\n'long done'")["value"]
    except Exception as e:  # noqa: BLE001
        v = repr(e)
    report["long_job"] = {"value": v, "seconds": round(time.monotonic() - t0, 1), "pass": v == "long done"}
    log(f"long_job: pass={report['long_job']['pass']} in {report['long_job']['seconds']} s")


def tool_tests(report: dict) -> None:
    """The MCP tools in-process (same code Claude calls), read-only."""
    from vwmcp.tools import draw2d, generic

    out = {}
    t0 = time.monotonic()
    out["vw_ping"] = generic.vw_ping()
    out["vw_document_info"] = generic.vw_document_info()
    st = draw2d.vw2d_structure()
    out["vw2d_structure_keys"] = sorted(st)[:20]
    ex = generic.vw_exec_python("vs.Rect(0, 0, 1)", modifies_document=False)
    out["exec_rect"] = {"ok": ex["ok"], "error": ex["error"]}
    out["seconds"] = round(time.monotonic() - t0, 2)
    report["tools"] = out
    log(f"tools: {out['seconds']} s, ping {out['vw_ping'].get('bridge_version')}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(Path(__file__).resolve().parents[1] / "src"))
    ap.add_argument("--only", default="protocol,tools")
    ap.add_argument("--delay", type=float, default=0.0)
    args = ap.parse_args()
    sys.path.insert(0, args.src)
    ROOT.mkdir(parents=True, exist_ok=True)
    time.sleep(args.delay)
    wait_idle()
    report = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "python": sys.version.split()[0]}
    log(f"live test start ({args.only})")
    for name, fn in (("protocol", protocol_tests), ("tools", tool_tests)):
        if name not in args.only.split(","):
            continue
        try:
            fn(report)
        except Exception as e:  # noqa: BLE001
            report[f"{name}_error"] = traceback.format_exc()
            log(f"{name} failed: {e!r}")
    report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    REPORT.write_text(json.dumps(report, indent=1, default=repr), encoding="utf-8")
    log("live test done")


if __name__ == "__main__":
    main()
