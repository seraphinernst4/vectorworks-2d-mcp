from vwmcp.client import TriggerError
import pytest

from vwmcp.trigger import MacTrigger, _APP_RE, parse_etime, parse_ps


class FakeProc:
    def __init__(self, done=False):
        self.done = done

    def poll(self):
        return 0 if self.done else None


def make(root, running, app=None, procs=None, max_pending=3):
    procs = [] if procs is None else procs

    def spawn(args):
        p = FakeProc()
        procs.append((args, p))
        return p

    t = MacTrigger(root, app=app, apps=lambda: running, spawn=spawn, max_pending=max_pending)
    return t, procs


def test_refuses_when_vectorworks_not_running(root):
    t, procs = make(root, running=[])
    with pytest.raises(TriggerError, match="not running"):
        t()
    assert procs == []


def test_refuses_while_vectorworks_is_still_starting(root):
    # Scripts sent during launch crash Vectorworks 2026.
    t, procs = make(root, running=[("Vectorworks 2026", 5.0)])
    with pytest.raises(TriggerError, match="starting"):
        t()
    assert procs == []


def test_sends_menu_command_via_vectorscript_file(root):
    t, procs = make(root, running=[("Vectorworks 2026", 600.0)])
    t()
    ((args, _),) = procs
    assert args[0] == "osascript"
    assert 'tell application "Vectorworks 2026" to run VectorScript' in " ".join(args)
    assert "DoMenuTextByName('MCP Run Job', 0)" in " ".join(args)


def test_uses_whichever_version_runs(root):
    t, procs = make(root, running=[("Vectorworks 2027", 300.0)])
    t()
    assert 'tell application "Vectorworks 2027"' in " ".join(procs[0][0])
    assert t.target == "Vectorworks 2027"


def test_configured_version_is_a_preference(root):
    both = [("Vectorworks 2027", 100.0), ("Vectorworks 2026", 900.0)]
    t, procs = make(root, running=both, app="Vectorworks 2026")
    t()
    assert 'tell application "Vectorworks 2026"' in " ".join(procs[0][0])
    # a stale setting (2026) must not block the only running version (2027)
    t, procs = make(root, running=[("Vectorworks 2027", 100.0)], app="Vectorworks 2026")
    t()
    assert 'tell application "Vectorworks 2027"' in " ".join(procs[0][0])


def test_pending_events_are_capped_not_refused(root):
    t, procs = make(root, running=[("Vectorworks 2026", 600.0)], max_pending=2)
    t()
    t()
    t()  # third: no new event, no error (the claim timeout decides)
    assert len(procs) == 2
    assert t.in_flight() and t.pending_for() >= 0
    for _, p in procs:
        p.done = True
    assert not t.in_flight() and t.pending_for() == 0
    t()
    assert len(procs) == 3


def test_app_regex():
    m = _APP_RE.search("/Applications/Vectorworks 2027/Vectorworks 2027.app/Contents/MacOS/Vectorworks")
    assert m and m.group(1) == "Vectorworks 2027"
    assert _APP_RE.search("/A/Vectorworks 2026.app/Contents/MacOS/Vectorworks -NSDocumentRevisionsDebugMode")
    assert not _APP_RE.search("/A/Vectorworks 2026.app/Contents/MacOS/Vectorworks Helper")


@pytest.mark.parametrize(
    "text,secs", [("00:07", 7), ("12:34", 754), ("01:00:00", 3600), ("2-00:00:01", 172801)]
)
def test_parse_etime(text, secs):
    assert parse_etime(text) == secs


def test_parse_ps_finds_vectorworks_only():
    out = """ 1540 02-02:22:39 /Applications/Vectorworks Cloud Services.app/Contents/MacOS/Vectorworks Cloud Services
 4257 02-00:52:07 /Applications/Vectorworks 2026/Vectorworks 2026.app/Contents/PlugIns/QLThumbnailExtension.appex/Contents/MacOS/QLThumbnailExtension -B x
31213       45:28 /Applications/Vectorworks 2026/Vectorworks 2026.app/Contents/MacOS/Vectorworks
31218       45:27 /Applications/Vectorworks 2026/Vectorworks 2026.app/Contents/Frameworks/Vectorworks Web Helper (GPU).app/Contents/MacOS/Vectorworks Web Helper (GPU) --type=gpu
  900       00:12 /Applications/Vectorworks 2027/Vectorworks 2027.app/Contents/MacOS/Vectorworks
"""
    assert parse_ps(out) == [("Vectorworks 2027", 12.0), ("Vectorworks 2026", 2728.0)]
    assert parse_ps("") == []


def test_direct_mode_runs_the_bridge_without_menu_command(root):
    t, procs = make(root, running=[("Vectorworks 2026", 600.0)])
    t.mode, t.direct = "direct", True
    t()
    script = procs[0][0][2]
    assert "PythonExecute('from vwmcp import vwmcp_bridge')" in script
    assert "PythonExecute('vwmcp_bridge.run_pending()')" in script
    assert "DoMenuTextByName" not in script and '"' not in script.split("run VectorScript ")[1][1:-1]


def test_auto_mode_switches_to_direct_once(root):
    procs = []

    def spawn(args):
        p = FakeProc(done=True)
        procs.append(args)
        return p

    t = MacTrigger(root, apps=lambda: [("Vectorworks 2026", 600.0)], spawn=spawn, mode="auto")
    t()
    assert "DoMenuTextByName" in procs[-1][2]
    assert t.on_unclaimed() is True and t.on_unclaimed() is False
    t()
    assert "PythonExecute" in procs[-1][2]
    menu = MacTrigger(root, apps=lambda: [("Vectorworks 2026", 600.0)], spawn=spawn, mode="menu")
    assert menu.on_unclaimed() is False
