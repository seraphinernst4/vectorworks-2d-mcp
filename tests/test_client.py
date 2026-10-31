import json
import os
import threading
import time

import pytest
import vwmcp_bridge

from vwmcp.client import Bridge, BridgeError, BridgeTimeout, TriggerError


class FakeExecutor:
    """Plays the Vectorworks side: each trigger runs the real bridge code."""

    def __init__(self, root, vs_module, delay=0.0):
        self.root, self.vs, self.delay, self.triggers = root, vs_module, delay, 0

    def __call__(self):
        self.triggers += 1
        run = lambda: vwmcp_bridge.run_pending(self.root, vs_module=self.vs)
        threading.Timer(self.delay, run).start()


def make_bridge(root, trigger, **kw):
    kw.setdefault("claim_timeout", 1.0)
    return Bridge(root=root, trigger=trigger, poll_interval=0.005, **kw)


def test_round_trip_returns_value_and_stdout(root, fake_vs):
    b = make_bridge(root, FakeExecutor(root, fake_vs))
    res = b.run("print('hi')\nvs.GetFName()")
    assert res["ok"] and res["value"] == "Plot.vwx" and res["stdout"] == "hi\n"
    assert b.bridge_version == vwmcp_bridge.BRIDGE_VERSION and b.supports_libs()
    assert set(b.last_timing) == {"claim_s", "total_s", "vw_s"}


def test_job_is_written_atomically(root, fake_vs):
    seen = []

    def peek():
        seen.extend(p.name for p in (root / "jobs").iterdir())
        vwmcp_bridge.run_pending(root, vs_module=fake_vs)

    make_bridge(root, peek).run("1")
    assert len(seen) == 1 and seen[0].endswith(".json")  # never a half-written .json


def test_job_ids_sort_in_submission_order(root, fake_vs):
    b = make_bridge(root, lambda: None)
    ids = [b._new_job_id() for _ in range(50)]
    assert ids == sorted(ids) and len(set(ids)) == 50


def test_unclaimed_job_is_cancelled_and_never_runs_later(root, fake_vs):
    # Like a dialog open in Vectorworks: the trigger is not answered in time.
    b = make_bridge(root, lambda: None, claim_timeout=0.1)
    with pytest.raises(BridgeTimeout) as e:
        b.run("vs.calls.append('ran')")
    assert "MCP Run Job" in str(e.value) and "will not run later" in str(e.value)
    assert list((root / "jobs").glob("*.json")) == []
    assert len(list((root / "jobs").glob("*.cancelled"))) == 1
    # The dialog closes, the queued trigger finally runs the menu command:
    assert vwmcp_bridge.run_pending(root, vs_module=fake_vs) == 0
    assert fake_vs.calls == []
    assert list((root / "results").glob("*.json")) == []


def test_claim_at_the_cancel_moment_waits_for_the_result(root, fake_vs):
    # Vectorworks claims the job just before the server's cancel rename:
    # the rename fails, so the server must wait for the result instead.
    class Racing(Bridge):
        def _cancel(self, job_path):
            claimed = vwmcp_bridge._claim_oldest(str(self.jobs))
            assert claimed
            threading.Timer(0.2, vwmcp_bridge._run_one,
                            (claimed, str(self.results), fake_vs)).start()
            return super()._cancel(job_path)

    b = Racing(root=root, trigger=lambda: None, poll_interval=0.005, claim_timeout=0.05)
    assert b.run("6 * 7")["value"] == 42


def test_long_job_after_claim_is_waited_for(root, fake_vs):
    # claimed at once, but runs longer than the claim timeout (a big check)
    b = make_bridge(root, FakeExecutor(root, fake_vs), claim_timeout=0.2, timeout=5)
    res = b.run("import time\ntime.sleep(0.6)\n'done'")
    assert res["value"] == "done"


def test_result_timeout_after_claim(root, fake_vs):
    b = make_bridge(root, FakeExecutor(root, fake_vs), claim_timeout=1, timeout=0.2)
    with pytest.raises(BridgeTimeout, match="still working"):
        b.run("import time\ntime.sleep(0.6)")
    assert "result timeout" in (root / "vwmcp.log").read_text()


def test_timeout_is_logged_with_timestamp(root, fake_vs):
    b = make_bridge(root, lambda: None, claim_timeout=0.05)
    with pytest.raises(BridgeTimeout):
        b.run("1")
    log = (root / "vwmcp.log").read_text()
    assert "cancelled" in log and log[:4].isdigit()


def test_lost_trigger_is_fired_again(root, fake_vs, monkeypatch):
    import vwmcp.client as client

    monkeypatch.setattr(client, "RETRIGGER_AFTER", 0.05)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) >= 2:  # the first Apple Event got lost
            vwmcp_bridge.run_pending(root, vs_module=fake_vs)

    assert make_bridge(root, flaky).run("5")["value"] == 5
    assert len(calls) >= 2


def test_trigger_failure_surfaces_as_error_and_cancels_job(root, fake_vs):
    def broken():
        raise TriggerError("Vectorworks is not running")

    b = make_bridge(root, broken)
    with pytest.raises(TriggerError):
        b.run("1")
    assert list((root / "jobs").glob("*.json")) == []
    assert "not running" in (root / "vwmcp.log").read_text()


def test_result_file_is_consumed(root, fake_vs):
    b = make_bridge(root, FakeExecutor(root, fake_vs))
    b.run("1")
    assert list((root / "results").glob("*.json")) == []
    assert list((root / "jobs").iterdir()) == []


def test_slow_claim_within_timeout(root, fake_vs):
    b = make_bridge(root, FakeExecutor(root, fake_vs, delay=0.15), claim_timeout=1)
    assert b.run("41 + 1")["value"] == 42


def test_libs_are_written_once_and_run_before_the_code(root, fake_vs):
    b = make_bridge(root, FakeExecutor(root, fake_vs))
    ref = b.lib_ref("helpers", "def twice(x):\n    return 2 * x\n")
    assert ref == b.lib_ref("helpers", "def twice(x):\n    return 2 * x\n")
    assert len(list((root / "lib").glob("*.py"))) == 1
    assert b.run("twice(21)", libs=[ref])["value"] == 42


def test_housekeeping_removes_only_stale_leftovers(root, fake_vs):
    b = make_bridge(root, lambda: None)
    (root / "jobs").mkdir(parents=True)
    (root / "results").mkdir(parents=True)
    old = time.time() - 7200
    for rel in ("jobs/a.running", "jobs/b.working", "jobs/c.cancelled", "results/d.json"):
        (root / rel).write_text("{}")
        os.utime(root / rel, (old, old))
    (root / "results/fresh.json").write_text("{}")
    (root / "jobs/queued.json").write_text("{}")
    assert b.housekeep() == 4
    left = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
    assert left == ["jobs/queued.json", "results/fresh.json"]


def test_old_bridge_without_libs_support_gets_inline_code(root, fake_vs):
    from vwmcp import calls

    b = make_bridge(root, FakeExecutor(root, fake_vs))
    assert not b.supports_libs()
    sent = []
    orig = b.run

    def spy(code, timeout=None, libs=None, check=True):
        sent.append((code, libs))
        return orig(code, timeout=timeout, libs=libs, check=check)

    b.run = spy
    calls._run_with_libs(b, [("l", "X = 3\n")], "result = X")
    assert sent[0][1] is None and "X = 3" in sent[0][0]
    calls._run_with_libs(b, [("l", "X = 3\n")], "result = X")
    assert sent[-1][1] and sent[-1][0] == "result = X"


def test_job_carries_ttl_beyond_claim_timeout(root, fake_vs):
    seen = {}

    def peek():
        job = next((root / "jobs").glob("*.json"))
        seen.update(json.loads(job.read_text()))
        vwmcp_bridge.run_pending(root, vs_module=fake_vs)

    make_bridge(root, peek, claim_timeout=15).run("1")
    assert seen["ttl"] > 15 and seen["protocol"] == 2


def test_auto_trigger_falls_back_when_menu_command_is_missing(root, fake_vs, monkeypatch):
    import vwmcp.client as client

    monkeypatch.setattr(client, "RETRIGGER_AFTER", 0.05)

    class AutoTrigger:
        direct = False

        def __call__(self):
            if self.direct:  # only the direct trigger reaches the bridge
                vwmcp_bridge.run_pending(root, vs_module=fake_vs)

        def in_flight(self):
            return False

        def on_unclaimed(self):
            changed, self.direct = not self.direct, True
            return changed

    b = make_bridge(root, AutoTrigger())
    assert b.run("7")["value"] == 7
    assert "direct trigger" in (root / "vwmcp.log").read_text()


def test_trigger_error_after_the_job_was_claimed_waits_for_it(root, fake_vs):
    """An earlier Apple Event already took the job when the new trigger fails:
    the job runs, so the server must wait for it, not report a failure."""
    b = make_bridge(root, None)

    def late_failure():
        claimed = vwmcp_bridge._claim_oldest(str(b.jobs))
        threading.Timer(0.1, vwmcp_bridge._run_one, (claimed, str(b.results), fake_vs)).start()
        raise TriggerError("Vectorworks 2026 is still starting")

    b.trigger = late_failure
    assert b.run("7 * 6")["value"] == 42


def test_vectorworks_quitting_during_a_job_fails_fast(root, fake_vs, monkeypatch):
    import vwmcp.client as client

    monkeypatch.setattr(client, "ALIVE_EVERY", 0.05)

    class Gone:
        def __call__(self):
            vwmcp_bridge._claim_oldest(str(b.jobs))  # claimed, then Vectorworks crashes

        def target_alive(self):
            return False

    b = make_bridge(root, Gone(), timeout=30)
    t = time.monotonic()
    with pytest.raises(BridgeError, match="quit or restarted"):
        b.run("1")
    assert time.monotonic() - t < 5
    assert "target gone" in (root / "vwmcp.log").read_text()


def test_alive_probe_does_not_disturb_normal_jobs(root, fake_vs, monkeypatch):
    import vwmcp.client as client

    monkeypatch.setattr(client, "ALIVE_EVERY", 0.01)

    class Slow(FakeExecutor):
        def target_alive(self):
            return True

    b = make_bridge(root, Slow(root, fake_vs), timeout=5)
    assert b.run("import time\ntime.sleep(0.2)\n3")["value"] == 3
