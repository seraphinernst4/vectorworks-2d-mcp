#!/usr/bin/env python3
"""Measure how long the trigger takes (macOS, Vectorworks running).

    python scripts/trigger_bench.py [--n 10]

Variants (all run the same read-only job through the menu command):
  file      osascript -e '... run VectorScript (read POSIX file "trigger.vss")'  (0.3.x)
  inline    osascript -e '... run VectorScript "<vectorscript>"'
  compiled  osascript trigger.scpt (compiled once with osacompile)
Plus two baselines without Vectorworks work: plain osascript start-up and an
Apple Event round trip (`get name`). Writes ~/.vwmcp/trigger_bench.json.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("VWMCP_HOME", Path.home() / ".vwmcp"))
VSS = "PROCEDURE T; BEGIN DoMenuTextByName('MCP Run Job', 0); END; RUN(T);"


def timed(args: list[str]) -> float:
    t0 = time.perf_counter()
    subprocess.run(args, capture_output=True, text=True, timeout=60)
    return time.perf_counter() - t0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--src", default=str(Path(__file__).resolve().parents[1] / "src"))
    ap.add_argument("--delay", type=float, default=0)
    args = ap.parse_args()
    sys.path.insert(0, args.src)
    time.sleep(args.delay)
    from vwmcp.client import Bridge
    from vwmcp.trigger import running_apps

    app = running_apps()[0][0]
    vss = ROOT / "trigger.vss"
    vss.write_text(VSS, encoding="utf-8")
    scpt = ROOT / "bench_trigger.scpt"
    subprocess.run(["osacompile", "-o", str(scpt), "-e", f'tell application "{app}" to run VectorScript "{VSS}"'],
                   check=True, capture_output=True)
    variants = {
        "file": ["osascript", "-e", f'tell application "{app}" to run VectorScript (read POSIX file "{vss}")'],
        "inline": ["osascript", "-e", f'tell application "{app}" to run VectorScript "{VSS}"'],
        "compiled": ["osascript", str(scpt)],
    }
    out: dict = {"app": app, "n": args.n}
    out["osascript_startup"] = statistics.median(timed(["osascript", "-e", "return 1"]) for _ in range(args.n))
    out["apple_event_get_name"] = statistics.median(
        timed(["osascript", "-e", f'tell application "{app}" to get name']) for _ in range(args.n))
    for name, cmd in variants.items():
        def trig(cmd=cmd):
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        b = Bridge(root=ROOT, trigger=trig, claim_timeout=10)
        times = []
        for _ in range(args.n):
            b.run("vs.GetFName()")
            times.append(b.last_timing["total_s"])
            time.sleep(0.3)  # let the previous osascript finish
        out[name] = {"median_s": statistics.median(times), "min_s": min(times), "max_s": max(times)}
    scpt.unlink(missing_ok=True)
    (ROOT / "trigger_bench.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
