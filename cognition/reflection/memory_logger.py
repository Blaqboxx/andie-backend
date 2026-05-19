"""
Memory Logger — persists ReflectionRecords to disk as append-only JSONL.

Each line in the log file is a self-contained JSON object representing one
ReflectionRecord.  This makes the log easy to tail, grep, and replay for
pattern detection without loading the entire history into memory.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional

from .reflection_models import ReflectionRecord

# ---------------------------------------------------------------------------
# Default storage path
# ---------------------------------------------------------------------------

_DEFAULT_LOG_DIR = Path(
    os.environ.get(
        "ANDIE_REFLECTION_LOG_DIR",
        os.path.join(os.path.dirname(__file__), "..", "..", "data", "reflection"),
    )
).resolve()

_LOG_FILENAME = "reflections.jsonl"


class MemoryLogger:
    """
    Append-only JSONL logger for ReflectionRecords.

    Thread-safe via a per-instance lock.

    Usage::

        logger = MemoryLogger()
        logger.append(record)
        recent = logger.tail(50)
    """

    def __init__(self, log_dir: Optional[Path] = None) -> None:
        self._log_dir = Path(log_dir) if log_dir else _DEFAULT_LOG_DIR
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self._log_dir / _LOG_FILENAME
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Write                                                                #
    # ------------------------------------------------------------------ #

    def append(self, record: ReflectionRecord) -> None:
        """Append a single record to the JSONL log (thread-safe)."""
        entry = record.to_log_entry()
        line  = json.dumps(entry, ensure_ascii=False, default=str)
        with self._lock:
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    # ------------------------------------------------------------------ #
    # Read                                                                 #
    # ------------------------------------------------------------------ #

    def tail(self, n: int = 100) -> List[dict]:
        """Return the last `n` log entries as raw dicts."""
        return list(self._iter_entries())[-n:]

    def all_entries(self) -> List[dict]:
        """Load the entire log into memory as a list of dicts."""
        return list(self._iter_entries())

    def filter(
        self,
        *,
        outcome: Optional[str] = None,
        agent_id: Optional[str] = None,
        since: Optional[datetime] = None,
    ) -> List[dict]:
        """
        Filtered read — streams the log and yields matching entries.

        Parameters
        ----------
        outcome:
            Match ``epistemic_status`` exactly, e.g. ``"epistemic_failure"``.
        agent_id:
            Match ``agent_id`` exactly.
        since:
            Only include entries with ``timestamp`` >= this datetime.
        """
        results = []
        for entry in self._iter_entries():
            if outcome and entry.get("epistemic_status") != outcome:
                continue
            if agent_id and entry.get("agent_id") != agent_id:
                continue
            if since:
                try:
                    ts = datetime.fromisoformat(entry["timestamp"])
                    if ts < since:
                        continue
                except (KeyError, ValueError):
                    pass
            results.append(entry)
        return results

    # ------------------------------------------------------------------ #
    # Maintenance                                                          #
    # ------------------------------------------------------------------ #

    def entry_count(self) -> int:
        if not self._log_path.exists():
            return 0
        with self._lock:
            with self._log_path.open("r", encoding="utf-8") as fh:
                return sum(1 for line in fh if line.strip())

    def rotate(self, keep_last: int = 10_000) -> int:
        """
        Trim the log to the last `keep_last` entries.

        Returns the number of entries removed.
        """
        entries = self.all_entries()
        if len(entries) <= keep_last:
            return 0
        trimmed = entries[-keep_last:]
        removed = len(entries) - len(trimmed)
        lines = [json.dumps(e, ensure_ascii=False, default=str) for e in trimmed]
        with self._lock:
            with self._log_path.open("w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        return removed

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _iter_entries(self) -> Iterator[dict]:
        if not self._log_path.exists():
            return
        with self._lock:
            with self._log_path.open("r", encoding="utf-8") as fh:
                lines = fh.readlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
