"""The launcher (mcpb/server/profile.js) builds exactly the profile block Python builds."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from vwmcp import app, profile

JS = Path(__file__).resolve().parents[1] / "mcpb" / "server" / "profile.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def _js(root: Path, instructions: str) -> str:
    static = {"table": profile.table(), "defaultBlock": profile.instructions_block(profile.defaults())}
    script = ("const p = require(%s); const s = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
              "process.stdout.write(p.withProfile(s.instructions, s.profile, s.root));" % json.dumps(str(JS)))
    out = subprocess.run([NODE, "-e", script], input=json.dumps({"instructions": instructions, "profile": static,
                                                                 "root": str(root)}),
                         capture_output=True, text=True, check=True)
    return out.stdout


@pytest.mark.parametrize("stored", [
    {},
    {"detail": "simplify_ok", "merge_parts": "always", "check_mode": "report"},
    {"confirm_above": 0, "tolerance_mm": 2.5, "simplify_paper_mm": 0.05},
    {"rules": ["Ruinen organisch lassen", "Schnittlinien 0,5 mm – immer"], "areas": "loose"},
    {"detail": "nonsense", "tolerance_mm": -1, "rules": [3]},  # invalid values fall back like in Python
])
def test_launcher_block_equals_python(tmp_path, monkeypatch, stored):
    root = tmp_path / "home"
    root.mkdir()
    (root / "profile.json").write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("VWMCP_HOME", str(root))
    default_text = app.build_instructions(profile.defaults())
    assert _js(root, default_text) == app.build_instructions(profile.load())


def test_launcher_without_profile_file_keeps_defaults(tmp_path):
    text = app.build_instructions(profile.defaults())
    assert _js(tmp_path, text) == text
    assert _js(tmp_path, "other instructions") == "other instructions"
