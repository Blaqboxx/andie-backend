from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

from .observability_alerts import emit_observability_alert


ALLOW_BACKFILL = os.environ.get("ANDIE_ALLOW_BACKFILL", "false").strip().lower() in {"1", "true", "yes", "on"}
OUTCOME_WEIGHT_REGISTRY_KEY = "__outcome_weight_registry__"
EFFECTIVENESS_TREND_REGISTRY_KEY = "__effectiveness_trend_registry__"


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

    def _normalize_outcome_registry_field(self, value: str | None) -> str | None:
        normalized = re.sub(r"[^a-z0-9:_|\-]+", "_", str(value or "").strip().lower()).strip("_")
        return normalized or None

    def _outcome_registry(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            registry = self.data.get(OUTCOME_WEIGHT_REGISTRY_KEY)
            if not isinstance(registry, dict):
                registry = {}
                self.data[OUTCOME_WEIGHT_REGISTRY_KEY] = registry
            return registry

    def _outcome_weight_key(
        self,
        intent_type: str | None,
        governance_profile: str | None,
        portfolio_group: str | None = None,
    ) -> str | None:
        intent = self._normalize_outcome_registry_field(intent_type)
        governance = self._normalize_outcome_registry_field(governance_profile)
        portfolio = self._normalize_outcome_registry_field(portfolio_group)
        if not intent or not governance:
            return None
        if portfolio:
            return f"{intent}::{governance}::{portfolio}"
        return f"{intent}::{governance}"

    def _parse_event_timestamp(self, observed_at: str | None = None) -> datetime:
        if not observed_at:
            return datetime.now(timezone.utc)
        try:
            normalized = str(observed_at).strip()
            if normalized.endswith("Z"):
                normalized = normalized[:-1] + "+00:00"
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)

    def _effectiveness_registry(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            registry = self.data.get(EFFECTIVENESS_TREND_REGISTRY_KEY)
            if not isinstance(registry, dict):
                registry = {}
                self.data[EFFECTIVENESS_TREND_REGISTRY_KEY] = registry
            return registry

    def _window_stats(self, samples: list[Dict[str, Any]], now: datetime, days: int) -> Dict[str, Any]:
        cutoff = now - timedelta(days=max(1, int(days)))
        window_scores: list[float] = []
        for sample in samples:
            sample_ts = self._parse_event_timestamp(sample.get("timestamp"))
            if sample_ts >= cutoff:
                try:
                    score = max(0.0, min(float(sample.get("effectiveness_score", 0.0) or 0.0), 1.0))
                except Exception:
                    score = 0.0
                window_scores.append(score)
        sample_count = len(window_scores)
        average = (sum(window_scores) / sample_count) if sample_count else 0.0
        return {
            "sample_count": sample_count,
            "avg_effectiveness": round(average, 4),
        }

    def record_effectiveness_trend(
        self,
        *,
        intent_type: str,
        governance_profile: str,
        effectiveness_score: float,
        portfolio_group: str | None = None,
        observed_at: str | None = None,
    ) -> Dict[str, Any]:
        with self._lock:
            key = self._outcome_weight_key(intent_type, governance_profile, portfolio_group)
            if not key:
                raise ValueError("intent_type and governance_profile are required")

            now_dt = self._parse_event_timestamp(observed_at)
            now_iso = now_dt.isoformat()
            effectiveness = max(0.0, min(float(effectiveness_score), 1.0))

            registry = self._effectiveness_registry()
            bucket = registry.setdefault(
                key,
                {
                    "intent_type": self._normalize_outcome_registry_field(intent_type),
                    "governance_profile": self._normalize_outcome_registry_field(governance_profile),
                    "portfolio_group": self._normalize_outcome_registry_field(portfolio_group),
                    "samples": [],
                    "window_30d": {"sample_count": 0, "avg_effectiveness": 0.0},
                    "window_90d": {"sample_count": 0, "avg_effectiveness": 0.0},
                    "last_updated": None,
                },
            )

            previous_30d = dict(bucket.get("window_30d") or {})
            previous_90d = dict(bucket.get("window_90d") or {})

            samples = bucket.setdefault("samples", [])
            samples.append(
                {
                    "timestamp": now_iso,
                    "effectiveness_score": round(effectiveness, 4),
                }
            )

            cutoff_90 = now_dt - timedelta(days=90)
            retained = [
                sample
                for sample in samples
                if self._parse_event_timestamp(sample.get("timestamp")) >= cutoff_90
            ]
            removed_samples = max(0, len(samples) - len(retained))
            bucket["samples"] = retained

            window_30d = self._window_stats(retained, now_dt, 30)
            window_90d = self._window_stats(retained, now_dt, 90)

            bucket["window_30d"] = window_30d
            bucket["window_90d"] = window_90d
            bucket["last_updated"] = now_iso

            previous_30_avg = float(previous_30d.get("avg_effectiveness", 0.0) or 0.0)
            current_30_avg = float(window_30d.get("avg_effectiveness", 0.0) or 0.0)
            trend_delta = round(current_30_avg - previous_30_avg, 4)
            if trend_delta > 0.01:
                trend_direction = "improving"
            elif trend_delta < -0.01:
                trend_direction = "declining"
            else:
                trend_direction = "stable"

            snapshot = {
                "intent_type": bucket.get("intent_type"),
                "governance_profile": bucket.get("governance_profile"),
                "portfolio_group": bucket.get("portfolio_group"),
                "window_30d": window_30d,
                "window_90d": window_90d,
                "last_updated": bucket.get("last_updated"),
                "available": window_90d.get("sample_count", 0) > 0,
            }

            baseline_update = {
                "event": "coordinator.effectiveness_baseline_updated",
                "window": "90d",
                "previous_average": round(float(previous_90d.get("avg_effectiveness", 0.0) or 0.0), 4),
                "current_average": window_90d.get("avg_effectiveness", 0.0),
                "sample_count": int(window_90d.get("sample_count", 0) or 0),
                "intent_type": snapshot.get("intent_type"),
                "governance_profile": snapshot.get("governance_profile"),
                "portfolio_group": snapshot.get("portfolio_group"),
                "timestamp": now_iso,
            }

            trend_update = {
                "event": "coordinator.effectiveness_trend_updated",
                "window": "30d",
                "previous_average": round(previous_30_avg, 4),
                "current_average": window_30d.get("avg_effectiveness", 0.0),
                "trend": trend_direction,
                "delta": trend_delta,
                "sample_count": int(window_30d.get("sample_count", 0) or 0),
                "intent_type": snapshot.get("intent_type"),
                "governance_profile": snapshot.get("governance_profile"),
                "portfolio_group": snapshot.get("portfolio_group"),
                "timestamp": now_iso,
            }

            window_rotation_update = None
            if removed_samples > 0:
                window_rotation_update = {
                    "event": "coordinator.effectiveness_window_rotated",
                    "window": "90d",
                    "removed_samples": int(removed_samples),
                    "sample_count": int(window_90d.get("sample_count", 0) or 0),
                    "intent_type": snapshot.get("intent_type"),
                    "governance_profile": snapshot.get("governance_profile"),
                    "portfolio_group": snapshot.get("portfolio_group"),
                    "timestamp": now_iso,
                }

        self.save()
        return {
            "registry": snapshot,
            "baseline_update": baseline_update,
            "trend_update": trend_update,
            "window_rotation_update": window_rotation_update,
        }

    def get_effectiveness_trend(
        self,
        *,
        intent_type: str | None,
        governance_profile: str | None,
        portfolio_group: str | None = None,
    ) -> Dict[str, Any]:
        key = self._outcome_weight_key(intent_type, governance_profile, portfolio_group)
        normalized_intent = self._normalize_outcome_registry_field(intent_type)
        normalized_governance = self._normalize_outcome_registry_field(governance_profile)
        normalized_portfolio = self._normalize_outcome_registry_field(portfolio_group)
        if not key:
            return {
                "intent_type": normalized_intent,
                "governance_profile": normalized_governance,
                "portfolio_group": normalized_portfolio,
                "window_30d": {"sample_count": 0, "avg_effectiveness": 0.0},
                "window_90d": {"sample_count": 0, "avg_effectiveness": 0.0},
                "available": False,
                "last_updated": None,
            }

        registry = self._effectiveness_registry()
        bucket = registry.get(key) or {}
        window_30d = dict(bucket.get("window_30d") or {"sample_count": 0, "avg_effectiveness": 0.0})
        window_90d = dict(bucket.get("window_90d") or {"sample_count": 0, "avg_effectiveness": 0.0})

        return {
            "intent_type": bucket.get("intent_type", normalized_intent),
            "governance_profile": bucket.get("governance_profile", normalized_governance),
            "portfolio_group": bucket.get("portfolio_group", normalized_portfolio),
            "window_30d": {
                "sample_count": int(window_30d.get("sample_count", 0) or 0),
                "avg_effectiveness": round(float(window_30d.get("avg_effectiveness", 0.0) or 0.0), 4),
            },
            "window_90d": {
                "sample_count": int(window_90d.get("sample_count", 0) or 0),
                "avg_effectiveness": round(float(window_90d.get("avg_effectiveness", 0.0) or 0.0), 4),
            },
            "available": int(window_90d.get("sample_count", 0) or 0) > 0,
            "last_updated": bucket.get("last_updated"),
        }

    def _trend_state(self, current: float, baseline: float, threshold: float = 0.01) -> str:
        delta = float(current) - float(baseline)
        if delta > threshold:
            return "improving"
        if delta < -threshold:
            return "declining"
        return "stable"

    def _compute_rollup(self, rows: list[Dict[str, Any]], scope_key: str, scope_value: str) -> Dict[str, Any]:
        sample_30 = 0
        sample_90 = 0
        weighted_30 = 0.0
        weighted_90 = 0.0
        last_updated = None
        intents: set[str] = set()
        portfolios: set[str] = set()
        governances: set[str] = set()

        for row in rows:
            window_30d = row.get("window_30d") or {}
            window_90d = row.get("window_90d") or {}
            c30 = int(window_30d.get("sample_count", 0) or 0)
            c90 = int(window_90d.get("sample_count", 0) or 0)
            a30 = float(window_30d.get("avg_effectiveness", 0.0) or 0.0)
            a90 = float(window_90d.get("avg_effectiveness", 0.0) or 0.0)

            sample_30 += c30
            sample_90 += c90
            weighted_30 += a30 * c30
            weighted_90 += a90 * c90

            intent = str(row.get("intent_type") or "").strip()
            governance = str(row.get("governance_profile") or "").strip()
            portfolio = str(row.get("portfolio_group") or "").strip()
            if intent:
                intents.add(intent)
            if governance:
                governances.add(governance)
            if portfolio:
                portfolios.add(portfolio)

            ts = row.get("last_updated")
            if isinstance(ts, str) and ts and (last_updated is None or ts > last_updated):
                last_updated = ts

        avg_30 = (weighted_30 / sample_30) if sample_30 else 0.0
        avg_90 = (weighted_90 / sample_90) if sample_90 else 0.0
        delta = round(avg_30 - avg_90, 4)

        return {
            scope_key: scope_value,
            "window_30d": {
                "sample_count": sample_30,
                "avg_effectiveness": round(avg_30, 4),
            },
            "window_90d": {
                "sample_count": sample_90,
                "avg_effectiveness": round(avg_90, 4),
            },
            "comparative_baseline": {
                "delta_30d_vs_90d": delta,
                "trend": self._trend_state(avg_30, avg_90),
            },
            "coverage": {
                "intent_types": sorted(intents),
                "governance_profiles": sorted(governances),
                "portfolio_groups": sorted(portfolios),
            },
            "available": sample_90 > 0,
            "last_updated": last_updated,
        }

    def get_effectiveness_portfolio_rollup(self, portfolio_group: str | None) -> Dict[str, Any]:
        normalized_portfolio = self._normalize_outcome_registry_field(portfolio_group)
        if not normalized_portfolio:
            return self._compute_rollup([], "portfolio_group", normalized_portfolio or "")

        registry = self._effectiveness_registry()
        rows = [
            row for row in registry.values()
            if self._normalize_outcome_registry_field(row.get("portfolio_group")) == normalized_portfolio
        ]
        return self._compute_rollup(rows, "portfolio_group", normalized_portfolio)

    def get_effectiveness_governance_rollup(self, governance_profile: str | None) -> Dict[str, Any]:
        normalized_governance = self._normalize_outcome_registry_field(governance_profile)
        if not normalized_governance:
            return self._compute_rollup([], "governance_profile", normalized_governance or "")

        registry = self._effectiveness_registry()
        rows = [
            row for row in registry.values()
            if self._normalize_outcome_registry_field(row.get("governance_profile")) == normalized_governance
        ]
        return self._compute_rollup(rows, "governance_profile", normalized_governance)

    def get_effectiveness_summary(self) -> Dict[str, Any]:
        registry = self._effectiveness_registry()
        rows = list(registry.values())

        portfolios = sorted({
            self._normalize_outcome_registry_field(row.get("portfolio_group"))
            for row in rows
            if self._normalize_outcome_registry_field(row.get("portfolio_group"))
        })
        governances = sorted({
            self._normalize_outcome_registry_field(row.get("governance_profile"))
            for row in rows
            if self._normalize_outcome_registry_field(row.get("governance_profile"))
        })

        portfolio_rollups = [self.get_effectiveness_portfolio_rollup(group) for group in portfolios]
        governance_rollups = [self.get_effectiveness_governance_rollup(profile) for profile in governances]
        overall = self._compute_rollup(rows, "scope", "overall")

        return {
            "portfolio_rollups": portfolio_rollups,
            "governance_rollups": governance_rollups,
            "overall": overall,
            "registry_entries": len(rows),
        }

    def record_outcome_weight(
        self,
        *,
        intent_type: str,
        governance_profile: str,
        effectiveness_score: float,
        portfolio_group: str | None = None,
    ) -> Dict[str, Any]:
        with self._lock:
            key = self._outcome_weight_key(intent_type, governance_profile, portfolio_group)
            if not key:
                raise ValueError("intent_type and governance_profile are required")

            registry = self._outcome_registry()
            effectiveness = max(0.0, min(float(effectiveness_score), 1.0))
            ts = datetime.now(timezone.utc).isoformat()
            bucket = registry.setdefault(
                key,
                {
                    "intent_type": self._normalize_outcome_registry_field(intent_type),
                    "governance_profile": self._normalize_outcome_registry_field(governance_profile),
                    "portfolio_group": self._normalize_outcome_registry_field(portfolio_group),
                    "sample_count": 0,
                    "success_count": 0,
                    "average_effectiveness": 0.0,
                    "recommendation_weight": 0.5,
                    "last_updated": None,
                },
            )

            sample_count = int(bucket.get("sample_count", 0) or 0) + 1
            previous_average = float(bucket.get("average_effectiveness", 0.0) or 0.0)
            average_effectiveness = ((previous_average * (sample_count - 1)) + effectiveness) / sample_count
            success_count = int(bucket.get("success_count", 0) or 0) + (1 if effectiveness >= 0.7 else 0)

            bucket["sample_count"] = sample_count
            bucket["success_count"] = success_count
            bucket["average_effectiveness"] = round(average_effectiveness, 4)
            bucket["recommendation_weight"] = round(average_effectiveness, 4)
            bucket["last_updated"] = ts

            snapshot = self.get_outcome_weight(
                intent_type=intent_type,
                governance_profile=governance_profile,
                portfolio_group=portfolio_group,
            )
        self.save()
        return snapshot

    def get_outcome_weight(
        self,
        *,
        intent_type: str | None,
        governance_profile: str | None,
        portfolio_group: str | None = None,
    ) -> Dict[str, Any]:
        key = self._outcome_weight_key(intent_type, governance_profile, portfolio_group)
        normalized_intent = self._normalize_outcome_registry_field(intent_type)
        normalized_governance = self._normalize_outcome_registry_field(governance_profile)
        normalized_portfolio = self._normalize_outcome_registry_field(portfolio_group)
        if not key:
            return {
                "intent_type": normalized_intent,
                "governance_profile": normalized_governance,
                "portfolio_group": normalized_portfolio,
                "sample_count": 0,
                "success_count": 0,
                "average_effectiveness": 0.0,
                "recommendation_weight": 0.5,
                "modifier": 0.0,
                "available": False,
                "last_updated": None,
            }

        registry = self._outcome_registry()
        bucket = registry.get(key) or {}
        sample_count = int(bucket.get("sample_count", 0) or 0)
        average_effectiveness = max(0.0, min(float(bucket.get("average_effectiveness", 0.0) or 0.0), 1.0))
        sample_factor = min(sample_count / 5.0, 1.0)
        modifier = max(-0.15, min((average_effectiveness - 0.5) * 0.30 * sample_factor, 0.15)) if sample_count else 0.0
        return {
            "intent_type": bucket.get("intent_type", normalized_intent),
            "governance_profile": bucket.get("governance_profile", normalized_governance),
            "portfolio_group": bucket.get("portfolio_group", normalized_portfolio),
            "sample_count": sample_count,
            "success_count": int(bucket.get("success_count", 0) or 0),
            "average_effectiveness": round(average_effectiveness, 4),
            "recommendation_weight": round(float(bucket.get("recommendation_weight", 0.5) or 0.5), 4),
            "modifier": round(modifier, 4),
            "available": sample_count > 0,
            "last_updated": bucket.get("last_updated"),
        }

    def compact(self, decay: float = 0.99) -> None:
        with self._lock:
            decay = max(0.90, min(float(decay), 1.0))
            for key, entry in self.data.items():
                if key in {OUTCOME_WEIGHT_REGISTRY_KEY, EFFECTIVENESS_TREND_REGISTRY_KEY}:
                    continue
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
            if key in {OUTCOME_WEIGHT_REGISTRY_KEY, EFFECTIVENESS_TREND_REGISTRY_KEY}:
                continue
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
