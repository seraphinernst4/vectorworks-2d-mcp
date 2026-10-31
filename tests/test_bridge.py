import json
import os
import time

import pytest

import vwmcp_bridge as bridge


def write_job(root, job_id, code, **extra):
    (root / "jobs").mkdir(parents=True, exist_ok=True)
    job = {"id": job_id, "code": code, "created": time.time(), **extra}
    (root / "jobs" / f"{job_id}.json").write_text(json.dumps(job))


def read_result(root, job_id):
    return json.loads((root / "results" / f"{job_id}.json").read_text())


def test_runs_code_with_vs_in_scope_and_returns_last_expression(root, fake_vs):
    write_job(root, "a1", "x = 2\nvs.GetFName() + str(x)")
    assert bridge.run_pending(root, vs_module=fake_vs) == 1
    res = read_result(root, "a1")
    assert res["ok"] is True
    assert res["value"] == "Plot.vwx2"
    assert res["bridge_version"] == bridge.BRIDGE_VERSION


def test_captures_stdout(root, fake_vs):
    write_job(root, "a2", "print('hello')\nprint('world')")
    bridge.run_pending(root, vs_module=fake_vs)
    res = read_result(root, "a2")
    assert res["stdout"] == "hello\nworld\n"
    assert res["value"] is None


def test_explicit_result_variable_wins_over_last_statement(root, fake_vs):
    write_job(root, "a3", "result = {'n': 3}\nfor i in range(2):\n    pass")
    bridge.run_pending(root, vs_module=fake_vs)
    assert read_result(root, "a3")["value"] == {"n": 3}


def test_exception_is_reported_with_traceback(root, fake_vs):
    write_job(root, "a4", "print('before')\n1/0")
    bridge.run_pending(root, vs_module=fake_vs)
    res = read_result(root, "a4")
    assert res["ok"] is False
    assert "ZeroDivisionError" in res["error"]
    assert "ZeroDivisionError" in res["traceback"]
    assert res["stdout"] == "before\n"


def test_syntax_error_is_reported(root, fake_vs):
    write_job(root, "a5", "def (:")
    bridge.run_pending(root, vs_module=fake_vs)
    res = read_result(root, "a5")
    assert res["ok"] is False
    assert "SyntaxError" in res["error"]


def test_non_json_value_falls_back_to_repr(root, fake_vs):
    write_job(root, "a6", "object()")
    bridge.run_pending(root, vs_module=fake_vs)
    assert read_result(root, "a6")["value"].startswith("<object object")


def test_runs_one_job_per_call_oldest_first(root, fake_vs):
    # One job per trigger keeps one tool call == one Vectorworks undo step.
    # Job ids start with a zero-padded timestamp, so name order == age order.
    write_job(root, "0002-new", "2")
    write_job(root, "0001-old", "1")
    assert bridge.run_pending(root, vs_module=fake_vs) == 1
    assert (root / "results" / "0001-old.json").exists()
    assert not (root / "results" / "0002-new.json").exists()
    assert (root / "jobs" / "0002-new.json").exists()


def test_job_file_is_removed_after_running(root, fake_vs):
    write_job(root, "a7", "1")
    bridge.run_pending(root, vs_module=fake_vs)
    assert list((root / "jobs").iterdir()) == []


def test_expired_job_is_not_executed(root, fake_vs):
    write_job(root, "stale", "vs.calls.append('ran')", created=time.time() - 3600, ttl=60)
    bridge.run_pending(root, vs_module=fake_vs)
    res = read_result(root, "stale")
    assert res["ok"] is False
    assert "expired" in res["error"]
    assert fake_vs.calls == []


def test_no_jobs_is_a_noop(root, fake_vs):
    assert bridge.run_pending(root, vs_module=fake_vs) == 0


def test_corrupt_job_file_does_not_wedge_the_queue(root, fake_vs):
    (root / "jobs").mkdir(parents=True)
    (root / "jobs" / "bad.json").write_text("{not json")
    bridge.run_pending(root, vs_module=fake_vs)
    res = read_result(root, "bad")
    assert res["ok"] is False
    assert list((root / "jobs").iterdir()) == []


# ---------------------------------------------------------------- protocol


def test_claim_renames_to_working_and_result_lands_before_cleanup(root, fake_vs):
    seen = {}

    def spy(code, vs_module, libs=()):
        seen["jobs"] = sorted(p.name for p in (root / "jobs").iterdir())
        return 1

    orig = bridge._execute
    bridge._execute = spy
    try:
        write_job(root, "w1", "1")
        bridge.run_pending(root, vs_module=fake_vs)
    finally:
        bridge._execute = orig
    assert seen["jobs"] == ["w1.working"]
    assert list((root / "jobs").iterdir()) == []
    assert read_result(root, "w1")["ok"] is True
    assert list((root / "results").glob("*.tmp")) == []


def test_cancelled_job_is_never_run(root, fake_vs):
    write_job(root, "c1", "vs.calls.append('ran')")
    (root / "jobs" / "c1.json").rename(root / "jobs" / "c1.cancelled")
    assert bridge.run_pending(root, vs_module=fake_vs) == 0
    assert fake_vs.calls == []


def test_libs_run_before_code_and_are_cached(root, fake_vs, tmp_path):
    import hashlib

    src = "def helper():\n    return 'from lib'\n"
    lib = tmp_path / "lib.py"
    lib.write_text(src)
    sha = hashlib.sha256(src.encode()).hexdigest()
    ref = {"name": "lib", "sha": sha, "path": str(lib)}
    write_job(root, "l1", "helper()", libs=[ref])
    bridge.run_pending(root, vs_module=fake_vs)
    assert read_result(root, "l1")["value"] == "from lib"
    assert sha in bridge._CACHE["libs"]
    lib.unlink()  # cached: the file is not needed again in this session
    write_job(root, "l2", "helper()", libs=[ref])
    bridge.run_pending(root, vs_module=fake_vs)
    assert read_result(root, "l2")["value"] == "from lib"


def test_lib_with_wrong_checksum_is_refused(root, fake_vs, tmp_path):
    lib = tmp_path / "lib.py"
    lib.write_text("X = 1\n")
    write_job(root, "l3", "X", libs=[{"name": "lib", "sha": "0" * 64, "path": str(lib)}])
    bridge.run_pending(root, vs_module=fake_vs)
    assert "checksum" in read_result(root, "l3")["error"]


# ----------------------------------------------------------- checked proxy


def make_vs():
    """A fake `vs` whose functions carry Vectorworks-style docstrings."""
    import types

    vs = types.ModuleType("vs")
    vs.calls = []

    def fn(name, definition):
        def f(*args):
            vs.calls.append((name, args))
            return name

        f.__doc__ = "Does things. Definition: " + definition
        setattr(vs, name, f)

    fn("Rect", "vs.Rect(p1, p2)")
    fn("SetFillBack", "vs.SetFillBack(h, color)")
    fn("HMove", "vs.HMove(h, xOffset, yOffset)")
    fn("ForEachObject", "vs.ForEachObject(callback, c)")
    fn("Locus3D", "vs.Locus3D(p)")
    fn("Changed", "vs.Changed(a, b, c)")  # live API disagrees with the index
    fn("GetFName", "vs.GetFName()")
    vs.Unindexed = lambda *a: vs.calls.append(("Unindexed", a))
    vs.kConstant = 7
    return vs


INDEX = {"f": {
    "Rect": {"a": "p1:POINT, p2:POINT", "t": "PP"},
    "SetFillBack": {"a": "h:HANDLE, color:COLOR", "t": "HC"},
    "HMove": {"a": "h:HANDLE, xOffset:REAL, yOffset:REAL", "t": "HRR"},
    "ForEachObject": {"a": "callback:PROCEDURE, c:CRITERIA", "t": "FS"},
    "Locus3D": {"a": "p:POINT3D", "t": "Q"},
    "Changed": {"a": "a:REAL, b:REAL", "t": "RR"},
    "Missing": {"a": "a:REAL", "t": "R"},
    "GetFName": {"a": "", "t": ""},
}}


@pytest.fixture
def checked(tmp_path):
    path = tmp_path / "vs_index.json"
    path.write_text(json.dumps(INDEX))
    vs = make_vs()
    table = bridge.load_check_table(vs, str(path))
    return vs, bridge.CheckedVS(vs, table), table


def test_check_table_follows_the_live_api(checked):
    vs, proxy, table = checked
    assert set(table) == {"Rect", "SetFillBack", "HMove", "ForEachObject", "Locus3D", "GetFName"}


def test_functions_without_parameters_are_checked_too(checked):
    vs, proxy, _ = checked
    assert proxy.GetFName() == "GetFName"
    with pytest.raises(bridge.VSArgumentError, match="too many"):
        proxy.GetFName("x")


@pytest.mark.parametrize("args", [((0, 0), (1, 1)), (0, 0, 1, 1), ((0, 0), 1, 1), ((0, 0, 0), (1, 1, 0))])
def test_points_as_tuples_or_numbers_pass(checked, args):
    vs, proxy, _ = checked
    assert proxy.Rect(*args) == "Rect"
    assert vs.calls[-1] == ("Rect", args)


@pytest.mark.parametrize("args,why", [
    ((0, 0, 1), "POINT"),
    ((0, 0, 1, 1, 2), "too many"),
    (((0, 0),), "POINT"),
    (((0,), (1, 1)), "tuple of 1"),
    (("0", 0, 1, 1), "POINT"),
])
def test_wrong_rect_calls_raise_before_vectorworks(checked, args, why):
    vs, proxy, _ = checked
    with pytest.raises(bridge.VSArgumentError) as e:
        proxy.Rect(*args)
    assert why in str(e.value) and "vs.Rect(p1:POINT, p2:POINT)" in str(e.value)
    assert vs.calls == []


def test_number_tuple_may_cover_consecutive_number_parameters():
    # ArcByCenter(x, y, radius, start, sweep): maybe Vectorworks flattens
    # (x, y); the check must never block what Vectorworks could accept.
    assert bridge.check_args("RRRRR", ((0.0, 0.0), 1.0, 0.0, 360.0)) is None
    assert bridge.check_args("RRRRR", (0.0, 0.0, 1.0, 0.0, 360.0)) is None
    assert "too few" in bridge.check_args("RRRRR", ((0.0, 0.0), 1.0, 0.0))
    assert "tuple of 3" in bridge.check_args("HRR", (object(), (1.0, 2.0, 3.0)))
    assert bridge.check_args("HRR", (object(), (1.0, 2.0))) is None
    assert "tuple of 2" in bridge.check_args("RS", ((1.0, 2.0), "x"))


def test_colour_must_be_one_tuple(checked):
    vs, proxy, _ = checked
    proxy.SetFillBack("h-placeholder" and object(), (65535, 0, 0))
    with pytest.raises(bridge.VSArgumentError, match="COLOR"):
        proxy.SetFillBack(object(), 65535, 0, 0)
    assert len(vs.calls) == 1


def test_text_for_numbers_or_handles_is_refused(checked):
    vs, proxy, _ = checked
    with pytest.raises(bridge.VSArgumentError, match="REAL"):
        proxy.HMove(object(), "1", 2)
    with pytest.raises(bridge.VSArgumentError, match="GetObjectByUuid"):
        proxy.HMove("3F2504E0-4F89-11D3-9A0C-0305E82C3301", 1, 2)
    proxy.HMove(object(), 1, 2.5)
    assert len(vs.calls) == 1


def test_callbacks_must_name_their_parameters(checked):
    vs, proxy, _ = checked

    def ok(h):
        pass

    proxy.ForEachObject(ok, "(ALL)")
    with pytest.raises(bridge.VSArgumentError, match=r"\*args"):
        proxy.ForEachObject(lambda *a: None, "(ALL)")
    with pytest.raises(bridge.VSArgumentError, match="not callable"):
        proxy.ForEachObject("ok", "(ALL)")


def test_unchecked_names_pass_through(checked):
    vs, proxy, _ = checked
    proxy.Unindexed(1, 2, 3, 4, 5)
    proxy.Changed(1, 2, 3)
    assert proxy.kConstant == 7
    assert proxy.Locus3D(1, 2, 3) == "Locus3D"
    with pytest.raises(AttributeError):
        proxy.NoSuchFunction
    assert "Rect" in dir(proxy)


def test_proxy_is_used_in_jobs_and_for_import_vs(root, tmp_path, monkeypatch):
    path = tmp_path / "vs_index.json"
    path.write_text(json.dumps(INDEX))
    monkeypatch.setattr(bridge, "_HERE", str(tmp_path))
    bridge._CACHE["index_key"] = None
    vs = make_vs()
    write_job(root, "p1", "vs.Rect(0, 0, 1)")
    write_job(root, "p2", "import vs as v2\nv2.Rect(0, 0, 1, 1)\ntype(v2).__name__")
    bridge.run_pending(root, vs_module=vs)
    bridge.run_pending(root, vs_module=vs)
    r1, r2 = read_result(root, "p1"), read_result(root, "p2")
    assert r1["ok"] is False and "VSArgumentError" in r1["error"] and "vs.Rect" in r1["error"]
    assert r2["ok"] is True and r2["value"] == "CheckedVS"
    assert vs.calls == [("Rect", (0, 0, 1, 1))]
    import sys
    assert type(sys.modules.get("vs")).__name__ != "CheckedVS"  # restored after the job


def test_check_can_be_switched_off_per_job(root, tmp_path, monkeypatch):
    path = tmp_path / "vs_index.json"
    path.write_text(json.dumps(INDEX))
    monkeypatch.setattr(bridge, "_HERE", str(tmp_path))
    bridge._CACHE["index_key"] = None
    vs = make_vs()
    write_job(root, "n1", "vs.Rect(0, 0, 1)", check=False)
    bridge.run_pending(root, vs_module=vs)
    assert read_result(root, "n1")["ok"] is True and vs.calls == [("Rect", (0, 0, 1))]


def test_broken_index_never_blocks_a_job(root, tmp_path, monkeypatch, fake_vs):
    (tmp_path / "vs_index.json").write_text("{broken")
    monkeypatch.setattr(bridge, "_HERE", str(tmp_path))
    bridge._CACHE["index_key"] = None
    write_job(root, "b1", "vs.GetFName()")
    bridge.run_pending(root, vs_module=fake_vs)
    res = read_result(root, "b1")
    assert res["ok"] is True and res["value"] == "Plot.vwx" and "unavailable" in res["warning"]


def test_bridge_reloads_itself_when_its_file_changes(tmp_path, root, fake_vs):
    import importlib.util
    import shutil
    import sys

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    shutil.copy(bridge.__file__, pkg / "ks_bridge_copy.py")
    spec = importlib.util.spec_from_file_location("ks_bridge_copy", pkg / "ks_bridge_copy.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ks_bridge_copy"] = mod
    spec.loader.exec_module(mod)
    try:
        text = (pkg / "ks_bridge_copy.py").read_text().replace(
            'BRIDGE_VERSION = "%s"' % bridge.BRIDGE_VERSION, 'BRIDGE_VERSION = "9.9.9"')
        (pkg / "ks_bridge_copy.py").write_text(text)
        later = mod._LOADED_MTIME + 5
        os.utime(pkg / "ks_bridge_copy.py", (later, later))
        write_job(root, "r1", "1")
        old_run = mod.run_pending
        old_run(root, vs_module=fake_vs)
        assert read_result(root, "r1")["bridge_version"] == "9.9.9"
        assert sys.modules["ks_bridge_copy"].BRIDGE_VERSION == "9.9.9"
    finally:
        sys.modules.pop("ks_bridge_copy", None)
