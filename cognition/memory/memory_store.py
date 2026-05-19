"""
STEP 11A — Memory Store
=======================
JSON-backed persistent key-value store with namespaced collections.

Provides atomic read/write of namespaced record sets to a single JSON file.
Records are stored as dicts with caller-defined schemas.  The store is the
single source of truth; all higher-level memory modules build on top of it.

Thread-safety: single-threaded asyncio use only.

Usage
-----
    store = MemoryStore("/path/to/memory.json")
    store.put("episodes", "ep-001", {"task": "deploy", "outcome": "success"})
    record = store.get("episodes", "ep-001")
    results = store.query("episodes", lambda r: r["outcome"] == "failure")
    store.flush()
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Internal layout: { namespace: { key: record_dict } }
_StoreData = Dict[str, Dict[str, Dict[str, Any]]]


class MemoryStore:
    """Persistent namespaced key-value store backed by a JSON file.

    Records are plain dicts.  The store is loaded once on construction and
    flushed to disk on ``flush()`` or any mutating call when
    ``auto_flush=True``.
    """

    def __init__(self, path: str | Path, auto_flush: bool = True) -> None:
        self._path      = Path(path)
        self._auto_flush = auto_flush
        self._data: _StoreData = {}
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                logger.debug("MemoryStore loaded %d namespaces from %s",
                             len(self._data), self._path)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("MemoryStore could not load '%s': %s — starting empty", self._path, exc)
                self._data = {}
        else:
            self._data = {}

    def flush(self) -> None:
        """Write current state to disk atomically (write-then-rename)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, default=str)
            os.replace(tmp, self._path)
        except OSError as exc:
            logger.error("MemoryStore flush failed: %s", exc)

    def _maybe_flush(self) -> None:
        if self._auto_flush:
            self.flush()

    # ── Namespace helpers ─────────────────────────────────────────────────────

    def _ns(self, namespace: str) -> Dict[str, Dict[str, Any]]:
        if namespace not in self._data:
            self._data[namespace] = {}
        return self._data[namespace]

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def put(self, namespace: str, key: str, value: Dict[str, Any]) -> None:
        self._ns(namespace)[key] = value
        self._maybe_flush()

    def get(self, namespace: str, key: str) -> Optional[Dict[str, Any]]:
        return self._ns(namespace).get(key)

    def delete(self, namespace: str, key: str) -> bool:
        ns = self._ns(namespace)
        existed = key in ns
        ns.pop(key, None)
        if existed:
            self._maybe_flush()
        return existed

    def exists(self, namespace: str, key: str) -> bool:
        return key in self._data.get(namespace, {})

    # ── Bulk ──────────────────────────────────────────────────────────────────

    def all(self, namespace: str) -> List[Dict[str, Any]]:
        """Return all records in a namespace as a list."""
        return list(self._ns(namespace).values())

    def keys(self, namespace: str) -> List[str]:
        return list(self._ns(namespace).keys())

    def query(
        self,
        namespace: str,
        filter_fn: Callable[[Dict[str, Any]], bool],
    ) -> List[Dict[str, Any]]:
        """Return all records in namespace where filter_fn returns True."""
        return [r for r in self._ns(namespace).values() if filter_fn(r)]

    def count(self, namespace: str) -> int:
        return len(self._data.get(namespace, {}))

    def namespaces(self) -> List[str]:
        return list(self._data.keys())

    def clear_namespace(self, namespace: str) -> None:
        self._data.pop(namespace, None)
        self._maybe_flush()

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, int]:
        return {ns: len(records) for ns, records in self._data.items()}
