import threading
from pathlib import Path

import pytest
import vwmcp_bridge

from vwmcp import app
from vwmcp.tools import generic as server
from vwmcp.client import Bridge


class Runner:
    def __init__(self, root, vs):
        self.root, self.vs = root, vs

    def __call__(self):
        threading.Thread(
            target=vwmcp_bridge.run_pending, args=(self.root,), kwargs={"vs_module": self.vs}
        ).start()


@pytest.fixture
def vs(fake_vs):
    def GetRField(h, record, field):
        """Returns string description of a record field value. Definition: vs.GetRField(h, record, field)"""

    fake_vs.GetRField = GetRField
    fake_vs.GetVersionEx = lambda: (31, 3, 0, 1, 846827)
    return fake_vs


@pytest.fixture(autouse=True)
def bridge(root, vs, monkeypatch):
    b = Bridge(root=root, trigger=Runner(root, vs), poll_interval=0.01, timeout=2)
    monkeypatch.setattr(app, "get_bridge", lambda: b)
    return b


def test_exec_python_returns_value_stdout_and_no_error():
    out = server.vw_exec_python("print('x')\n6*7")
    assert out == {"ok": True, "value": 42, "stdout": "x\n", "error": None, "traceback": None}


def test_exec_python_reports_errors_instead_of_raising():
    out = server.vw_exec_python("raise ValueError('bad seat')")
    assert out["ok"] is False and "bad seat" in out["error"] and "ValueError" in out["traceback"]


def test_ping_reports_versions_and_document():
    out = server.vw_ping()
    assert out["document"] == "Plot.vwx"
    assert out["vectorworks_version"] == "31.3.0"
    assert out["bridge_version"] == app.EXPECTED_BRIDGE_VERSION
    assert "warning" not in out


def test_ping_warns_on_bridge_version_mismatch(monkeypatch):
    monkeypatch.setattr(app, "EXPECTED_BRIDGE_VERSION", "9.9.9")
    out = server.vw_ping()
    assert "9.9.9" in out["warning"] and "install" in out["warning"]


def test_vs_lookup_uses_the_index_without_vectorworks(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "trigger", lambda: pytest.fail("index lookup must not call Vectorworks"))
    out = server.vw_vs_lookup("record field")
    assert out[0]["name"] == "GetRField"
    assert out[0]["signature"] == "vs.GetRField(h: HANDLE, record: STRING, field: STRING)"
    rect = server.vw_vs_lookup("Rect")[0]
    assert rect["name"] == "Rect" and rect["argument_check"] and "POINT" in rect["notes"][0]
    new = server.vw_vs_lookup("GetActivePhase")[0]
    assert new["since"] == "Vectorworks 2027"


def test_vs_lookup_falls_back_to_live_docstrings():
    out = server.vw_vs_lookup("GetRField", live=True)
    assert out[0]["name"] == "GetRField" and out[0]["source"] == "live"
    assert "Definition" in out[0]["doc"]
    assert server.vw_vs_lookup("zzz no such thing") == []


def test_packaged_bridge_matches_repo_copy():
    repo = Path(__file__).resolve().parents[1] / "vw-plugin" / "vwmcp" / "vwmcp_bridge.py"
    assert app.bundled_bridge_source() == repo.read_text()
    assert f'BRIDGE_VERSION = "{app.EXPECTED_BRIDGE_VERSION}"' in repo.read_text()


def test_ping_warns_on_untested_vectorworks_version(vs):
    vs.GetVersionEx = lambda: (30, 4, 0, 1, 800000)  # Vectorworks 2025
    out = server.vw_ping()
    assert "2026" in out["warning"] and "30.4.0" in out["warning"]


def test_ping_combines_warnings(vs, monkeypatch):
    vs.GetVersionEx = lambda: (30, 4, 0, 1, 800000)
    monkeypatch.setattr(app, "EXPECTED_BRIDGE_VERSION", "9.9.9")
    out = server.vw_ping()
    assert "9.9.9" in out["warning"] and "2026" in out["warning"]


def test_ping_notes_prepared_2027(vs):
    vs.GetVersionEx = lambda: (32, 0, 0, 1, 900000)  # Vectorworks 2027
    out = server.vw_ping()
    assert "warning" not in out and "2027" in out["note"] and "not been tested live" in out["note"]


def test_ping_reloads_an_old_bridge_and_asks_again(bridge, monkeypatch):
    from vwmcp import calls

    monkeypatch.setattr(vwmcp_bridge, "BRIDGE_VERSION", "0.1.0")  # old module still in memory
    reloads = []

    def fake_reload(b):
        reloads.append(b.bridge_version)
        monkeypatch.setattr(vwmcp_bridge, "BRIDGE_VERSION", app.EXPECTED_BRIDGE_VERSION)
        b.bridge_version = app.EXPECTED_BRIDGE_VERSION

    monkeypatch.setattr(calls, "maybe_reload", fake_reload)
    out = server.vw_ping()
    assert reloads == ["0.1.0"]
    assert out["bridge_version"] == app.EXPECTED_BRIDGE_VERSION and "warning" not in out
