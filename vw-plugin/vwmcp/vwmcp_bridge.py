"""vwmcp bridge: the in-Vectorworks half of Keystone for Vectorworks.

A small executor. Each call to run_pending() (the "MCP Run Job" menu
command) claims the oldest job in <root>/jobs/, runs its Python with `vs`
in scope and writes <root>/results/<id>.json. All real logic lives in the
MCP server.

Job protocol (every step an atomic rename, so nothing runs twice or half):
  server:  jobs/<id>.tmp -> jobs/<id>.json
  bridge:  jobs/<id>.json -> jobs/<id>.working   (claim: only one rename wins)
  bridge:  results/<id>.json.tmp -> results/<id>.json, then delete .working
  server:  not claimed in time -> jobs/<id>.json -> jobs/<id>.cancelled
           (if that rename fails, the bridge has just claimed it: wait)

`vs` is replaced per job by a checked proxy: before a vs.* call, the
arguments are checked against vs_index.json (next to this file). A wrong
count or an obvious type error raises a Python exception with the right
signature instead of reaching Vectorworks, which would open a modal
"Script Errors" dialog. Functions not in the index pass straight through.

Vectorworks keeps Python state between menu runs, so the index and compiled
helper libraries are cached here, and an updated copy of this file is
reloaded automatically.

Keep this file stdlib-only and compatible with Python 3.9 (Vectorworks 2026)
and 3.14 (Vectorworks 2027).
"""

import ast
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import time
import traceback
import types

BRIDGE_VERSION = "0.2.0"
DEFAULT_ROOT = os.path.join(os.path.expanduser("~"), ".vwmcp")
INDEX_NAME = "vs_index.json"

try:
    _HERE = os.path.dirname(os.path.abspath(__file__))
    _LOADED_MTIME = os.path.getmtime(os.path.abspath(__file__))
except (NameError, OSError):  # pragma: no cover - exec'd without a file
    _HERE = None
    _LOADED_MTIME = None

# Survives importlib.reload(): keep caches already built in this session.
_CACHE = globals().get("_CACHE") or {"index": None, "index_key": None, "libs": {}}


# ---------------------------------------------------------------- results


def _write_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, default=repr)
    os.replace(tmp, path)


def _jsonable(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


# ---------------------------------------------------------- checked proxy


class VSArgumentError(TypeError):
    """A vs.* call whose arguments don't match the function's signature.
    Raised before Vectorworks sees the call (it would show a modal dialog)."""


_NUMBER = (int, float)
_TUPLE_LEN = {"P": (2, 3), "Q": (2, 3), "V": (2, 3), "C": (3,)}
_WIDTH = {"P": 2, "Q": 3, "V": 3}
_TYPE_NAME = {"H": "HANDLE", "R": "REAL", "I": "INTEGER", "B": "BOOLEAN", "S": "STRING",
              "P": "POINT", "Q": "POINT3D", "V": "VECTOR", "C": "COLOR", "F": "callback"}
_DEFINITION = re.compile(r"Definition:.*?vs\.\w+\s*\(([^)]*)\)")


def _live_param_count(fn):
    """Parameter count from Vectorworks' own docstring, None if it has none."""
    m = _DEFINITION.search(getattr(fn, "__doc__", None) or "")
    if not m:
        return None
    inner = m.group(1).strip()
    return len([p for p in inner.split(",") if p.strip()]) if inner else 0


def _has_var_args(fn):
    code = getattr(fn, "__code__", None)
    return bool(code is not None and code.co_flags & 0x04)


def check_args(codes, args):
    """None if args fit the type codes, else a short description of the problem.
    Conservative: whatever Vectorworks might accept passes (a tuple of numbers
    may also stand for consecutive number parameters, e.g. (x, y), r, ...)."""
    i, n, k = 0, len(args), 0
    while k < len(codes):
        c = codes[k]
        pos = k + 1
        if c in _TUPLE_LEN:
            if i < n and isinstance(args[i], (tuple, list)):
                if len(args[i]) not in _TUPLE_LEN[c]:
                    return "parameter %d (%s) needs %s values, got a tuple of %d" % (
                        pos, _TYPE_NAME[c], " or ".join(str(x) for x in _TUPLE_LEN[c]), len(args[i]))
                i += 1
                k += 1
                continue
            if c == "C":
                return ("parameter %d (COLOR) must be one tuple (r, g, b); loose numbers are "
                        "ignored silently by Vectorworks" % pos)
            w = _WIDTH[c]
            loose = args[i:i + w]
            if len(loose) < w or not all(isinstance(a, _NUMBER) for a in loose):
                return "parameter %d (%s) needs a tuple or %d numbers" % (pos, _TYPE_NAME[c], w)
            i += w
            k += 1
            continue
        if i >= n:
            return "too few arguments (%d given)" % n
        a = args[i]
        if c in "RIB" and isinstance(a, str):
            return "parameter %d (%s) got text %r" % (pos, _TYPE_NAME[c], a[:40])
        if c in "RI" and isinstance(a, (tuple, list)) and len(a) > 1:
            span = codes[k:k + len(a)]
            if len(span) == len(a) and all(x in "RIB" for x in span):
                i += 1  # one tuple covering consecutive number parameters
                k += len(a)
                continue
            return "parameter %d (%s) got a tuple of %d" % (pos, _TYPE_NAME[c], len(a))
        if c == "H" and isinstance(a, str):
            return ("parameter %d (HANDLE) got text %r; for a UUID use "
                    "vs.GetObjectByUuid(uuid)" % (pos, a[:40]))
        if c == "F":
            if not callable(a):
                return "parameter %d (callback) is not callable" % pos
            if _has_var_args(a):
                return ("parameter %d (callback) uses *args; Vectorworks counts named parameters, "
                        "declare them explicitly (e.g. def f(h))" % pos)
        i += 1
        k += 1
    if i != n:
        return "too many arguments (%d given)" % n
    return None


def _make_checked(name, fn, codes, params):
    def checked(*args, **kwargs):
        if not kwargs:
            problem = check_args(codes, args)
            if problem is not None:
                raise VSArgumentError(
                    "vs.%s not called: %s. Signature: vs.%s(%s)%s" % (
                        name, problem, name, params,
                        "  [POINT = (x, y) or x, y]" if "P" in codes else ""))
        return fn(*args, **kwargs)

    checked.__name__ = name
    checked.__qualname__ = name
    checked.__doc__ = getattr(fn, "__doc__", None)
    checked.__wrapped__ = fn
    return checked


class CheckedVS(types.ModuleType):
    """Stands in for the `vs` module during a job."""

    def __init__(self, real, table):
        types.ModuleType.__init__(self, "vs", getattr(real, "__doc__", None))
        object.__setattr__(self, "_ks_real", real)
        object.__setattr__(self, "_ks_table", table)

    def __getattr__(self, name):
        real = object.__getattribute__(self, "_ks_real")
        value = getattr(real, name)  # AttributeError as usual
        spec = object.__getattribute__(self, "_ks_table").get(name)
        if spec is not None and callable(value):
            value = _make_checked(name, value, spec[0], spec[1])
        object.__setattr__(self, name, value)  # cache
        return value

    def __dir__(self):
        return dir(object.__getattribute__(self, "_ks_real"))


def load_check_table(vs_module, path=None):
    """{name: (codes, params)} for the functions of this Vectorworks that can be
    checked. Entries whose parameter count disagrees with Vectorworks' own
    docstring are dropped (the live API wins). Cached per file and module."""
    path = path or (os.path.join(_HERE, INDEX_NAME) if _HERE else None)
    if not path or not os.path.isfile(path):
        return {}
    key = (path, os.path.getmtime(path), id(vs_module))
    if _CACHE["index_key"] == key and _CACHE["index"] is not None:
        return _CACHE["index"]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    table = {}
    for name, entry in data.get("f", {}).items():
        codes = entry.get("t")
        if codes is None:  # "" = no parameters, still checked
            continue
        fn = getattr(vs_module, name, None)
        if fn is None or not callable(fn):
            continue
        live = _live_param_count(fn)
        if live is not None and live != len(codes):
            continue
        table[name] = (codes, entry.get("a", ""))
    _CACHE["index"], _CACHE["index_key"] = table, key
    return table


# ----------------------------------------------------------------- running


def _compiled_lib(lib):
    """Compiled code of a helper library ({"sha", "path"} or {"sha", "src"}),
    cached by its sha256 for the whole Vectorworks session."""
    sha = lib.get("sha")
    code = _CACHE["libs"].get(sha) if sha else None
    if code is not None:
        return code
    src = lib.get("src")
    if src is None:
        with open(lib["path"], encoding="utf-8") as f:
            src = f.read()
    if sha and hashlib.sha256(src.encode("utf-8")).hexdigest() != sha:
        raise RuntimeError("helper library %s does not match its checksum" % lib.get("path"))
    code = compile(src, "<%s>" % lib.get("name", "lib"), "exec")
    if sha:
        if len(_CACHE["libs"]) > 16:
            _CACHE["libs"].clear()
        _CACHE["libs"][sha] = code
    return code


def _execute(code, vs_module, libs=()):
    """Run code; return the last expression's value, or `result` if assigned."""
    scope = {"vs": vs_module, "__name__": "__vwmcp__"}
    for lib in libs:
        exec(_compiled_lib(lib), scope)
    tree = ast.parse(code, "<vwmcp job>", "exec")
    last_expr = None
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last_expr = ast.Expression(tree.body.pop().value)
    exec(compile(tree, "<vwmcp job>", "exec"), scope)
    value = None
    if last_expr is not None:
        value = eval(compile(last_expr, "<vwmcp job>", "eval"), scope)
    if "result" in scope:
        value = scope["result"]
    return value


def _claim_oldest(jobs_dir):
    """Claim the oldest job by renaming it to .working; returns the new path."""
    try:
        names = [n for n in os.listdir(jobs_dir) if n.endswith(".json")]
    except FileNotFoundError:
        return None
    names.sort()  # job ids are time-prefixed by the server
    for name in names:
        src = os.path.join(jobs_dir, name)
        dst = src[: -len(".json")] + ".working"
        try:
            os.replace(src, dst)  # loses against the server's cancel rename
            return dst
        except FileNotFoundError:
            continue
    return None


def _reloaded():
    """The freshly loaded module if this file changed since it was imported."""
    if _LOADED_MTIME is None:
        return None
    try:
        if os.path.getmtime(os.path.abspath(__file__)) <= _LOADED_MTIME:
            return None
        mod = sys.modules.get(__name__)
        if mod is None or getattr(mod, "run_pending", None) is not run_pending:
            return None
        import importlib.util

        # re-run the new file in this module's namespace (caches survive)
        spec = importlib.util.spec_from_file_location(__name__, os.path.abspath(__file__))
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # never let an update stop the bridge
        return None


def run_pending(root=DEFAULT_ROOT, vs_module=None, max_jobs=1):
    """Run up to max_jobs queued jobs (one per menu run = one undo step).
    Returns the number run."""
    fresh = _reloaded()
    if fresh is not None:
        return fresh.run_pending(root, vs_module, max_jobs)
    if vs_module is None:
        import vs as vs_module  # only importable inside Vectorworks
    root = str(root)
    jobs_dir = os.path.join(root, "jobs")
    results_dir = os.path.join(root, "results")
    os.makedirs(results_dir, exist_ok=True)

    ran = 0
    while ran < max_jobs:
        claimed = _claim_oldest(jobs_dir)
        if claimed is None:
            break
        ran += 1
        _run_one(claimed, results_dir, vs_module)
    return ran


def _run_one(claimed, results_dir, vs_module):
    job_id = os.path.basename(claimed)[: -len(".working")]
    out = io.StringIO()
    started = time.time()
    res = {"id": job_id, "ok": False, "value": None, "stdout": "", "error": None,
           "traceback": None, "bridge_version": BRIDGE_VERSION, "claimed_at": started}
    proxy, saved_vs = None, None
    try:
        with open(claimed, encoding="utf-8") as f:
            job = json.load(f)
        ttl = job.get("ttl")
        if ttl is not None and started - job.get("created", started) > ttl:
            res["error"] = "job expired before Vectorworks picked it up"
        else:
            run_vs = vs_module
            if job.get("check", True):
                try:
                    table = load_check_table(vs_module)
                    if table:
                        run_vs = proxy = CheckedVS(vs_module, table)
                except Exception as e:  # checking is a safety net, never a blocker
                    res["warning"] = "argument check unavailable: %s: %s" % (type(e).__name__, e)
            if proxy is not None:
                saved_vs = sys.modules.get("vs")
                sys.modules["vs"] = proxy  # `import vs` inside the job gets the proxy too
            with contextlib.redirect_stdout(out):
                value = _execute(job["code"], run_vs, job.get("libs") or ())
            res["ok"] = True
            res["value"] = _jsonable(value)
    except BaseException as e:  # noqa: BLE001 - report everything, never crash VW
        res["error"] = "%s: %s" % (type(e).__name__, e)
        res["traceback"] = traceback.format_exc()
    finally:
        if proxy is not None:
            if saved_vs is None:
                sys.modules.pop("vs", None)
            else:
                sys.modules["vs"] = saved_vs
    res["stdout"] = out.getvalue()
    res["duration"] = round(time.time() - started, 4)
    _write_json_atomic(os.path.join(results_dir, job_id + ".json"), res)
    with contextlib.suppress(FileNotFoundError):
        os.remove(claimed)
