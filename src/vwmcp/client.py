"""Server side of the job-folder bridge.

The server writes <root>/jobs/<id>.json (via .tmp), fires a trigger that
makes Vectorworks run the "MCP Run Job" menu command (which calls
vwmcp_bridge.run_pending), then waits:

1. up to `claim_timeout` (15 s) for Vectorworks to claim the job (rename to
   .working). Not claimed in time: the server renames the job to .cancelled.
   If that rename succeeds, the job can never run. If it fails, Vectorworks
   claimed it at that moment, so the server goes on waiting.
2. up to `timeout` for the result of a claimed job (long jobs are fine).
"""

from __future__ import annotations

import contextlib
import hashlib
import itertools
import json
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get("VWMCP_HOME", Path.home() / ".vwmcp"))
DEFAULT_TIMEOUT = float(os.environ.get("VWMCP_TIMEOUT", "600"))
DEFAULT_CLAIM_TIMEOUT = float(os.environ.get("VWMCP_CLAIM_TIMEOUT", "15"))
# Results, cancelled and orphaned jobs older than this are removed.
STALE_AFTER = 3600.0
# Re-fire the trigger if the job is still unclaimed after this long and no
# trigger is in flight (an Apple Event can get lost while Vectorworks is busy).
RETRIGGER_AFTER = 2.0
# while a claimed job runs: how often to check that Vectorworks still runs
ALIVE_EVERY = 2.0


class BridgeError(RuntimeError):
    """The bridge could not deliver a job or its result."""

    # True when the job was cancelled before Vectorworks took it: then it can
    # never run, nothing was changed (callers may safely retry or roll back)
    never_ran = False


class BridgeTimeout(BridgeError):
    pass


class TriggerError(BridgeError):
    pass


def _version_tuple(v: str | None) -> tuple:
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return ()


class Bridge:
    def __init__(
        self,
        root: Path | str = DEFAULT_ROOT,
        trigger: Callable[[], None] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        poll_interval: float = 0.02,
        claim_timeout: float | None = None,
    ):
        self.root = Path(root)
        self.jobs = self.root / "jobs"
        self.results = self.root / "results"
        self.libdir = self.root / "lib"
        self.log_path = self.root / "vwmcp.log"
        if trigger is None:
            from vwmcp.trigger import default_trigger

            trigger = default_trigger(self.root)
        self.trigger = trigger
        self.timeout = timeout
        self.claim_timeout = DEFAULT_CLAIM_TIMEOUT if claim_timeout is None else claim_timeout
        self.poll_interval = poll_interval
        self._lock = threading.Lock()  # one job in flight: one trigger, one undo step
        self._seq = itertools.count()
        self.bridge_version: str | None = None  # last version seen in a result
        self._libs: dict[str, dict] = {}
        self.last_timing: dict = {}
        self._housekept = 0.0

    # ------------------------------------------------------------ helpers

    def log(self, message: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().isoformat(timespec="seconds")
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"{stamp} {message}\n")

    def _new_job_id(self) -> str:
        # Zero-padded so the bridge's name sort is submission order.
        return f"{time.time_ns():020d}-{os.getpid()}-{next(self._seq):06d}"

    def supports_libs(self) -> bool:
        """The bridge in Vectorworks caches helper libraries (>= 0.2.0)."""
        return _version_tuple(self.bridge_version) >= (0, 2, 0)

    def lib_ref(self, name: str, source: str) -> dict:
        """Reference to a helper library: written once to <root>/lib/<sha>.py;
        the bridge compiles it once per Vectorworks session."""
        sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
        ref = self._libs.get(sha)
        path = self.libdir / f"{sha}.py"
        if ref is None or not path.is_file():
            self.libdir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(source, encoding="utf-8")
            os.replace(tmp, path)
            ref = {"name": name, "sha": sha, "path": str(path)}
            self._libs[sha] = ref
        return ref

    def housekeep(self, now: float | None = None) -> int:
        """Remove leftovers: old results nobody read, cancelled jobs, jobs
        orphaned by a crash, stale helper libraries. Returns files removed."""
        now = time.time() if now is None else now
        removed = 0
        patterns = [(self.results, "*.json"), (self.results, "*.tmp"), (self.jobs, "*.cancelled"),
                    (self.jobs, "*.working"), (self.jobs, "*.running"), (self.jobs, "*.tmp")]
        for folder, pattern in patterns:
            for p in folder.glob(pattern) if folder.is_dir() else ():
                with contextlib.suppress(OSError):
                    if now - p.stat().st_mtime > STALE_AFTER:
                        p.unlink()
                        removed += 1
        if self.libdir.is_dir():
            keep = set(self._libs)
            for p in self.libdir.glob("*.py"):
                with contextlib.suppress(OSError):
                    if p.stem not in keep and now - p.stat().st_mtime > 7 * 86400:
                        p.unlink()
                        removed += 1
        self._housekept = now
        return removed

    # ---------------------------------------------------------------- run

    def run(self, code: str, timeout: float | None = None, libs: list[dict] | None = None,
            check: bool = True) -> dict:
        """Run code inside Vectorworks; return the bridge's result dict.
        libs: helper libraries (see lib_ref) executed before the code."""
        timeout = self.timeout if timeout is None else timeout
        with self._lock:
            if time.time() - self._housekept > 600:
                with contextlib.suppress(OSError):
                    self.housekeep()
            return self._run_locked(code, timeout, libs, check)

    def _run_locked(self, code: str, timeout: float, libs: list[dict] | None, check: bool) -> dict:
        self.jobs.mkdir(parents=True, exist_ok=True)
        self.results.mkdir(parents=True, exist_ok=True)
        job_id = self._new_job_id()
        job_path = self.jobs / f"{job_id}.json"
        result_path = self.results / f"{job_id}.json"
        created = time.time()
        # ttl: the bridge refuses jobs older than this (orphans of a crashed
        # server); generous, so it never refuses a job we still wait for.
        job = {"id": job_id, "code": code, "created": created,
               "ttl": self.claim_timeout + 60, "protocol": 2}
        if libs:
            job["libs"] = libs
        if not check:
            job["check"] = False
        tmp = self.jobs / f"{job_id}.tmp"
        tmp.write_text(json.dumps(job), encoding="utf-8")
        os.replace(tmp, job_path)

        t0 = time.monotonic()
        claimed_at = None
        try:
            self.trigger()
        except TriggerError as e:
            if self._cancel(job_path):
                self.log(f"trigger failed for job {job_id}: {e}")
                e.never_ran = True
                raise
            # an earlier Apple Event (or another server) already took it: wait for it
            claimed_at = time.monotonic()

        # 1. wait for the claim (or a result, if it was quick)
        claim_deadline = t0 + self.claim_timeout
        last_fire = t0
        while claimed_at is None:
            if result_path.exists() or not job_path.exists():  # done, or renamed to .working
                claimed_at = time.monotonic()
                break
            now = time.monotonic()
            # wall clock too: monotonic time stands still while the Mac sleeps,
            # the bridge's ttl check doesn't
            if now >= claim_deadline or time.time() - created >= self.claim_timeout:
                if self._cancel(job_path):
                    msg = self._not_claimed_message()
                    self.log(f"not claimed in {self.claim_timeout:g}s, cancelled {job_id}: {msg}")
                    err = BridgeTimeout(msg)
                    err.never_ran = True
                    raise err
                claimed_at = now  # claimed right now: wait for the result
                break
            if now - last_fire >= RETRIGGER_AFTER and not self._trigger_in_flight():
                last_fire = now
                hook = getattr(self.trigger, "on_unclaimed", None)
                if callable(hook) and hook():
                    self.log("Vectorworks answered but did not run 'MCP Run Job': "
                             "using the direct trigger (PythonExecute) from now on")
                with contextlib.suppress(TriggerError):
                    self.trigger()
            time.sleep(self.poll_interval)

        # 2. wait for the result; if Vectorworks quits or restarts meanwhile,
        # stop waiting instead of hanging until the timeout
        deadline = claimed_at + timeout
        alive = getattr(self.trigger, "target_alive", None)
        next_probe = claimed_at + ALIVE_EVERY
        while time.monotonic() < deadline:
            if callable(alive) and time.monotonic() >= next_probe:
                next_probe = time.monotonic() + ALIVE_EVERY
                if alive() is False and not result_path.exists():
                    msg = ("Vectorworks quit or restarted while working on this request; it did not "
                           "finish. Open Vectorworks with your document and check what was done.")
                    self.log(f"target gone, job {job_id}: {msg}")
                    raise BridgeError(msg)
            if result_path.exists():
                res = self._consume(result_path)
                self.bridge_version = res.get("bridge_version") or self.bridge_version
                self.last_timing = {
                    "claim_s": round(claimed_at - t0, 3),
                    "total_s": round(time.monotonic() - t0, 3),
                    "vw_s": res.get("duration"),
                }
                return res
            time.sleep(self.poll_interval)

        msg = (
            f"Vectorworks is still working on this request after {timeout:g}s. "
            "It keeps running there; its result will be discarded. Check Vectorworks "
            "and try again with a smaller request."
        )
        self.log(f"result timeout job {job_id}: {msg}")
        raise BridgeTimeout(msg)

    def _trigger_in_flight(self) -> bool:
        probe = getattr(self.trigger, "in_flight", None)
        return bool(probe()) if callable(probe) else False

    def _not_claimed_message(self) -> str:
        probe = getattr(self.trigger, "pending_for", None)
        stuck = probe() if callable(probe) else 0.0
        if stuck and stuck > 5:
            return (
                f"Vectorworks did not take the request within {self.claim_timeout:g}s: it has "
                "not answered for a while, so a dialog is probably open (or it is busy). "
                "Close the dialog in Vectorworks and try again. The request was cancelled "
                "and will not run later."
            )
        menu = not getattr(self.trigger, "direct", True)  # menu, or auto still on the menu
        return (
            f"Vectorworks did not take the request within {self.claim_timeout:g}s. Check that "
            "a document is open and no dialog is showing"
            + (" and that the 'MCP Run Job' menu command is in your workspace" if menu else "")
            + ". The request was cancelled and will not run later."
        )

    def _cancel(self, job_path: Path) -> bool:
        """Rename an unclaimed job to .cancelled. True if it was still unclaimed
        (then it can never run); False if Vectorworks claimed it first."""
        try:
            os.replace(job_path, job_path.with_suffix(".cancelled"))
            return True
        except FileNotFoundError:
            return False

    def _consume(self, result_path: Path) -> dict:
        for attempt in range(5):
            try:
                res = json.loads(result_path.read_text(encoding="utf-8"))
                break
            except (json.JSONDecodeError, OSError):  # pragma: no cover - atomic writes
                if attempt == 4:
                    raise
                time.sleep(0.02)
        with contextlib.suppress(FileNotFoundError):
            result_path.unlink()
        return res
