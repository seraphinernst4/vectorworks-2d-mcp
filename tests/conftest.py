import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vw-plugin" / "vwmcp"))


@pytest.fixture
def fake_vs():
    """A stand-in for Vectorworks' built-in `vs` module."""
    vs = types.ModuleType("vs")
    vs.GetFName = lambda: "Plot.vwx"
    vs.calls = []
    return vs


@pytest.fixture
def root(tmp_path):
    return tmp_path / "vwmcp"


@pytest.fixture(autouse=True)
def _no_update_check(monkeypatch):
    """Tests never ask GitHub for a newer Keystone (test_update fakes it)."""
    monkeypatch.setenv("VWMCP_UPDATE_CHECK", "0")
