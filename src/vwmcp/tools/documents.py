"""Several open documents: vw_documents, vw_switch_document, vw_open_document.

How it works and why it goes through macOS: see vwmcp.documents.
"""

from __future__ import annotations

import time
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from vwmcp import app, documents
from vwmcp import trigger as trig
from vwmcp.app import READ, mcp
from vwmcp.client import BridgeError
from vwmcp.documents import DocumentsError

_sleep = time.sleep  # tests replace these
_clock = time.monotonic

SWITCH = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
VERIFY_CLAIM_S = 30.0  # a freshly switched/opened document may keep Vectorworks busy for a moment


def _process() -> dict:
    proc = documents.find_process()
    if proc is None:
        raise ToolError("Vectorworks is not running. vw_open_document starts it with a file.")
    return proc


def _ui(pid: int) -> dict:
    try:
        return documents.read_ui(pid)
    except DocumentsError as e:
        raise ToolError(str(e)) from e


def _active(claim_timeout: float | None = None) -> dict:
    """Name and path of the document in front, from Vectorworks itself."""
    try:
        res = app.get_bridge().run(documents.IDENT_CODE, guarded=False, claim_timeout=claim_timeout)
    except BridgeError as e:
        raise ToolError(str(e)) from e
    if not res.get("ok") or not isinstance(res.get("value"), dict):
        raise ToolError("Vectorworks didn't say which document is in front: %s" % (res.get("error") or "?"))
    return res["value"]


def _blocked_by(ui: dict) -> None:
    dlg = documents.dialog_of(ui)
    if dlg:
        raise ToolError("Vectorworks shows a dialog ('%s'); it blocks every request. Ask the user to answer it, "
                        "then try again." % dlg)


def _pin_info(doc: dict) -> dict:
    return {"name": doc.get("name"), "path": documents.posix_path(doc.get("path")) or None}


@mcp.tool(annotations=READ)
def vw_documents() -> dict:
    """Open Vectorworks documents: which one is in front (every call works
    there) and which one Keystone works in (keystone_document: while
    another one is in front, calls stop with WRONG DOCUMENT instead of
    touching the wrong file). Switch with vw_switch_document, open files
    with vw_open_document."""
    proc = _process()
    ui = _ui(proc["pid"])
    docs = documents.documents_from_ui(ui)
    out: dict = {"application": proc["name"]}
    dlg = documents.dialog_of(ui)
    active = None
    if dlg:
        out["dialog"] = dlg
        out["note"] = "Vectorworks shows a dialog; requests wait until the user answers it."
    else:
        active = _active()
        out["active"] = _pin_info(active)
        for d in docs:
            d["active"] = documents.same_name(d["name"], active.get("name"))
    pin = documents.PIN
    pinned = pin.current() if pin.enabled() else None
    for d in docs:
        d["keystone"] = bool(pinned) and documents.same_name(d["name"], pinned["name"])
        d.pop("source", None)
    out["documents"] = docs
    out["keystone_document"] = _pin_info(pinned) if pinned else None
    out["guard"] = "on" if pin.enabled() else "off (profile: always the document in front)"
    if not docs:
        out["windows"] = [{"name": w.get("name"), "subrole": w.get("subrole")} for w in ui.get("windows") or []]
        out["note"] = ("No document is open (start screen?). If Vectorworks shows documents, tell the developer: "
                       "the window list of this Vectorworks version needs adjusting (see windows).")
    return out


def _switch(proc: dict, title: str, front: bool) -> tuple[dict, str]:
    """Bring the document window `title` to the front and verify it inside
    Vectorworks; one retry with Vectorworks brought to the front."""
    tried = []
    for bring in ([front] if front else [False, True]):
        try:
            method = documents.switch_window(proc["pid"], title, bring)
        except DocumentsError as e:
            raise ToolError(str(e)) from e
        tried.append(method)
        if method == "not_found":
            raise ToolError("Vectorworks has no window '%s' any more (closed meanwhile?). Check with vw_documents."
                            % title)
        _sleep(0.3)
        active = _active(VERIFY_CLAIM_S)
        if documents.same_name(active.get("name"), title):
            return active, method + (" (Vectorworks brought to the front)" if bring else "")
    raise ToolError("Asked macOS to bring '%s' to the front (%s), but Vectorworks still works in '%s'. Ask the user "
                    "to click the document once, then call vw_switch_document again."
                    % (title, ", ".join(tried), active.get("name")))


@mcp.tool(annotations=SWITCH)
def vw_switch_document(document: str, front: bool = False) -> dict:
    """Bring another open document to the front in Vectorworks and work in it
    from now on (document: its name as in vw_documents, without extension
    is fine, or its path). Checked inside Vectorworks afterwards. If it is
    already in front, Keystone just takes it over (nothing moves) - that is
    the answer to WRONG DOCUMENT when the user switched on purpose.
    front=True also brings the Vectorworks app in front of other apps
    (default: the user's focus stays where it is)."""
    proc = _process()
    ui = _ui(proc["pid"])
    _blocked_by(ui)
    try:
        doc = documents.match_document(documents.documents_from_ui(ui), document)
    except DocumentsError as e:
        raise ToolError(str(e)) from e
    active = _active()
    if documents.same_name(active.get("name"), doc["name"]):
        documents.PIN.set(active)
        return {"document": _pin_info(active), "switched": False,
                "note": "Already in front; Keystone works in it now."}
    active, method = _switch(proc, doc["name"], front)
    documents.PIN.set(active)
    return {"document": _pin_info(active), "switched": True, "method": method}


# One call must stay well under the ~80 s a call to a linked computer may
# take; longer work (starting Vectorworks, loading a big file) goes on in
# Vectorworks and the next call picks it up.
STEP_S = 50.0
_PENDING: dict[str, dict] = {}  # file -> {"since", "before"}: handed to Vectorworks, not visible yet
PENDING_FOR = 180.0  # a file handed over that never shows up: hand it over again after this


def _wait_ready(deadline: float) -> dict | None:
    """Wait until Vectorworks runs long enough and answers macOS; None if the
    time is up first."""
    while _clock() < deadline:
        proc = documents.find_process()
        if proc is not None and proc["uptime"] >= trig.MIN_UPTIME:
            try:
                ui = documents.read_ui(proc["pid"])
            except DocumentsError:
                ui = None
            if ui is not None and ui.get("windows"):
                return proc
        _sleep(1.0)
    return None


def _find_new(docs: list[dict], file, template: bool, before: set[str]) -> str | None:
    if template:
        new = [d for d in docs if documents.norm(d["name"]) not in before]
        return new[0]["name"] if new else None
    hit = [d for d in docs if documents.same_name(d["name"], file.name)]
    return hit[0]["name"] if hit else None


@mcp.tool(annotations=SWITCH)
def vw_open_document(path: str, front: bool = False, wait_s: float = STEP_S) -> dict:
    """Open a Vectorworks file (.vwx) by its full path and work in it from
    now on; a template (.sta) opens as a new untitled document. Starts
    Vectorworks if needed (the version you work with). A file that is
    already open is only brought to the front. No dialogs from Keystone -
    but Vectorworks may ask something itself (e.g. converting a file from
    an older version): then the user must answer it. One call waits at most
    ~50 s: if the result says status "starting" or "loading", call
    vw_open_document again with the same path - it goes on from there and
    never opens the file twice. front=True brings Vectorworks in front of
    other apps."""
    try:
        file = documents.check_openable(path)
    except DocumentsError as e:
        raise ToolError(str(e)) from e
    template = file.suffix.lower() == ".sta"
    key = str(file)
    deadline = _clock() + max(5.0, min(wait_s, STEP_S))
    pending = _PENDING.get(key)
    if pending and _clock() - pending["since"] > PENDING_FOR:
        _PENDING.pop(key, None)
        pending = None

    proc = documents.find_process()
    if proc is None:
        bundle = documents.installed_bundle()
        if not bundle:
            raise ToolError("Can't find the Vectorworks app to open the file with.")
        try:
            documents.open_file(bundle, None, front)  # start it first: a file handed over while it
        except DocumentsError as e:              # starts may be dropped (start screen)
            raise ToolError(str(e)) from e
        app_name = Path(bundle).stem
        proc = _wait_ready(deadline)
        if proc is None:
            return {"status": "starting", "application": app_name,
                    "note": "Vectorworks is starting. Call vw_open_document again with the same path in about "
                            "20 s; it goes on from there."}
    elif proc["uptime"] < trig.MIN_UPTIME:
        proc = _wait_ready(deadline)
        if proc is None:
            return {"status": "starting", "note": "Vectorworks is still starting. Call vw_open_document again "
                                                  "with the same path in about 20 s."}

    ui = _ui(proc["pid"])
    _blocked_by(ui)
    docs = documents.documents_from_ui(ui)
    before = pending["before"] if pending else {documents.norm(d["name"]) for d in docs}
    found = _find_new(docs, file, template, before) if (pending or not template) else None
    if found and not pending and not template:  # was open before this call
        active = _active()
        if not documents.same_name(active.get("name"), found):
            active, _method = _switch(proc, found, front)
        if active.get("path") and not documents.same_file(active["path"], file):
            raise ToolError("Another file called '%s' is open (%s). Close or rename one of them first."
                            % (file.name, documents.posix_path(active["path"])))
        documents.PIN.set(active)
        return {"document": _pin_info(active), "opened": False, "note": "Was already open; now in front."}

    if not found and not pending:
        try:
            documents.open_file(proc.get("bundle") or documents.installed_bundle() or "", key, front)
        except DocumentsError as e:
            raise ToolError(str(e)) from e
        _PENDING[key] = pending = {"since": _clock(), "before": before}

    while not found and _clock() < deadline:
        _sleep(1.0)
        try:
            ui = documents.read_ui(proc["pid"])
        except DocumentsError:  # busy loading: ask again
            continue
        dlg = documents.dialog_of(ui)
        if dlg:
            raise ToolError("Vectorworks asks something while opening '%s': %s. Tell the user what it asks; they "
                            "answer it in Vectorworks. Then call vw_open_document again with the same path."
                            % (file.name, dlg))
        found = _find_new(documents.documents_from_ui(ui), file, template, before)
    if not found:
        return {"status": "loading", "file": key,
                "note": "Vectorworks is still loading '%s'. Call vw_open_document again with the same path in "
                        "about 20 s (it won't open the file twice), or check with vw_documents." % file.name}

    _PENDING.pop(key, None)
    active = _active(max(5.0, min(VERIFY_CLAIM_S, deadline - _clock() + 10.0)))
    if not documents.same_name(active.get("name"), found):
        active, _method = _switch(proc, found, front)
    if not template and active.get("path") and not documents.same_file(active["path"], file):
        raise ToolError("Vectorworks opened '%s' but reports the path %s. Check with vw_documents."
                        % (found, documents.posix_path(active["path"])))
    documents.PIN.set(active)
    out = {"document": _pin_info(active), "opened": True, "application": proc["name"]}
    if template:
        out["note"] = "New untitled document from the template; save it under a name when the user wants to keep it."
    return out
