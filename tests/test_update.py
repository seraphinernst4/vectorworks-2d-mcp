"""Update check and download (network faked)."""

import json
import zipfile

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from vwmcp import __version__, update
from vwmcp.tools import generic


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VWMCP_HOME", str(tmp_path / "vwmcp"))
    monkeypatch.delenv("VWMCP_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(update, "downloads_dir", lambda: tmp_path / "Downloads")
    return tmp_path


def _mcpb(path, version, name="keystone-vectorworks"):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("manifest.json", json.dumps({"name": name, "display_name": "Keystone for Vectorworks",
                                                "version": version}))


class Net:
    """Fake GitHub: server.json with `latest`, the .mcpb with `file_version`."""

    def __init__(self, latest, file_version=None, fail=False):
        self.latest, self.file_version, self.fail, self.calls = latest, file_version or latest, fail, []

    def __call__(self, url, dest=None, timeout=5.0):
        self.calls.append(url)
        if self.fail:
            raise OSError("offline")
        if url == update.VERSION_URL:
            return json.dumps({"name": "x", "version": self.latest}).encode()
        _mcpb(dest, self.file_version)
        return None


def test_versions_compare_as_numbers():
    assert update.newer("0.6.10", "0.6.9") and update.newer("1.0.0", "0.9.9")
    assert not update.newer("0.6.3", "0.6.3") and not update.newer(None) and not update.newer("0.5.0", "0.6.3")


def test_check_caches_and_reports(home, monkeypatch):
    net = Net("99.0.0")
    monkeypatch.setattr(update, "_fetch", net)
    st = update.check()
    assert st["latest"] == "99.0.0" and st["update_available"] and st["current"] == __version__
    update.check()  # fresh cache: no second request
    assert len(net.calls) == 1
    update.check(force=True)
    assert len(net.calls) == 2


def test_check_offline_never_raises(home, monkeypatch):
    monkeypatch.setattr(update, "_fetch", Net("1", fail=True))
    st = update.check(force=True)
    assert "error" in st and st["latest"] is None and not st["update_available"]


def test_check_can_be_switched_off(home, monkeypatch):
    monkeypatch.setenv("VWMCP_UPDATE_CHECK", "0")
    net = Net("99.0.0")
    monkeypatch.setattr(update, "_fetch", net)
    assert update.check()["disabled"] and not net.calls


def test_vw_update_downloads_and_opens_the_installer(home, monkeypatch):
    monkeypatch.setattr(update, "_fetch", Net("99.0.0"))
    opened = []
    monkeypatch.setattr(update, "open_installer", lambda p: opened.append(p) or True)
    out = generic.vw_update()
    assert out["version"] == "99.0.0" and out["installer_opened"]
    assert out["file"].endswith("Downloads/Keystone-for-Vectorworks-99.0.0.mcpb") and opened == [out["file"]]
    assert not list((home / "Downloads").glob(".Keystone*"))  # no half file left


def test_vw_update_when_up_to_date(home, monkeypatch):
    monkeypatch.setattr(update, "_fetch", Net(__version__))
    out = generic.vw_update()
    assert out["up_to_date"] and "file" not in out
    out = generic.vw_update(install=False, force=True)  # download anyway, don't open
    assert out["version"] == __version__ and "installer_opened" not in out


def test_vw_update_rejects_a_wrong_file(home, monkeypatch):
    net = Net("99.0.0")

    def bad(url, dest=None, timeout=5.0):
        if url == update.VERSION_URL:
            return net(url, dest, timeout)
        _mcpb(dest, "99.0.0", name="something-else")
        return None
    monkeypatch.setattr(update, "_fetch", bad)
    monkeypatch.setattr(update, "check", lambda **kw: {"current": __version__, "latest": "99.0.0",
                                                      "update_available": True})
    with pytest.raises(ToolError, match="not the Keystone"):
        generic.vw_update()
    (home / "Downloads").mkdir(exist_ok=True)
    assert not list((home / "Downloads").iterdir())


def test_vw_update_offline(home, monkeypatch):
    monkeypatch.setattr(update, "_fetch", Net("1", fail=True))
    with pytest.raises(ToolError, match="could not ask GitHub"):
        generic.vw_update()


# --- 0.6.5: every tool result insists on a pending update -------------------

def _cache(home, latest, age_s=0.0):
    import time
    path = home / "vwmcp" / "update.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"latest": latest, "checked_ts": time.time() - age_s}), encoding="utf-8")


def test_pending_reads_the_cache_only(home, monkeypatch):
    net = Net("99.0.0")
    monkeypatch.setattr(update, "_fetch", net)
    monkeypatch.setattr(update, "refresh_in_background", lambda: net.calls.append("bg"))
    assert update.pending() is None  # nothing known yet: no notice, no blocking request
    _cache(home, "99.0.0")
    p = update.pending()
    assert p == {"current": __version__, "latest": "99.0.0"}
    assert "vw_update" in update.notice(p) and "99.0.0" in update.notice(p) and __version__ in update.notice(p)
    assert not [c for c in net.calls if c != "bg"]  # never asks GitHub itself


def test_pending_none_when_up_to_date_or_switched_off(home, monkeypatch):
    monkeypatch.setattr(update, "refresh_in_background", lambda: None)
    _cache(home, __version__)
    assert update.pending() is None
    _cache(home, "99.0.0")
    monkeypatch.setenv("VWMCP_UPDATE_CHECK", "0")
    assert update.pending() is None


def test_stale_cache_refreshes_in_background_at_most_now_and_then(home, monkeypatch):
    started = []
    monkeypatch.setattr(update, "refresh_in_background", lambda: started.append(1))
    monkeypatch.setattr(update, "_last_bg", 0.0)
    _cache(home, "99.0.0", age_s=update.MAX_AGE_S + 60)
    assert update.pending()["latest"] == "99.0.0"  # the old answer still counts meanwhile
    update.pending()
    update.pending()
    assert started == [1]  # one refresh, not one per tool call


def _server_with_tools():
    from vwmcp.app import KeystoneServer

    srv = KeystoneServer("t")

    @srv.tool()
    def hello() -> dict:
        return {"a": 1}

    @srv.tool()
    def vw_update() -> dict:
        return {"up_to_date": False}

    return srv


def _texts(result):
    return [c.text for c in result.content if getattr(c, "type", None) == "text"]


def test_tool_results_carry_the_update_notice(home, monkeypatch):
    import anyio

    monkeypatch.setattr(update, "refresh_in_background", lambda: None)
    srv = _server_with_tools()
    res = anyio.run(srv.call_tool, "hello", {})
    assert len(_texts(res)) == 1 and not any("KEYSTONE UPDATE" in t for t in _texts(res))

    _cache(home, "99.0.0")
    res = anyio.run(srv.call_tool, "hello", {})
    notes = [t for t in _texts(res) if "KEYSTONE UPDATE" in t]
    assert len(notes) == 1 and "99.0.0" in notes[0] and "vw_update" in notes[0]
    assert json.loads(_texts(res)[0]) == {"a": 1}  # the tool's own result comes first, untouched
    # vw_update reports on the update itself: no extra note there
    res = anyio.run(srv.call_tool, "vw_update", {})
    assert not any("KEYSTONE UPDATE" in t for t in _texts(res))


def test_server_and_instructions_insist_on_updates():
    from vwmcp import app

    assert isinstance(app.mcp, app.KeystoneServer)
    text = app.INSTRUCTIONS
    assert "KEYSTONE UPDATE" in text and "vw_update" in text and "insist" in text.lower()
