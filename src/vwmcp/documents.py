"""Several open documents: list, switch, open - and the document pin.

Vectorworks' scripting API can't list, switch or open documents (no vs.*
function does it, and its AppleScript dictionary has no usable document
list), and every script runs in the document that is in front. So:

- list and switch go through macOS UI scripting (System Events, JXA): the
  document windows of the Vectorworks process and its Window menu
  ("Fenster"), exactly what the user would click;
- open goes through Launch Services (`open -a <Vectorworks.app> file`);
- every switch/open is verified inside Vectorworks (GetFName/GetFPathName).

All of it runs in the server process, never inside Vectorworks: a script
there that waits for Vectorworks' own UI would block it.

The pin: Keystone remembers the document it works in. Every guarded job
first checks inside Vectorworks that this document is the active one and
refuses otherwise (WrongDocument, nothing ran), so a click on another
document while Claude works never sends edits or reads into the wrong
file. The pin is set by vw_switch_document / vw_open_document, or by the
first call after it lapsed (PIN_IDLE seconds without a call), and every
guarded call keeps it alive. Profile setting "documents" = "follow" turns
the guard off (always work in the document in front).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path

from vwmcp import trigger as trig
from vwmcp.client import WrongDocument

PIN_IDLE = float(os.environ.get("VWMCP_PIN_IDLE", "900"))  # 15 minutes without a call: start fresh
OSA_TIMEOUT = 12.0
DOC_EXTS = (".vwx", ".sta")
UNTITLED = ("ohne titel", "untitled", "sans titre", "senza titolo", "sin título")

IDENT_CODE = """
_n = vs.GetFName()
_p = vs.GetFPathName()
result = {"name": "" if _n is None else str(_n), "path": "" if _p is None else str(_p)}
"""

# Runs first in every guarded job (as a helper library, before vwlib):
# compares Vectorworks' own answers now with its answers when the pin was
# taken, so both sides use the same path format.
GUARD_TEMPLATE = '''# Keystone document guard (generated): refuse the job in another document
class WrongDocument(Exception):
    pass


def _ks_guard(want):
    import json
    import unicodedata

    def n(s):
        return unicodedata.normalize("NFC", s or "").strip().casefold()

    name = vs.GetFName()
    path = vs.GetFPathName()
    name = "" if name is None else str(name)
    path = "" if path is None else str(path)
    if want["path"] and n(want["path"]) != n(want["name"]) and path and n(path) != n(name):
        ok = n(path) == n(want["path"])
    else:
        ok = n(name) == n(want["name"])
    if not ok:
        raise WrongDocument(json.dumps({"active": {"name": name, "path": path}, "expected": want}))


_ks_guard(%r)
del _ks_guard
'''


# ------------------------------------------------------------------ names


def norm(s: str | None) -> str:
    return unicodedata.normalize("NFC", s or "").strip().casefold()


def stem(s: str | None) -> str:
    n = norm(s)
    for ext in DOC_EXTS:
        if n.endswith(ext):
            return n[: -len(ext)]
    return n


def same_name(a: str | None, b: str | None) -> bool:
    """Window titles may hide the extension (Finder setting)."""
    return bool(norm(a)) and (norm(a) == norm(b) or stem(a) == stem(b))


def looks_like_document(title: str | None) -> bool:
    n = norm(title)
    return n.endswith(DOC_EXTS) or n.startswith(UNTITLED)


def posix_path(vw_path: str | None) -> str:
    """GetFPathName as a POSIX path. On the Mac it may be an HFS path
    ("Macintosh HD:Users:...:Plan.vwx"); the volume name is dropped then."""
    p = vw_path or ""
    if not p or p.startswith("/") or ":" not in p:
        return p
    _vol, _, rest = p.partition(":")
    return "/" + "/".join(x.replace("/", ":") for x in rest.split(":") if x)


def same_file(vw_path: str | None, path: str | os.PathLike) -> bool:
    """Is Vectorworks' path of a document this file?"""
    a = norm(posix_path(vw_path))
    b = norm(os.path.abspath(os.path.expanduser(str(path))))
    if not a or not b or "/" not in a:
        return False
    # an HFS path lost its volume: then it is the end of the POSIX path
    # (startup disk: equal; other disks: /Volumes/<name>/...)
    return a == b or ((vw_path or "")[:1] != "/" and b.endswith(a))


def label(doc: dict | None) -> str:
    if not doc:
        return "?"
    return doc.get("name") or doc.get("path") or "?"


# -------------------------------------------------------------------- pin


def guard_source(doc: dict) -> str:
    return GUARD_TEMPLATE % ({"name": doc.get("name") or "", "path": doc.get("path") or ""},)


def wrong_document_message(active: dict, expected: dict) -> str:
    a, e = label(active), label(expected)
    return (
        f"WRONG DOCUMENT - nothing ran. Vectorworks shows '{a}' in front, but Keystone works in "
        f"'{e}' (the user brought another document to the front, or the file got a new name).\n"
        f"- If the user's latest message is about '{a}' (they switched on purpose): take it over with "
        f"vw_switch_document(document='{a}') - nothing moves in Vectorworks - and repeat the call.\n"
        f"- Otherwise don't work in '{a}': bring '{e}' back with vw_switch_document(document='{e}'), "
        "repeat the call and tell the user you switched back (if they may be working in "
        f"'{a}' right now, ask first)."
    )


class DocumentPin:
    """The document Keystone works in (see the module docstring). Used as
    `Bridge.guard`: prepare() before a guarded job, finish() after it."""

    def __init__(self, idle: float = PIN_IDLE, clock: Callable[[], float] = time.monotonic,
                 enabled: bool | None = None):
        self.idle = idle
        self.clock = clock
        self._enabled = enabled  # None: from the profile / VWMCP_DOC_GUARD
        self.doc: dict | None = None
        self.last = 0.0
        self._lock = threading.Lock()

    def enabled(self) -> bool:
        if self._enabled is not None:
            return self._enabled
        if os.environ.get("VWMCP_DOC_GUARD", "1").strip() == "0":
            return False
        try:
            from vwmcp import profile

            return profile.load().get("documents", "guard") != "follow"
        except Exception:  # noqa: BLE001 - a broken profile keeps the safe default
            return True

    def current(self) -> dict | None:
        with self._lock:
            if self.doc is not None and self.clock() - self.last > self.idle:
                self.doc = None
            return dict(self.doc) if self.doc else None

    def set(self, doc: dict | None) -> None:
        with self._lock:
            if not doc or not (doc.get("name") or doc.get("path")):
                self.doc = None
                return
            self.doc = {"name": doc.get("name") or "", "path": doc.get("path") or ""}
            self.last = self.clock()

    def clear(self) -> None:
        with self._lock:
            self.doc = None

    def prepare(self, bridge) -> str | None:
        if not self.enabled():
            return None
        doc = self.current()
        if doc is None:  # first call (after a pause): Keystone works where the user is now
            res = bridge.run(IDENT_CODE, guarded=False)
            value = res.get("value") if res.get("ok") else None
            if not isinstance(value, dict) or not value.get("name"):
                return None
            self.set(value)
            doc = self.current()
        return guard_source(doc) if doc else None

    def finish(self, res: dict) -> None:
        err = res.get("error") or ""
        if err.startswith("WrongDocument:"):
            try:
                info = json.loads(err.split(":", 1)[1])
            except ValueError:
                info = {}
            active, expected = info.get("active") or {}, info.get("expected") or self.current() or {}
            raise WrongDocument(wrong_document_message(active, expected), active, expected)
        with self._lock:
            if self.doc is not None:
                self.last = self.clock()


PIN = DocumentPin()


# ------------------------------------------------------ macOS UI scripting


class DocumentsError(RuntimeError):
    """Listing, switching or opening documents failed (message for Claude)."""


LIST_JS = r"""
function run(argv) {
  var pid = parseInt(argv[0], 10);
  var se = Application("System Events");
  var ps = se.applicationProcesses.whose({unixId: pid})();
  if (!ps.length) return JSON.stringify({error: "no_process"});
  var p = ps[0];
  function attr(el, n) { try { var v = el.attributes.byName(n).value(); return v === undefined ? null : v; } catch (e) { return null; } }
  function str(v) { return (v === null || v === undefined) ? null : String(v); }
  var out = {frontmost: null, windows: [], menu: null};
  try { out.frontmost = p.frontmost(); } catch (e) {}
  var ws = p.windows();
  for (var i = 0; i < ws.length; i++) {
    var w = ws[i], name = null;
    try { name = w.name(); } catch (e) {}
    out.windows.push({name: str(name), subrole: str(attr(w, "AXSubrole")), document: str(attr(w, "AXDocument")),
                      main: attr(w, "AXMain") === true, minimized: attr(w, "AXMinimized") === true,
                      modal: attr(w, "AXModal") === true});
  }
  try {
    var bar = p.menuBars[0].menuBarItems();
    for (var j = 0; j < bar.length; j++) {
      var t = null; try { t = bar[j].name(); } catch (e) {}
      if (t === "Fenster" || t === "Window") {
        out.menu = [];
        var items = bar[j].menus[0].menuItems();
        for (var k = 0; k < items.length; k++) {
          var mn = null; try { mn = items[k].name(); } catch (e) {}
          out.menu.push({name: str(mn), mark: str(attr(items[k], "AXMenuItemMarkChar"))});
        }
        break;
      }
    }
  } catch (e) { out.menu_error = String(e); }
  return JSON.stringify(out);
}
"""

SWITCH_JS = r"""
function run(argv) {
  var pid = parseInt(argv[0], 10), title = argv[1], front = argv[2] === "1";
  var se = Application("System Events");
  var ps = se.applicationProcesses.whose({unixId: pid})();
  if (!ps.length) return "no_process";
  var p = ps[0];
  if (front) { try { p.frontmost = true; } catch (e) {} }
  try {
    var bar = p.menuBars[0].menuBarItems();
    for (var j = 0; j < bar.length; j++) {
      var t = null; try { t = bar[j].name(); } catch (e) {}
      if (t === "Fenster" || t === "Window") {
        var items = bar[j].menus[0].menuItems();
        for (var k = items.length - 1; k >= 0; k--) {
          var mn = null; try { mn = items[k].name(); } catch (e) {}
          if (mn === title) { items[k].actions.byName("AXPress").perform(); return "menu"; }
        }
        break;
      }
    }
  } catch (e) {}
  var ws = p.windows();
  for (var i = 0; i < ws.length; i++) {
    var n = null; try { n = ws[i].name(); } catch (e) {}
    if (n === title) {
      try { ws[i].actions.byName("AXRaise").perform(); } catch (e) {}
      try { ws[i].attributes.byName("AXMain").value = true; } catch (e) {}
      return "raise";
    }
  }
  return "not_found";
}
"""


def explain_osa_error(stderr: str) -> str:
    s = stderr or ""
    low = s.lower()
    if "-1743" in s or "not authorized to send apple events" in low or "nicht berechtigt" in low:
        return ("macOS doesn't let Claude control 'System Events' yet (needed to see and switch Vectorworks "
                "windows). Ask the user: System Settings > Privacy & Security > Automation "
                "(Systemeinstellungen > Datenschutz & Sicherheit > Automation) > Claude: turn on "
                "'System Events', then try again.")
    if "-1719" in s or "-25211" in s or "assistive" in low or "bedienungshilfen" in low or "hilfszugriff" in low:
        return ("macOS doesn't give Claude Accessibility access yet (needed to see and switch Vectorworks "
                "windows). Ask the user: System Settings > Privacy & Security > Accessibility "
                "(Systemeinstellungen > Datenschutz & Sicherheit > Bedienungshilfen): turn on Claude, then "
                "try again (macOS may want Claude restarted).")
    return "macOS UI scripting failed: " + (s.strip() or "no message")


def run_js(script: str, *args: str, timeout: float = OSA_TIMEOUT,
           runner: Callable[..., subprocess.CompletedProcess] | None = None) -> str:
    if sys.platform != "darwin" and runner is None:
        raise DocumentsError("Listing, switching and opening documents works on macOS only (for now).")
    run = runner or subprocess.run
    try:
        r = run(["osascript", "-l", "JavaScript", "-e", script, *args], capture_output=True, text=True,
                timeout=timeout)
    except subprocess.TimeoutExpired:
        raise DocumentsError("Vectorworks didn't answer macOS in time (it is busy, loading a file, or a dialog "
                             "is open). Try again in a moment.") from None
    if r.returncode != 0:
        raise DocumentsError(explain_osa_error(r.stderr))
    return (r.stdout or "").strip()


def find_process(processes: Callable[[], list[dict]] | None = None) -> dict | None:
    """The Vectorworks Keystone talks to (VWMCP_APP preferred), or None."""
    procs = (processes or trig.running_processes)()
    if not procs:
        return None
    want = trig.configured_app()
    for p in procs:
        if want and p["name"] == want:
            return p
    return procs[0]


def read_ui(pid: int, runner=None) -> dict:
    out = run_js(LIST_JS, str(pid), runner=runner)
    try:
        ui = json.loads(out)
    except ValueError:
        raise DocumentsError("unexpected answer from macOS: %r" % out[:200]) from None
    if ui.get("error") == "no_process":
        raise DocumentsError("Vectorworks is no longer running.")
    return ui


def dialog_of(ui: dict) -> str | None:
    """Title of a dialog Vectorworks shows (it blocks every request), or None."""
    for w in ui.get("windows") or []:
        if w.get("subrole") in ("AXDialog", "AXSystemDialog") or w.get("modal"):
            return w.get("name") or "(untitled dialog)"
    return None


def documents_from_ui(ui: dict) -> list[dict]:
    """Open documents from a read_ui() snapshot: [{"name", "active", "source"}].

    Cocoa apps list their windows at the end of the Window menu, after the
    last separator - all documents, also ones in background tabs. When that
    tail looks like documents it is the list; otherwise the standard
    windows are."""
    std = [w for w in ui.get("windows") or []
           if w.get("name") and w.get("subrole") in (None, "AXStandardWindow") and not w.get("modal")]
    std_names = {norm(w["name"]) for w in std}
    tail: list[dict] = []
    for item in reversed(ui.get("menu") or []):
        if not item.get("name"):
            break  # separator
        tail.append(item)
    tail.reverse()
    docs: list[dict] = []
    if tail and any(norm(i["name"]) in std_names or looks_like_document(i["name"]) for i in tail):
        for i in tail:
            docs.append({"name": i["name"], "active": (i.get("mark") or "").strip() not in ("", "missing value"),
                         "source": "menu"})
        seen = {norm(d["name"]) for d in docs}
        docs += [{"name": w["name"], "active": bool(w.get("main")), "source": "window"}
                 for w in std if norm(w["name"]) not in seen]
    else:
        docs = [{"name": w["name"], "active": bool(w.get("main")), "source": "window"} for w in std]
    return docs


def match_document(docs: list[dict], wanted: str) -> dict:
    """The open document the user means: exact name, name without
    extension, a path's file name, or a unique part of a name."""
    w = wanted.strip()
    if "/" in w or ":" in w:
        w = os.path.basename(w.rstrip("/")) if "/" in w else w.rsplit(":", 1)[-1]
    for test in (lambda d: norm(d["name"]) == norm(w), lambda d: same_name(d["name"], w)):
        hits = [d for d in docs if test(d)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise DocumentsError("several open documents are called %r; save one under another name" % w)
    hits = [d for d in docs if norm(w) and norm(w) in norm(d["name"])]
    if len(hits) == 1:
        return hits[0]
    names = ", ".join(repr(d["name"]) for d in docs) or "none"
    if hits:
        raise DocumentsError("%r fits several open documents: %s - say which one" % (wanted, names))
    raise DocumentsError("no open document matches %r (open: %s). To open a file use vw_open_document "
                         "with its path." % (wanted, names))


def switch_window(pid: int, title: str, front: bool, runner=None) -> str:
    out = run_js(SWITCH_JS, str(pid), title, "1" if front else "0", runner=runner)
    if out == "no_process":
        raise DocumentsError("Vectorworks is no longer running.")
    return out


def open_file(bundle: str, path: str, front: bool,
              runner: Callable[..., subprocess.CompletedProcess] | None = None) -> None:
    """Hand the file to Vectorworks through Launch Services (no dialog)."""
    if sys.platform != "darwin" and runner is None:
        raise DocumentsError("Opening documents works on macOS only (for now).")
    args = ["open"] + ([] if front else ["-g"]) + ["-a", bundle, path]
    try:
        r = (runner or subprocess.run)(args, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise DocumentsError("macOS didn't hand the file to Vectorworks within 30 s.") from None
    if r.returncode != 0:
        raise DocumentsError("macOS couldn't open the file in Vectorworks: " + (r.stderr or "").strip())


def installed_bundle() -> str | None:
    """Newest installed Vectorworks app (VWMCP_APP preferred), for opening a
    file while Vectorworks isn't running."""
    want = trig.configured_app()
    found = []
    for base in (Path("/Applications"), Path.home() / "Applications"):
        if not base.is_dir():
            continue
        found.extend(base.glob("Vectorworks 20*/Vectorworks 20*.app"))
    if want:
        for app in found:
            if app.stem == want:
                return str(app)
    return str(max(found, key=lambda a: a.stem)) if found else None


def check_openable(path: str) -> Path:
    p = Path(os.path.expanduser(path.strip()))
    if not p.is_absolute():
        raise DocumentsError("give the full path of the file (e.g. /Users/<name>/Documents/Plan.vwx)")
    if not p.is_file():
        raise DocumentsError("there is no file at %s" % p)
    if p.suffix.lower() not in DOC_EXTS:
        hint = (" DWG/DXF: import them into the open document with vw2d_import (no dialog)."
                if p.suffix.lower() in (".dwg", ".dxf") else "")
        raise DocumentsError("only Vectorworks files (.vwx) and templates (.sta) open this way." + hint)
    return p
