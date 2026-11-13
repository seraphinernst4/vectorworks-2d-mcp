"""Several documents: the document pin (guard inside Vectorworks), the
window list heuristics, and the switch/open tools with a fake macOS."""

import ast
import json
import subprocess
import threading
from pathlib import Path

import pytest
import vwmcp_bridge
from mcp.server.mcpserver.exceptions import ToolError

from vwmcp import app, documents, profile
from vwmcp import trigger as trig
from vwmcp.client import Bridge, BridgeTimeout, WrongDocument
from vwmcp.journal import Journal
from vwmcp.tools import documents as T
from vwmcp.tools import generic


class Runner:
    def __init__(self, root, vs):
        self.root, self.vs = root, vs

    def __call__(self):
        threading.Thread(
            target=vwmcp_bridge.run_pending, args=(self.root,), kwargs={"vs_module": self.vs}
        ).start()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def vs(fake_vs):
    fake_vs.doc = {"name": "Plan.vwx", "path": "/Users/s/Plan.vwx"}
    fake_vs.GetFName = lambda: fake_vs.doc["name"]
    fake_vs.GetFPathName = lambda: fake_vs.doc["path"]
    fake_vs.GetVersionEx = lambda: (31, 3, 0, 1, 846827)
    fake_vs.touched = []
    fake_vs.Touch = lambda: fake_vs.touched.append(fake_vs.doc["name"])
    return fake_vs


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def pin(clock):
    return documents.DocumentPin(idle=900, clock=clock, enabled=True)


@pytest.fixture(autouse=True)
def bridge(root, vs, pin, monkeypatch):
    b = Bridge(root=root, trigger=Runner(root, vs), poll_interval=0.01, timeout=2)
    b.guard = pin
    monkeypatch.setattr(app, "get_bridge", lambda: b)
    monkeypatch.setattr(app, "get_journal", lambda: Journal(root / "journal.jsonl"))
    monkeypatch.setattr(documents, "PIN", pin)
    return b


# ------------------------------------------------------------------ guard


def test_guard_source_is_python39():
    src = documents.guard_source({"name": "Pläne \"A\".vwx", "path": "Macintosh HD:Users:s:Pläne.vwx"})
    ast.parse(src, feature_version=(3, 9))


def test_first_call_pins_the_document_in_front(bridge, pin, vs):
    res = bridge.run("vs.Touch()\n1")
    assert res["ok"] and vs.touched == ["Plan.vwx"]
    assert pin.current() == {"name": "Plan.vwx", "path": "/Users/s/Plan.vwx"}


def test_other_document_in_front_stops_before_anything_runs(bridge, pin, vs):
    bridge.run("1")
    vs.doc = {"name": "Other.vwx", "path": "/Users/s/Other.vwx"}
    with pytest.raises(WrongDocument) as e:
        bridge.run("vs.Touch()")
    assert vs.touched == [] and e.value.never_ran
    msg = str(e.value)
    assert "WRONG DOCUMENT" in msg and "'Other.vwx'" in msg and "'Plan.vwx'" in msg
    assert "vw_switch_document(document='Other.vwx')" in msg
    assert e.value.active["name"] == "Other.vwx" and e.value.expected["name"] == "Plan.vwx"


def test_unguarded_calls_run_in_any_document(bridge, vs):
    bridge.run("1")
    vs.doc = {"name": "Other.vwx", "path": "/Users/s/Other.vwx"}
    assert bridge.run("vs.Touch()\n2", guarded=False)["value"] == 2
    assert vs.touched == ["Other.vwx"]


def test_same_name_in_another_folder_is_another_document(bridge, vs):
    bridge.run("1")
    vs.doc = {"name": "Plan.vwx", "path": "/Users/s/Alt/Plan.vwx"}
    with pytest.raises(WrongDocument):
        bridge.run("vs.Touch()")


def test_untitled_documents_compare_by_name(bridge, vs):
    vs.doc = {"name": "Ohne Titel 1", "path": ""}
    bridge.run("1")
    assert bridge.run("vs.Touch()\n3")["value"] == 3
    vs.doc = {"name": "Ohne Titel 2", "path": ""}
    with pytest.raises(WrongDocument):
        bridge.run("vs.Touch()")


def test_hfs_paths_and_unicode_forms_match(bridge, pin, vs):
    pin.set({"name": "Pläne.vwx", "path": "Macintosh HD:Users:s:Pläne.vwx"})  # NFC
    vs.doc = {"name": "Pläne.vwx", "path": "Macintosh HD:Users:s:Pläne.vwx"}  # NFD
    assert bridge.run("vs.Touch()\n4")["value"] == 4


def test_errors_after_the_guard_are_normal_errors(bridge, pin):
    bridge.run("1")
    res = bridge.run("raise ValueError('bad')")
    assert not res["ok"] and "bad" in res["error"]
    assert pin.current() is not None


def test_pin_lapses_after_a_pause(bridge, pin, clock, vs):
    bridge.run("1")
    clock.t += 600
    bridge.run("1")  # a call keeps it alive
    clock.t += 600
    vs.doc = {"name": "Other.vwx", "path": "/Users/s/Other.vwx"}
    assert pin.current() is not None
    clock.t += 400
    assert bridge.run("vs.Touch()\n5")["value"] == 5  # pause over: works where the user is now
    assert pin.current()["name"] == "Other.vwx"


def test_follow_profile_turns_the_guard_off(bridge, vs, monkeypatch, tmp_path):
    monkeypatch.setenv("VWMCP_HOME", str(tmp_path / "home"))
    p = documents.DocumentPin()
    bridge.guard = p
    assert p.enabled()
    profile.save({"documents": "follow"})
    assert not p.enabled()
    bridge.run("1")
    vs.doc = {"name": "Other.vwx", "path": "/x/Other.vwx"}
    assert bridge.run("vs.Touch()\n6")["value"] == 6


def test_env_switch_turns_the_guard_off(monkeypatch):
    monkeypatch.setenv("VWMCP_DOC_GUARD", "0")
    assert not documents.DocumentPin().enabled()


def test_no_document_name_means_no_guard(bridge, pin, vs):
    vs.doc = {"name": "", "path": ""}
    assert bridge.run("7")["value"] == 7 and pin.current() is None


def test_old_bridge_without_libs_gets_the_guard_inline(bridge, pin, vs, monkeypatch):
    monkeypatch.setattr(Bridge, "supports_libs", lambda self: False)
    bridge.run("1")
    vs.doc = {"name": "Other.vwx", "path": "/x/Other.vwx"}
    with pytest.raises(WrongDocument):
        bridge.run("vs.Touch()")
    assert vs.touched == []


def test_claim_timeout_per_call(root, vs):
    b = Bridge(root=root, trigger=lambda: None, poll_interval=0.01, timeout=2, claim_timeout=30)
    with pytest.raises(BridgeTimeout) as e:
        b.run("1", claim_timeout=0.1)
    assert e.value.never_ran and "0.1s" in str(e.value)


# ------------------------------------------- existing tools with the guard


def test_exec_python_in_wrong_document_is_refused_and_not_journaled(vs, root):
    generic.vw_exec_python("1", modifies_document=False)
    vs.doc = {"name": "Other.vwx", "path": "/x/Other.vwx"}
    with pytest.raises(ToolError, match="WRONG DOCUMENT"):
        generic.vw_exec_python("vs.Touch()")
    assert vs.touched == [] and not (root / "journal.jsonl").exists()


def test_revert_in_wrong_document_keeps_the_step(vs, root):
    j = Journal(root / "journal.jsonl")
    j.record("Plan.vwx", "vw_insert_symbol", [{"op": "created", "uuid": "u1"}])
    generic.vw_exec_python("1", modifies_document=False)  # pins Plan.vwx
    vs.doc = {"name": "Other.vwx", "path": "/x/Other.vwx"}
    with pytest.raises(ToolError, match="WRONG DOCUMENT"):
        generic.vw_revert_last()
    assert j.peek("Plan.vwx")["tool"] == "vw_insert_symbol"


def test_ping_and_document_info_work_in_any_document(vs, monkeypatch):
    generic.vw_exec_python("1", modifies_document=False)
    vs.doc = {"name": "Other.vwx", "path": "/x/Other.vwx"}
    assert generic.vw_ping()["document"] == "Other.vwx"
    monkeypatch.setattr(app.calls, "call", lambda b, fn, _guarded=True, **kw: (
        {"name": "Other.vwx", "path": "/x/Other.vwx", "guarded": _guarded}))
    info = generic.vw_document_info()
    assert info["guarded"] is False
    assert info["keystone_document"] == "Plan.vwx" and "vw_switch_document" in info["note"]


# ----------------------------------------------------------- names, paths


def test_names_and_paths():
    assert documents.same_name("Plan.vwx", "plan") and documents.same_name("Plän", "Pläne"[:4])
    assert not documents.same_name("", "")
    assert documents.posix_path("Macintosh HD:Users:s:a/b.vwx") == "/Users/s/a:b.vwx"
    assert documents.posix_path("/Users/s/x.vwx") == "/Users/s/x.vwx"
    assert documents.same_file("Macintosh HD:Users:s:P.vwx", "/Users/s/P.vwx")
    assert documents.same_file("Daten:Projekte:P.vwx", "/Volumes/Daten/Projekte/P.vwx")
    assert documents.same_file("/Users/s/P.vwx", "/Users/s/p.vwx")
    assert not documents.same_file("/Users/s/P.vwx", "/Users/t/P.vwx")
    assert not documents.same_file("P.vwx", "/Users/s/P.vwx")


UI = {
    "frontmost": False,
    "windows": [
        {"name": "Plan.vwx", "subrole": "AXStandardWindow", "main": True},
        {"name": "Attribute", "subrole": "AXFloatingWindow", "main": False},
        {"name": "Detail.vwx", "subrole": "AXStandardWindow", "main": False},
    ],
    "menu": [
        {"name": "Paletten", "mark": None},
        {"name": None, "mark": None},
        {"name": "Plan.vwx", "mark": "✓"},
        {"name": "Detail.vwx", "mark": None},
        {"name": "Schnitt.vwx", "mark": None},  # in a background tab: no own AX window
    ],
}


def test_documents_from_window_menu_include_background_tabs():
    docs = documents.documents_from_ui(UI)
    assert [d["name"] for d in docs] == ["Plan.vwx", "Detail.vwx", "Schnitt.vwx"]
    assert [d["active"] for d in docs] == [True, False, False]


def test_documents_from_windows_when_the_menu_has_no_documents():
    ui = dict(UI, menu=[{"name": "Paletten"}, {"name": None}, {"name": "Arbeitsumgebung"}])
    assert [d["name"] for d in documents.documents_from_ui(ui)] == ["Plan.vwx", "Detail.vwx"]
    assert [d["name"] for d in documents.documents_from_ui(dict(UI, menu=None))] == ["Plan.vwx", "Detail.vwx"]


def test_dialogs_are_found_and_are_no_documents():
    ui = dict(UI, windows=UI["windows"] + [{"name": "Konvertieren", "subrole": "AXDialog", "modal": True}])
    assert documents.dialog_of(ui) == "Konvertieren"
    assert documents.dialog_of(UI) is None
    assert "Konvertieren" not in [d["name"] for d in documents.documents_from_ui(ui)]


def test_match_document():
    docs = documents.documents_from_ui(UI)
    assert documents.match_document(docs, "detail")["name"] == "Detail.vwx"
    assert documents.match_document(docs, "/Users/s/Schnitt.vwx")["name"] == "Schnitt.vwx"
    assert documents.match_document(docs, "Schni")["name"] == "Schnitt.vwx"
    with pytest.raises(documents.DocumentsError, match="several"):
        documents.match_document(docs, ".vwx")
    with pytest.raises(documents.DocumentsError, match="vw_open_document"):
        documents.match_document(docs, "Lageplan")


def test_osascript_errors_are_explained():
    assert "Automation" in documents.explain_osa_error("execution error: Not authorized to send Apple events (-1743)")
    assert "Bedienungshilfen" in documents.explain_osa_error("osascript is not allowed assistive access. (-1719)")
    assert "failed" in documents.explain_osa_error("boom")


def test_run_js_uses_osascript_and_maps_failures():
    calls = []

    def ok(args, **kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, '{"windows": []}\n', "")

    assert documents.run_js("JS", "12", runner=ok) == '{"windows": []}'
    assert calls[0][:4] == ["osascript", "-l", "JavaScript", "-e"] and calls[0][-1] == "12"

    def denied(args, **kw):
        return subprocess.CompletedProcess(args, 1, "", "Not authorized to send Apple events (-1743)")

    with pytest.raises(documents.DocumentsError, match="System Events"):
        documents.run_js("JS", runner=denied)

    def slow(args, **kw):
        raise subprocess.TimeoutExpired(args, 1)

    with pytest.raises(documents.DocumentsError, match="in time"):
        documents.run_js("JS", runner=slow)


def test_open_file_command():
    seen = []

    def run(args, **kw):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    documents.open_file("/Applications/Vectorworks 2026/Vectorworks 2026.app", "/x/P.vwx", False, runner=run)
    documents.open_file("/A.app", "/x/P.vwx", True, runner=run)
    assert seen == [["open", "-g", "-a", "/Applications/Vectorworks 2026/Vectorworks 2026.app", "/x/P.vwx"],
                    ["open", "-a", "/A.app", "/x/P.vwx"]]


def test_ps_processes_with_pid_and_bundle():
    out = trig.parse_ps_processes(
        " 812 01:02:03 /Applications/Vectorworks 2026/Vectorworks 2026.app/Contents/MacOS/Vectorworks\n"
        " 900 00:05 /Applications/Vectorworks 2027/Vectorworks 2027.app/Contents/MacOS/Vectorworks -psn_0_1\n"
        "  77 00:01 /usr/bin/true\n")
    assert [(p["name"], p["pid"], p["uptime"]) for p in out] == [("Vectorworks 2027", 900, 5.0),
                                                                ("Vectorworks 2026", 812, 3723.0)]
    assert out[1]["bundle"] == "/Applications/Vectorworks 2026/Vectorworks 2026.app"


def test_check_openable(tmp_path):
    f = tmp_path / "P.vwx"
    f.write_text("x")
    assert documents.check_openable(str(f)) == f
    with pytest.raises(documents.DocumentsError, match="full path"):
        documents.check_openable("P.vwx")
    with pytest.raises(documents.DocumentsError, match="no file"):
        documents.check_openable(str(tmp_path / "missing.vwx"))
    d = tmp_path / "a.dwg"
    d.write_text("x")
    with pytest.raises(documents.DocumentsError, match="vw2d_import"):
        documents.check_openable(str(d))


# --------------------------------------------------- tools with fake macOS


class FakeMac:
    """Vectorworks' windows as macOS shows them; switching and opening
    change the fake `vs` document like Vectorworks would."""

    def __init__(self, vs, docs):
        self.vs = vs
        self.docs = docs  # name -> path
        self.front = []
        self.switches = []
        self.opened = []
        self.dialog = None
        self.uptime = 3600.0
        self.ignore_background_switch = False
        self.running = True
        self.drop_while_loading = 0

    def ui(self, pid):
        wins = [{"name": n, "subrole": "AXStandardWindow", "main": n == self.vs.doc["name"]} for n in self.docs]
        if not self.docs:
            wins.append({"name": "Vectorworks Design Suite 2026", "subrole": "AXStandardWindow", "main": True})
        if self.dialog:
            wins.append({"name": self.dialog, "subrole": "AXDialog", "modal": True})
        menu = [{"name": "Paletten"}, {"name": None}] + [
            {"name": n, "mark": "✓" if n == self.vs.doc["name"] else None} for n in self.docs]
        return {"windows": wins, "menu": menu}

    def switch(self, pid, title, front):
        self.switches.append((title, front))
        if title not in self.docs:
            return "not_found"
        if front or not self.ignore_background_switch:
            self.vs.doc = {"name": title, "path": self.docs[title]}
        return "menu"

    def open(self, bundle, path, front, runner=None):
        self.opened.append((bundle, path, front))
        self.running = True
        if path is None:
            return  # only started
        if self.drop_while_loading:
            self.drop_while_loading -= 1
            return  # still loading: not visible yet
        name = Path(path).name if not path.endswith(".sta") else "Ohne Titel 1"
        self.docs[name] = path if not path.endswith(".sta") else ""
        self.vs.doc = {"name": name, "path": self.docs[name]}
        self.running = True

    def process(self, processes=None):
        if not self.running:
            return None
        return {"name": "Vectorworks 2026", "pid": 812, "uptime": self.uptime,
                "bundle": "/Applications/Vectorworks 2026/Vectorworks 2026.app"}


@pytest.fixture
def mac(vs, monkeypatch):
    m = FakeMac(vs, {"Plan.vwx": "/Users/s/Plan.vwx", "Detail.vwx": "/Users/s/Detail.vwx"})
    monkeypatch.setattr(documents, "find_process", m.process)
    monkeypatch.setattr(documents, "read_ui", m.ui)
    monkeypatch.setattr(documents, "switch_window", m.switch)
    monkeypatch.setattr(documents, "open_file", m.open)
    monkeypatch.setattr(documents, "installed_bundle", lambda: "/Applications/Vectorworks 2026/Vectorworks 2026.app")
    clock = Clock()

    def sleep(s):
        clock.t += s

    monkeypatch.setattr(T, "_sleep", sleep)
    monkeypatch.setattr(T, "_clock", clock)
    return m


def test_vw_documents_lists_active_and_keystone_document(mac, vs):
    generic.vw_exec_python("1", modifies_document=False)
    out = T.vw_documents()
    assert out["application"] == "Vectorworks 2026" and out["guard"] == "on"
    assert out["documents"] == [{"name": "Plan.vwx", "active": True, "keystone": True},
                                {"name": "Detail.vwx", "active": False, "keystone": False}]
    assert out["active"] == {"name": "Plan.vwx", "path": "/Users/s/Plan.vwx"}
    assert out["keystone_document"]["name"] == "Plan.vwx"


def test_vw_documents_reports_a_dialog_instead_of_waiting(mac):
    mac.dialog = "Sichern?"
    out = T.vw_documents()
    assert out["dialog"] == "Sichern?" and "active" not in out


def test_switch_brings_the_document_to_front_and_pins_it(mac, vs, pin):
    out = T.vw_switch_document("detail")
    assert out["switched"] and out["document"] == {"name": "Detail.vwx", "path": "/Users/s/Detail.vwx"}
    assert mac.switches == [("Detail.vwx", False)]
    assert pin.current()["name"] == "Detail.vwx"
    generic.vw_exec_python("vs.Touch()")
    assert vs.touched == ["Detail.vwx"]


def test_switch_retries_with_vectorworks_in_front(mac, vs):
    mac.ignore_background_switch = True
    out = T.vw_switch_document("Detail.vwx")
    assert mac.switches == [("Detail.vwx", False), ("Detail.vwx", True)]
    assert "front" in out["method"]


def test_switch_to_the_document_in_front_only_takes_it_over(mac, vs, pin):
    generic.vw_exec_python("1", modifies_document=False)  # pinned Plan.vwx
    vs.doc = {"name": "Detail.vwx", "path": "/Users/s/Detail.vwx"}  # the user clicked Detail
    with pytest.raises(ToolError, match="WRONG DOCUMENT"):
        generic.vw_exec_python("vs.Touch()")
    out = T.vw_switch_document("Detail.vwx")
    assert out["switched"] is False and mac.switches == []
    generic.vw_exec_python("vs.Touch()")
    assert vs.touched == ["Detail.vwx"]


def test_switch_refuses_unknown_documents_and_dialogs(mac):
    with pytest.raises(ToolError, match="no open document"):
        T.vw_switch_document("Lageplan")
    mac.dialog = "Konvertieren"
    with pytest.raises(ToolError, match="dialog"):
        T.vw_switch_document("Detail")


def test_open_a_file_and_work_in_it(mac, vs, pin, tmp_path):
    f = tmp_path / "Lageplan.vwx"
    f.write_text("x")
    out = T.vw_open_document(str(f))
    assert out["opened"] and out["document"]["name"] == "Lageplan.vwx"
    assert mac.opened == [("/Applications/Vectorworks 2026/Vectorworks 2026.app", str(f), False)]
    assert pin.current()["name"] == "Lageplan.vwx"


def test_open_an_open_file_only_switches(mac, vs, tmp_path):
    f = tmp_path / "Detail.vwx"
    f.write_text("x")
    mac.docs["Detail.vwx"] = str(f)
    out = T.vw_open_document(str(f))
    assert out["opened"] is False and mac.opened == [] and mac.switches == [("Detail.vwx", False)]


def test_open_refuses_a_namesake_from_another_folder(mac, vs, tmp_path):
    f = tmp_path / "Detail.vwx"
    f.write_text("x")
    with pytest.raises(ToolError, match="Another file called"):
        T.vw_open_document(str(f))


def test_open_a_template_makes_a_new_document(mac, vs, tmp_path):
    f = tmp_path / "Büro.sta"
    f.write_text("x")
    out = T.vw_open_document(str(f))
    assert out["document"]["name"] == "Ohne Titel 1" and "template" in out["note"]


def test_open_starts_vectorworks_first_then_hands_over_the_file(mac, vs, tmp_path):
    mac.running = False
    mac.docs.clear()
    f = tmp_path / "Neu.vwx"
    f.write_text("x")
    out = T.vw_open_document(str(f))
    assert out["opened"] and out["document"]["name"] == "Neu.vwx"
    bundle = "/Applications/Vectorworks 2026/Vectorworks 2026.app"
    assert mac.opened == [(bundle, None, False), (bundle, str(f), False)]


def test_open_reports_starting_when_vectorworks_takes_long(mac, vs, tmp_path, monkeypatch):
    mac.running = False
    real = mac.open

    def slow_start(bundle, path, front, runner=None):
        real(bundle, path, front)
        mac.running = False  # not up within this call

    monkeypatch.setattr(documents, "open_file", slow_start)
    f = tmp_path / "Neu.vwx"
    f.write_text("x")
    out = T.vw_open_document(str(f))
    assert out["status"] == "starting" and "again" in out["note"]


def test_open_reports_a_dialog_from_vectorworks(mac, vs, tmp_path, monkeypatch):
    f = tmp_path / "Alt.vwx"
    f.write_text("x")
    real_open = mac.open

    def open_with_question(*a, **kw):
        real_open(*a, **kw)
        mac.dialog = "Datei konvertieren"

    monkeypatch.setattr(documents, "open_file", open_with_question)
    with pytest.raises(ToolError, match="Datei konvertieren"):
        T.vw_open_document(str(f))


def test_a_slow_file_is_picked_up_by_the_next_call_and_never_opened_twice(mac, vs, tmp_path, monkeypatch):
    monkeypatch.setattr(T, "_PENDING", {})
    f = tmp_path / "Riesig.vwx"
    f.write_text("x")
    mac.drop_while_loading = 1
    out = T.vw_open_document(str(f), wait_s=10)
    assert out["status"] == "loading" and "again" in out["note"]
    mac.docs["Riesig.vwx"] = str(f)  # Vectorworks finished loading meanwhile
    vs.doc = {"name": "Riesig.vwx", "path": str(f)}
    out = T.vw_open_document(str(f))
    assert out["opened"] and out["document"]["name"] == "Riesig.vwx"
    assert [o[1] for o in mac.opened] == [str(f)]  # handed over once


def test_one_call_stays_under_the_remote_limit(mac, vs, tmp_path, monkeypatch):
    monkeypatch.setattr(T, "_PENDING", {})
    f = tmp_path / "Riesig.vwx"
    f.write_text("x")
    mac.drop_while_loading = 1
    start = T._clock()
    T.vw_open_document(str(f), wait_s=600)
    assert T._clock() - start <= T.STEP_S + 1


def test_dialog_text_and_buttons_are_reported():
    ui = {"windows": [{"name": None, "subrole": "AXDialog", "texts": ["Vectorworks-Bibliotheken aktualisieren",
                                                                    "Die Liste ... ist nicht mehr aktuell."],
                       "buttons": ["Schließen", "Aktualisieren"]}]}
    d = documents.dialog_of(ui)
    assert d.startswith("untitled dialog - Vectorworks-Bibliotheken") and "buttons: Schließen, Aktualisieren" in d


def test_start_screen_is_no_document():
    ui = {"windows": [{"name": "Vectorworks Design Suite 2027", "subrole": "AXStandardWindow", "main": True}],
          "menu": [{"name": "Paletten"}]}
    assert documents.documents_from_ui(ui) == []


def test_start_the_version_the_user_works_with(monkeypatch, tmp_path):
    monkeypatch.setenv("VWMCP_HOME", str(tmp_path))
    monkeypatch.delenv("VWMCP_APP", raising=False)
    apps = [Path("/Applications/Vectorworks 2026/Vectorworks 2026.app"),
            Path("/Applications/Vectorworks 2027/Vectorworks 2027.app")]
    tested = ["Vectorworks 2026"]
    assert documents.installed_bundle(apps, tested).endswith("Vectorworks 2026.app")  # newest tested
    assert documents.installed_bundle(apps, []).endswith("Vectorworks 2027.app")  # nothing tested: newest
    trig.remember_app(tmp_path, "Vectorworks 2027")
    assert documents.installed_bundle(apps, tested).endswith("Vectorworks 2027.app")  # the one used last
    monkeypatch.setenv("VWMCP_APP", "Vectorworks 2026")
    assert documents.installed_bundle(apps, tested).endswith("Vectorworks 2026.app")  # configured wins
    assert documents.installed_bundle([], tested) is None


def test_trigger_remembers_the_vectorworks_it_talks_to(tmp_path):
    t = trig.MacTrigger(tmp_path, apps=lambda: [("Vectorworks 2026", 100.0)], spawn=lambda a: None, min_uptime=0)
    t._reap = lambda: None
    t()
    assert trig.last_app(tmp_path) == "Vectorworks 2026"


def test_find_process_prefers_the_last_used_version(monkeypatch, tmp_path):
    monkeypatch.setenv("VWMCP_HOME", str(tmp_path))
    monkeypatch.delenv("VWMCP_APP", raising=False)
    procs = [{"name": "Vectorworks 2027", "pid": 2, "uptime": 5.0, "bundle": "b27"},
             {"name": "Vectorworks 2026", "pid": 1, "uptime": 50.0, "bundle": "b26"}]
    assert documents.find_process(lambda: procs)["pid"] == 2
    trig.remember_app(tmp_path, "Vectorworks 2026")
    assert documents.find_process(lambda: procs)["pid"] == 1


def test_tools_are_registered():
    names = {t.name for t in app.mcp._tool_manager.list_tools()}
    assert {"vw_documents", "vw_switch_document", "vw_open_document"} <= names


def test_instructions_and_profile_explain_documents():
    assert "SEVERAL DOCUMENTS" in app.INSTRUCTIONS and "vw_open_document" in app.INSTRUCTIONS
    assert profile.defaults()["documents"] == "guard"
    assert "WRONG DOCUMENT" in profile.rule_text("documents", "guard")
    assert json.dumps(profile.table())  # the launcher's copy stays serialisable
