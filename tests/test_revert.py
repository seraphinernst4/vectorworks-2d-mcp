import pytest
from mcp.server.mcpserver.exceptions import ToolError

from vwmcp import app
from vwmcp.journal import Journal
from vwmcp.tools import generic, spotlight


class FakeVW:
    """Records vwlib calls and returns canned values."""

    def __init__(self, document="Plot.vwx"):
        self.calls, self.document = [], document

    def __call__(self, fn, **kw):
        self.calls.append((fn, kw))
        if fn == "set_record":
            return {"document": self.document, "before": {"Note": "old"}}
        if fn == "get_record":
            return {"Note": "new"}
        if fn == "insert_symbol":
            return {"document": self.document, "uuid": "sym-1", "type": "Symbol"}
        if fn == "set_lighting_device_fields":
            return {"document": self.document, "devices": [{"uuid": "ld-1", "before": {"Channel": "5"}}]}
        if fn == "read_devices":
            return [{"uuid": "ld-1", "Channel": "101"}]
        if fn == "current_document":
            return self.document
        if fn == "revert2d":
            return {"document": self.document, "reverted": len(kw["ops"]), "mapping": {}}
        raise AssertionError(fn)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    f = FakeVW()
    for mod in (app, generic, spotlight):
        monkeypatch.setattr(mod, "vw", f)
    monkeypatch.setattr(app, "vw2d", lambda fn, timeout=None, **kw: f(fn, **kw))
    monkeypatch.setattr(app, "get_journal", lambda: Journal(tmp_path / "j.jsonl"))
    monkeypatch.setattr(generic, "_run_raw", lambda code: {"ok": True, "value": None, "stdout": "", "error": None, "traceback": None})
    return f


def test_set_record_then_revert_restores_before_values(fake):
    assert generic.vw_set_record("u1", "Notes", {"Note": "new"}) == {"Note": "new"}
    out = generic.vw_revert_last()
    fn, kw = fake.calls[-1]
    assert fn == "revert2d"
    assert kw["ops"] == [{"op": "fields", "uuid": "u1", "record": "Notes", "before": {"Note": "old"}}]
    assert out["reverted_tool"] == "vw_set_record"


def test_insert_symbol_then_revert_deletes_it(fake):
    generic.vw_insert_symbol("Source 4", 1, 2)
    generic.vw_revert_last()
    assert fake.calls[-1][1]["ops"] == [{"op": "created", "uuid": "sym-1"}]


def test_lighting_fields_revert_uses_lighting_device_record(fake):
    spotlight.vw_set_lighting_device_fields([{"uuid": "ld-1", "fields": {"Channel": "101"}}])
    generic.vw_revert_last()
    assert fake.calls[-1][1]["ops"] == [
        {"op": "fields", "uuid": "ld-1", "record": "Lighting Device", "before": {"Channel": "5"}}
    ]


def test_revert_refuses_when_active_document_changed(fake):
    generic.vw_set_record("u1", "Notes", {"Note": "new"})
    fake.document = "Other.vwx"
    with pytest.raises(ToolError, match="nothing to revert in 'Other.vwx'"):
        generic.vw_revert_last()


def test_exec_python_blocks_revert_unless_read_only(fake):
    generic.vw_set_record("u1", "Notes", {"Note": "new"})
    generic.vw_exec_python("print(1)", modifies_document=False)
    assert generic.vw_revert_last()["reverted_tool"] == "vw_set_record"

    generic.vw_set_record("u1", "Notes", {"Note": "new"})
    generic.vw_exec_python("vs.Rect(0,0,1,1)")
    with pytest.raises(ToolError, match="vw_exec_python"):
        generic.vw_revert_last()
def test_revert_that_times_out_in_vectorworks_is_not_put_back(fake, monkeypatch):
    generic.vw_set_record("u1", "Notes", {"Note": "new"})

    def slow(fn, timeout=None, **kw):
        if fn == "revert2d":
            raise ToolError("Vectorworks is still working on this request after 120s. It keeps running there")
        return fake(fn, **kw)

    monkeypatch.setattr(app, "vw2d", slow)
    with pytest.raises(ToolError, match="still running in Vectorworks"):
        generic.vw_revert_last()
    with pytest.raises(ToolError):  # nothing left to revert: the entry was not put back
        generic.vw_revert_last()


def test_revert_that_never_ran_is_put_back(fake, monkeypatch):
    generic.vw_set_record("u1", "Notes", {"Note": "new"})

    def busy(fn, timeout=None, **kw):
        if fn == "revert2d":
            raise ToolError("Vectorworks did not take the request within 15s ... will not run later.")
        return fake(fn, **kw)

    monkeypatch.setattr(app, "vw2d", busy)
    with pytest.raises(ToolError, match="did not take"):
        generic.vw_revert_last()
    monkeypatch.setattr(app, "vw2d", lambda fn, timeout=None, **kw: fake(fn, **kw))
    assert generic.vw_revert_last()["reverted_tool"] == "vw_set_record"
