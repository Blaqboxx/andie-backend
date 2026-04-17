from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from autonomy.observability_alerts import emit_observability_alert


ALLOW_BACKFILL = os.environ.get("ANDIE_ALLOW_BACKFILL", "false").strip().lower() in {"1", "true", "yes", "on"}


class MemoryStore:
    def __init__(self, path: str | None = None) -> None:
        default_path = Path(__file__).resolve().parent.parent / "storage" / "learning" / "skill_memory.json"
        configured = path or os.environ.get("ANDIE_SKILL_MEMORY_PATH")
        self.path = Path(configured) if configured else default_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.data = self._load()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save(self) -> None:
        with self._lock:
            try:
                self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            except Exception as exc:
                emit_observability_alert(
                    "memory_write_error",
                    "Failed to persist learning memory",
                    severity="critical",
                    metadata={
                        "path": str(self.path),
                        "error": str(exc),
                    },
                )
                raise

    def _memory_key(self, skill_name: str, context_key: str | None = None) -> str:
        key = str(skill_name or "").strip()
        context = str(context_key or "").strip()
        return f"{key}::{context}" if context else key

    def _canonicalize_context_key(self, context_key: str | None) -> str | None:
        if not context_key:
            return None
        normalized = re.sub(r"[^a-z0-9:_|\-]+", "", str(context_key).strip().lower())
        normalized = normalized.replace("hls_stream", "hls").replace("rtmp_stream", "rtmp")
        return normalized or None

    def _error_signature(self, error: str | None) -> str:
        if not error:
            return "unknown_error"
        lowered = str(error).strip().lower()
        if "timeout" in lowered:
            return "timeout_error"
        if "not found" in lowered or "404" in lowered:
            return "not_found_error"
        if "permission" in lowered or "unauthorized" in lowered or "forbidden" in lowered:
            return "permission_error"
        if "connection" in lowered or "network" in lowered or "dns" in lowered:
            return "connection_error"

        compact = re.sub(r"\s+", " ", lowered)
        return compact[:120]

    def compact(self, decay: float = 0.99) -> None:
        with self._lock:
            decay = max(0.90, min(float(decay), 1.0))
            for _, entry in self.data.items():
                successes = float(entry.get("successes", 0) or 0)
                failures = float(entry.get("failures", 0) or 0)
                executions = float(entry.get("executions", 0) or 0)

                entry["successes"] = round(max(successes * decay, 0.0), 4)
                entry["failures"] = round(max(failures * decay, 0.0), 4)
                entry["executions"] = round(max(executions * decay, 0.0), 4)

                signatures = entry.get("failure_signatures") or {}
                for key in list(signatures.keys()):
                    signatures[key] = round(max(float(signatures[key]) * decay, 0.0), 4)
                    if signatures[key] < 0.01:
                        signatures.pop(key, None)

                replacement_outcomes = entry.get("replacement_outcomes") or {}
                for field in ("total", "success", "failure"):
                    if field not in replacement_outcomes:
                        continue
                    replacement_outcomes[field] = round(max(float(replacement_outcomes[field]) * decay, 0.0), 4)
                    if replacement_outcomes[field] < 0.01:
                        replacement_outcomes[field] = 0.0
                if replacement_outcomes.get("total", 0.0) <= 0:
                    replacement_outcomes["last_updated"] = None

                replacement_pairs = entry.get("replacement_pairs") or {}
                for pair_key in list(replacement_pairs.keys()):
                    pair_entry = replacement_pairs.get(pair_key) or {}
                    for field in ("success", "failure"):
                        if field not in pair_entry:
                            continue
                        pair_entry[field] = round(max(float(pair_entry[field]) * decay, 0.0), 4)
                    if max(float(pair_entry.get("success", 0.0) or 0.0), float(pair_entry.get("failure", 0.0) or 0.0)) < 0.01:
                        replacement_pairs.pop(pair_key, None)

    def _ensure_skill_entry(self, skill_name: str, context_key: str | None = None) -> Dict[str, Any]:
        with self._lock:
            canonical_context = self._canonicalize_context_key(context_key)
            key = self._memory_key(skill_name, canonical_context)
            if key not in self.data:
                self.data[key] = {
                    "executions": 0,
                    "successes": 0,
                    "failures": 0,
                    "avg_latency": 0.0,
                    "failure_signatures": {},
                    "last_updated": None,
                    "skill": skill_name,
                    "context_key": canonical_context,
                }
            return self.data[key]

    def log_execution(
        self,
        skill_name: str,
        success: bool,
        latency: float,
        error: str | None = None,
        context_key: str | None = None,
    ) -> None:
        with self._lock:
            canonical_context = self._canonicalize_context_key(context_key)
            skill = self._ensure_skill_entry(skill_name, canonical_context)
            skill["executions"] += 1

            if success:
                skill["successes"] += 1
            else:
                skill["failures"] += 1
                signature = self._error_signature(error)
                signatures = skill["failure_signatures"]
                signatures[signature] = signatures.get(signature, 0) + 1

            executions = max(skill["executions"], 1)
            previous_avg = float(skill.get("avg_latency", 0.0) or 0.0)
            skill["avg_latency"] = ((previous_avg * (executions - 1)) + max(float(latency), 0.0)) / executions
            skill["last_updated"] = datetime.now(timezone.utc).isoformat()

            if skill["executions"] and int(skill["executions"]) % 25 == 0:
                self.compact(decay=0.99)

        self.save()

    def log_replacement_outcome(
        self,
        skill_name: str,
        result: str,
        replaced_from: str | None = None,
        context_key: str | None = None,
    ) -> None:
        with self._lock:
            normalized_result = str(result or "").strip().lower()
            if normalized_result not in {"success", "failure"}:
                raise ValueError("result must be 'success' or 'failure'")
            ts = datetime.now(timezone.utc).isoformat()

            skill = self._ensure_skill_entry(skill_name, context_key)
            outcomes = skill.setdefault(
                "replacement_outcomes",
                {"total": 0, "success": 0, "failure": 0, "last_updated": None},
            )
            outcomes["total"] = float(outcomes.get("total", 0) or 0) + 1
            outcomes[normalized_result] = float(outcomes.get(normalized_result, 0) or 0) + 1
            outcomes["last_updated"] = ts

            original_skill = str(replaced_from or "").strip()
            if original_skill:
                pairs = skill.setdefault("replacement_pairs", {})
                pair = pairs.setdefault(original_skill, {"success": 0, "failure": 0, "last_updated": None})
                pair[normalized_result] = float(pair.get(normalized_result, 0) or 0) + 1
                pair["last_updated"] = ts

            skill["last_updated"] = ts
        self.save()

    def log_operator_feedback(
        self,
        edit_type: str,
        skill_name: str | None = None,
        from_skill: str | None = None,
        to_skill: str | None = None,
        context_key: str | None = None,
    ) -> None:
        """Record a dampened operator-feedback signal (swap / skip / reorder).

        Signals are stored inside the normal skill memory entry under the
        ``operator_feedback`` sub-key so they survive across restarts and
        participate in the existing compact/decay cycle.
        """
        with self._lock:
            ts = datetime.now(timezone.utc).isoformat()
            canonical = self._canonicalize_context_key(context_key)

            def _ensure_entry(key: str, skill: str) -> Dict[str, Any]:
                if key not in self.data:
                    self._ensure_skill_entry(skill, canonical)
                if "operator_feedback" not in self.data[key]:
                    self.data[key]["operator_feedback"] = {
                        "swaps_to": 0,
                        "swaps_from": 0,
                        "skips": 0,
                        "reorders_up": 0,
                        "reorders_down": 0,
                        "last_feedback": None,
                    }
                else:
                    fb = self.data[key]["operator_feedback"]
                    fb.setdefault("reorders_up", 0)
                    fb.setdefault("reorders_down", 0)
                return self.data[key]["operator_feedback"]

            if edit_type == "reorder" and skill_name:
                direction = str(from_skill or "").strip().lower()  # reuse from_skill as direction field
                fb = _ensure_entry(self._memory_key(skill_name, canonical), skill_name)
                if direction == "up":
                    fb["reorders_up"] = fb.get("reorders_up", 0) + 1
                else:
                    fb["reorders_down"] = fb.get("reorders_down", 0) + 1
                fb["last_feedback"] = ts

            elif edit_type == "swap" and from_skill and to_skill:
                fb_from = _ensure_entry(self._memory_key(from_skill, canonical), from_skill)
                fb_from["swaps_from"] = fb_from.get("swaps_from", 0) + 1
                fb_from["last_feedback"] = ts

                fb_to = _ensure_entry(self._memory_key(to_skill, canonical), to_skill)
                fb_to["swaps_to"] = fb_to.get("swaps_to", 0) + 1
                fb_to["last_feedback"] = ts

            elif edit_type == "skip" and skill_name:
                fb = _ensure_entry(self._memory_key(skill_name, canonical), skill_name)
                fb["skips"] = fb.get("skips", 0) + 1
                fb["last_feedback"] = ts

        self.save()

    def get_feedback_summary(self) -> Dict[str, Any]:
        """Return a mapping of all skills that have received operator feedback."""
        result: Dict[str, Any] = {}
        for key, entry in self.data.items():
            fb = entry.get("operator_feedback") or {}
            outcomes = entry.get("replacement_outcomes") or {}
            total_outcomes = float(outcomes.get("total", 0) or 0)
            if any(fb.get(f, 0) for f in ("swaps_to", "swaps_from", "skips", "reorders_up", "reorders_down")) or total_outcomes > 0:
                success_total = float(outcomes.get("success", 0) or 0)
                failure_total = float(outcomes.get("failure", 0) or 0)
                result[key] = {
                    "skill": entry.get("skill", key),
                    "context_key": entry.get("context_key"),
                    "swaps_to": fb.get("swaps_to", 0),
                    "swaps_from": fb.get("swaps_from", 0),
                    "skips": fb.get("skips", 0),
                    "reorders_up": fb.get("reorders_up", 0),
                    "reorders_down": fb.get("reorders_down", 0),
                    "last_feedback": fb.get("last_feedback"),
                    "replacement_outcomes": {
                        "total": int(round(total_outcomes)),
                        "success": int(round(success_total)),
                        "failure": int(round(failure_total)),
                        "last_updated": outcomes.get("last_updated"),
                    },
                    "replacement_success_rate": round(success_total / total_outcomes, 4) if total_outcomes else None,
                }
        return result
