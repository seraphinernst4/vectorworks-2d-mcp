"""Per-call revert journal.

Vectorworks merges back-to-back script-triggered runs of the same menu
command into one undo step, so Cmd-Z can't reliably undo exactly one tool
call. Typed write tools record how to reverse themselves here, and
vw_revert_last replays that. Stored as JSON lines so it survives restarts.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

RAW_PYTHON = "vw_exec_python"


class JournalError(RuntimeError):
    pass


class Journal:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def _save(self, entries: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
        tmp.replace(self.path)

    def record(self, document: str, tool: str, ops: list[dict] | None) -> None:
        """ops=None marks an unrevertable write (raw Python)."""
        entries = self._load()
        entries.append({"time": time.time(), "document": document, "tool": tool, "ops": ops})
        self._save(entries[-200:])

    def _last_index(self, entries: list[dict], document: str) -> int:
        for i in range(len(entries) - 1, -1, -1):
            if entries[i]["document"] == document:
                return i
        raise JournalError(f"nothing to revert in {document!r} (only typed write tools are journaled)")

    def remap(self, document: str, mapping: dict[str, str]) -> None:
        """After a revert recreated objects under new UUIDs, point older
        entries at the new objects."""
        if not mapping:
            return

        def sub(x):
            if isinstance(x, str):
                return mapping.get(x, x)
            if isinstance(x, list):
                return [sub(v) for v in x]
            if isinstance(x, dict):
                return {k: sub(v) for k, v in x.items()}
            return x

        entries = self._load()
        for e in entries:
            if e["document"] == document and e["ops"]:
                e["ops"] = sub(e["ops"])
        self._save(entries)

    def peek(self, document: str) -> dict:
        entries = self._load()
        return entries[self._last_index(entries, document)]

    def pop(self, document: str) -> dict:
        entries = self._load()
        i = self._last_index(entries, document)
        entry = entries.pop(i)
        self._save(entries)
        if entry["ops"] is None:
            raise JournalError(
                f"the last write in {document!r} was {entry['tool']}, which can't be "
                "reverted automatically; use Cmd-Z in Vectorworks. (Call again to "
                "revert the write before it.)"
            )
        return entry
