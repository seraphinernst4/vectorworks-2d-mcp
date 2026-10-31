from vwmcp.client import TriggerError
import pytest

from vwmcp.trigger import MacTrigger, _APP_RE, parse_etime


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
    assert "DoMenuTextByName('MCP Run Job', 0)" in (root / "trigger.vss").read_text()


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
