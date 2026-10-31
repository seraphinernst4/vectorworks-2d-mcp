"""Ways to make Vectorworks run the "MCP Run Job" menu command.

macOS: AppleScript `run VectorScript` calling DoMenuTextByName. The menu
command gives the job a real document/undo context. Sending Python
directly (`run Python script`) is NOT used: in Vectorworks 2026 any
document write from that context, or any script sent while the app is
launching, crashes Vectorworks.

The Apple Event only returns when the menu command has finished, so a
pending osascript means "Vectorworks is busy or a dialog is open". The
trigger never refuses because of that (the job-claim timeout in client.py
decides); it only avoids stacking more than a few events.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from vwmcp.client import TriggerError

MENU_COMMAND = "MCP Run Job"
MIN_UPTIME = float(os.environ.get("VWMCP_MIN_UPTIME", "20"))
MAX_PENDING = 3
# menu:   run the "MCP Run Job" menu command (needs it in the workspace)
# direct: run the bridge through VectorScript's PythonExecute (no menu
#         command needed; the pattern Vectorworks documents for AppleScript)
# auto:   menu command; if Vectorworks answered but didn't take the job (no
#         such command), switch to direct for the rest of the session
TRIGGER_MODE = os.environ.get("VWMCP_TRIGGER", "menu")
MENU_VSS = f"PROCEDURE T; BEGIN DoMenuTextByName('{MENU_COMMAND}', 0); END; RUN(T);"
DIRECT_VSS = ("PROCEDURE T; BEGIN PythonBeginContext; PythonExecute('from vwmcp import vwmcp_bridge'); "
              "PythonExecute('vwmcp_bridge.run_pending()'); PythonEndContext; END; RUN(T);")
_APP_RE = re.compile(r"/(Vectorworks 20\d\d)\.app/Contents/MacOS/Vectorworks(?:$|\s+-)")


def parse_etime(text: str) -> int:
    """Parse ps etime ([[dd-]hh:]mm:ss) into seconds."""
    days, _, rest = text.strip().rpartition("-")
    parts = [int(p) for p in rest.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts
    return (int(days) if days else 0) * 86400 + h * 3600 + m * 60 + s


def parse_ps(text: str) -> list[tuple[str, float]]:
    """Vectorworks apps in `ps -axo pid=,etime=,command=` output as
    (app name, seconds since start), most recently started first."""
    apps = []
    for line in text.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        m = _APP_RE.search(parts[2].strip())
        if m:
            with contextlib.suppress(ValueError):
                apps.append((m.group(1), float(parse_etime(parts[1]))))
    apps.sort(key=lambda a: a[1])
    return apps


def running_apps() -> list[tuple[str, float]]:
    """Running Vectorworks versions, e.g. [("Vectorworks 2027", 812.0)].
    (ps, not pgrep: BSD pgrep hides its own ancestors, e.g. when a test is
    started from inside Vectorworks.)"""
    ps = subprocess.run(["ps", "-axo", "pid=,etime=,command="], capture_output=True, text=True)
    return parse_ps(ps.stdout)


def configured_app() -> str | None:
    """VWMCP_APP if set to a real name (not empty, not an unsubstituted ${...})."""
    app = (os.environ.get("VWMCP_APP") or "").strip()
    return app if re.fullmatch(r"Vectorworks 20\d\d", app) else None


class MacTrigger:
    def __init__(
        self,
        root: Path,
        app: str | None = None,
        min_uptime: float = MIN_UPTIME,
        apps: Callable[[], list[tuple[str, float]]] | None = None,
        spawn: Callable[[list[str]], subprocess.Popen] | None = None,
        max_pending: int = MAX_PENDING,
        mode: str | None = None,
    ):
        self.root = Path(root)
        self.mode = mode or TRIGGER_MODE
        if self.mode not in ("menu", "direct", "auto"):
            self.mode = "menu"
        self.direct = self.mode == "direct"
        self.app = app if app is not None else configured_app()  # None: whichever runs
        self.min_uptime = min_uptime
        self.apps = apps or running_apps
        self.spawn = spawn or (
            lambda args: subprocess.Popen(
                args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        )
        self.max_pending = max_pending
        self._pending: list[tuple[subprocess.Popen, float]] = []
        self.target: str | None = None  # app name of the last trigger

    def _reap(self) -> None:
        self._pending = [(p, t) for p, t in self._pending if p.poll() is None]

    def in_flight(self) -> bool:
        self._reap()
        return bool(self._pending)

    def pending_for(self) -> float:
        """Seconds the oldest unanswered trigger has been waiting (0 if none)."""
        self._reap()
        return time.monotonic() - min(t for _, t in self._pending) if self._pending else 0.0

    def choose_app(self) -> tuple[str, float]:
        running = self.apps()
        if not running:
            want = self.app or "Vectorworks"
            raise TriggerError(f"{want} is not running. Open it with a document first.")
        if self.app:  # a preference: wins when it runs, else whichever runs
            for name, up in running:
                if name == self.app:
                    return name, up
        return running[0]

    def __call__(self) -> None:
        app, up = self.choose_app()
        if up < self.min_uptime:
            raise TriggerError(f"{app} is still starting ({up:.0f}s). Try again in a few seconds.")
        self._reap()
        if len(self._pending) >= self.max_pending:
            return  # enough events queued; they will run the job when Vectorworks is free
        vss_text = DIRECT_VSS if self.direct else MENU_VSS
        script = f'tell application "{app}" to run VectorScript "{vss_text}"'
        self.target = app
        self._pending.append((self.spawn(["osascript", "-e", script]), time.monotonic()))

    def on_unclaimed(self) -> bool:
        """Vectorworks answered the trigger but didn't take the job. In auto
        mode that means the menu command is missing: switch to direct.
        True if the mode changed."""
        if self.mode == "auto" and not self.direct:
            self.direct = True
            return True
        return False


def manual_trigger() -> None:
    """No automation available: the user runs MCP Run Job themselves."""


def default_trigger(root: Path) -> Callable[[], None]:
    if sys.platform == "darwin":
        return MacTrigger(root)
    return manual_trigger
