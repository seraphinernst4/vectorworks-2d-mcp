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
