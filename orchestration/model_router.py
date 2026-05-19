"""Distributed model routing with centralized governance.

Phase 2 (Option D) extends the Phase 1 cognitive router by adding:
    - Per-model endpoint abstraction (multi-node ready)
    - Endpoint health and latency telemetry
    - Availability-aware model fallback selection
    - Structured routing audit including selected endpoint
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import os
import time
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4


class TaskType(Enum):
    """Task classification for routing."""
    ARCHITECTURE = "architecture"
    CODING = "coding"
    DEBUGGING = "debugging"
    REFACTORING = "refactoring"
    TERMINAL = "terminal"
    SHELL_COMMAND = "shell_command"
    BUILD = "build"
    PLANNING = "planning"
    REASONING = "reasoning"
    CONVERSATION = "conversation"
    PERSONALITY = "personality"
    SECURITY = "security"
    MEMORY_RECALL = "memory_recall"
    ORCHESTRATION = "orchestration"
    UNKNOWN = "unknown"


class ModelCapability(Enum):
    """Model capability classification."""
    LIGHTWEIGHT = "lightweight"
    GENERAL = "general"
    CODE_SPECIALIST = "code_specialist"
    EMBEDDING = "embedding"


@dataclass
class ModelDefinition:
    """Registration record for an available model."""
    name: str
    endpoint: str
    capability: ModelCapability
    context_length: int = 4096
    token_budget: Optional[int] = None
    enabled: bool = True
    preferred_for: List[TaskType] = None
    local_only: bool = False
    description: str = ""
    
    def __post_init__(self):
        if self.preferred_for is None:
            self.preferred_for = []


@dataclass
class RoutingDecision:
    """Output of routing decision."""
    decision_id: str
    timestamp_utc: str
    task_type: TaskType
    selected_model: str
    selected_endpoint: str
    reason: str
    alternatives: List[str]
    estimated_tokens: int
    route_type: str  # "local" | "remote" | "distributed"
    confidence: float  # 0.0-1.0
    actor: str
    correlation_id: Optional[str] = None


@dataclass
class ModelTelemetry:
    """Per-model endpoint health and latency observations."""
    endpoint: str
    available: bool = True
    checks_total: int = 0
    checks_failed: int = 0
    last_checked_utc: Optional[str] = None
    last_latency_ms: Optional[float] = None
    last_error: Optional[str] = None


class ModelRouter:
    """
    Cognitive routing layer for task-to-model dispatch.
    
    Centralizes:
      - Task classification
      - Model availability tracking
      - Routing policy enforcement
      - Decision audit logging
    """
    
    def __init__(self, default_model: str = "mistral:latest"):
        self.default_model = default_model
        self._models: Dict[str, ModelDefinition] = {}
        self._decisions: List[RoutingDecision] = []
        self._telemetry: Dict[str, ModelTelemetry] = {}
        self._max_audit_size = 500
        self._register_default_models()
    
    def _utc_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
    
    def _endpoint_for(self, alias: str, fallback: str) -> str:
        """Resolve endpoint from env overrides for distributed deployments."""
        env_map_raw = os.getenv("MODEL_ENDPOINTS_JSON", "").strip()
        if env_map_raw:
            try:
                endpoint_map = json.loads(env_map_raw)
                if isinstance(endpoint_map, dict) and alias in endpoint_map:
                    return str(endpoint_map[alias]).rstrip("/")
            except json.JSONDecodeError:
                pass

        env_key = f"MODEL_ENDPOINT_{alias.upper()}"
        return os.getenv(env_key, fallback).rstrip("/")

    def _register_default_models(self) -> None:
        """Register default model routing table."""

        # Defaults preserve current single-node behavior and can be overridden.
        default_local = os.getenv("MODEL_ENDPOINT_DEFAULT", "http://host.docker.internal:11434").rstrip("/")
        endpoint_phi3 = self._endpoint_for("phi3", default_local)
        endpoint_deepseek = self._endpoint_for("deepseek", default_local)
        endpoint_mistral = self._endpoint_for("mistral", default_local)
        endpoint_nomic = self._endpoint_for("nomic_embed", default_local)

        # Phi-3 Mini: fast, lightweight tasks
        self._models["phi3:mini"] = ModelDefinition(
            name="phi3:mini",
            endpoint=endpoint_phi3,
            capability=ModelCapability.LIGHTWEIGHT,
            context_length=4096,
            token_budget=None,
            enabled=True,
            preferred_for=[
                TaskType.TERMINAL,
                TaskType.SHELL_COMMAND,
                TaskType.BUILD,
            ],
            local_only=True,
            description="Lightweight model for fast terminal and build tasks",
        )
        
        # Mistral: orchestration and reasoning
        self._models["mistral:latest"] = ModelDefinition(
            name="mistral:latest",
            endpoint=endpoint_mistral,
            capability=ModelCapability.GENERAL,
            context_length=8192,
            token_budget=None,
            enabled=True,
            preferred_for=[
                TaskType.PLANNING,
                TaskType.REASONING,
                TaskType.CONVERSATION,
                TaskType.PERSONALITY,
                TaskType.ORCHESTRATION,
            ],
            local_only=True,
            description="General-purpose reasoning and personality model",
        )
        
        # Nomic Embed: embeddings
        self._models["nomic-embed-text:latest"] = ModelDefinition(
            name="nomic-embed-text:latest",
            endpoint=endpoint_nomic,
            capability=ModelCapability.EMBEDDING,
            context_length=8192,
            enabled=True,
            preferred_for=[
                TaskType.MEMORY_RECALL,
            ],
            local_only=True,
            description="Embedding model for memory and vector operations",
        )

        for model_name, model in self._models.items():
            self._telemetry[model_name] = ModelTelemetry(endpoint=model.endpoint)

    def _probe_endpoint(self, endpoint: str, timeout_seconds: float = 1.0) -> tuple[bool, Optional[float], Optional[str]]:
        """Probe Ollama-compatible endpoint via /api/tags."""
        started = time.perf_counter()
        url = f"{endpoint}/api/tags"
        try:
            req = Request(url, method="GET")
            with urlopen(req, timeout=timeout_seconds) as response:
                status = getattr(response, "status", 200)
                latency_ms = (time.perf_counter() - started) * 1000.0
                if 200 <= status < 500:
                    return True, latency_ms, None
                return False, latency_ms, f"unexpected_status_{status}"
        except URLError as exc:
            latency_ms = (time.perf_counter() - started) * 1000.0
            return False, latency_ms, str(exc)
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000.0
            return False, latency_ms, str(exc)
    
    def load_available_models(self) -> Dict[str, bool]:
        """
        Probe available models (for future multi-node support).
        Returns availability status for each registered model.
        """
        availability: Dict[str, bool] = {}
        endpoint_health: Dict[str, tuple[bool, Optional[float], Optional[str]]] = {}

        for model in self._models.values():
            if model.endpoint not in endpoint_health:
                endpoint_health[model.endpoint] = self._probe_endpoint(model.endpoint)

        for name, model in self._models.items():
            endpoint_ok, latency_ms, error = endpoint_health[model.endpoint]
            is_available = bool(model.enabled and endpoint_ok)
            availability[name] = is_available

            telemetry = self._telemetry[name]
            telemetry.available = is_available
            telemetry.checks_total += 1
            if not is_available:
                telemetry.checks_failed += 1
            telemetry.last_checked_utc = self._utc_now()
            telemetry.last_latency_ms = latency_ms
            telemetry.last_error = error if not is_available else None

        return availability
    
    def classify_task(self, task_description: str) -> TaskType:
        """
        Classify task based on keywords/heuristics.
        (Phase 2: can be enhanced with actual task classifier)
        """
        desc_lower = task_description.lower()
        
        # Simple keyword-based classification
        if any(word in desc_lower for word in ["arch", "design", "structure", "refactor", "optimize"]):
            return TaskType.ARCHITECTURE
        elif any(word in desc_lower for word in ["debug", "fix", "trace", "error", "bug"]):
            return TaskType.DEBUGGING
        elif any(word in desc_lower for word in ["write", "generate", "create", "implement", "code"]):
            return TaskType.CODING
        elif any(word in desc_lower for word in ["terminal", "shell", "bash", "cmd", "command"]):
            return TaskType.TERMINAL
        elif any(word in desc_lower for word in ["build", "compile", "package", "deploy"]):
            return TaskType.BUILD
        elif any(word in desc_lower for word in ["plan", "schedule", "orchestrate", "workflow"]):
            return TaskType.PLANNING
        elif any(word in desc_lower for word in ["think", "reason", "analyze", "evaluate"]):
            return TaskType.REASONING
        elif any(word in desc_lower for word in ["talk", "chat", "discuss", "hello", "hi"]):
            return TaskType.CONVERSATION
        elif any(word in desc_lower for word in ["memory", "recall", "context", "search", "find"]):
            return TaskType.MEMORY_RECALL
        elif any(word in desc_lower for word in ["security", "sentinel", "guard", "check", "verify"]):
            return TaskType.SECURITY
        
        return TaskType.UNKNOWN
    
    def select(
        self,
        task_type: str,
        task_description: str = "",
        actor: str = "system",
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Select best model for task.
        
        Returns routing decision with model name, reason, and alternatives.
        """
        decision_id = str(uuid4())
        
        # Parse task type
        try:
            task_enum = TaskType[task_type.upper()] if isinstance(task_type, str) else task_type
        except (KeyError, AttributeError):
            task_enum = self.classify_task(task_description)
        
        # Keep availability fresh with low-cost health probe.
        availability = self.load_available_models()

        # Score candidates
        scores = {}
        for name, model in self._models.items():
            if not model.enabled or not availability.get(name, False):
                continue
            
            # Base score
            score = 0.5
            
            # +0.3 if in preferred_for
            if task_enum in model.preferred_for:
                score += 0.3
            
            # +0.1 if same capability
            if model.capability == ModelCapability.CODE_SPECIALIST and task_enum in [
                TaskType.CODING, TaskType.ARCHITECTURE, TaskType.DEBUGGING
            ]:
                score += 0.1
            
            # -0.2 if lightweight and task is complex
            if model.capability == ModelCapability.LIGHTWEIGHT and task_enum in [
                TaskType.ARCHITECTURE, TaskType.REASONING
            ]:
                score -= 0.2
            
            scores[name] = score
        
        # Select best with explicit fallback semantics
        if not scores:
            selected_model = self.default_model
            default_ok = availability.get(selected_model, False)
            reason = (
                "no_candidates_available; using_default" if default_ok
                else "no_candidates_available; default_unavailable"
            )
            alternatives = []
            confidence = 0.0
        else:
            sorted_models = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            selected_model = sorted_models[0][0]
            confidence = min(1.0, sorted_models[0][1])
            reason = f"best_scored_{task_enum.value}"
            alternatives = [m[0] for m in sorted_models[1:3]]

        selected_endpoint = self._models.get(selected_model, ModelDefinition(
            name=selected_model,
            endpoint="unknown",
            capability=ModelCapability.GENERAL,
        )).endpoint

        route_type = "distributed"
        if "localhost" in selected_endpoint or "host.docker.internal" in selected_endpoint or "127.0.0.1" in selected_endpoint:
            route_type = "local"
        
        decision = RoutingDecision(
            decision_id=decision_id,
            timestamp_utc=self._utc_now(),
            task_type=task_enum,
            selected_model=selected_model,
            selected_endpoint=selected_endpoint,
            reason=reason,
            alternatives=alternatives,
            estimated_tokens=512,  # TODO: actual estimation
            route_type=route_type,
            confidence=confidence,
            actor=actor,
            correlation_id=correlation_id,
        )
        
        # Append to audit log (with size cap)
        self._decisions.append(decision)
        if len(self._decisions) > self._max_audit_size:
            self._decisions = self._decisions[-self._max_audit_size:]
        
        return {
            "model": selected_model,
            "endpoint": selected_endpoint,
            "reason": reason,
            "alternatives": alternatives,
            "estimated_tokens": decision.estimated_tokens,
            "route": decision.route_type,
            "confidence": confidence,
            "decision_id": decision_id,
        }
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get registered models and availability."""
        models = []
        for name in sorted(self._models.keys()):
            model = self._models[name]
            models.append({
                "name": model.name,
                "capability": model.capability.value,
                "endpoint": model.endpoint,
                "context_length": model.context_length,
                "enabled": model.enabled,
                "available": self._telemetry.get(name, ModelTelemetry(endpoint=model.endpoint)).available,
                "preferred_for": [t.value for t in model.preferred_for],
                "local_only": model.local_only,
                "description": model.description,
            })
        return {"models": models}
    
    def get_policy(self) -> Dict[str, Any]:
        """Get current routing policy."""
        return {
            "default_model": self.default_model,
            "task_types_supported": [t.value for t in TaskType],
            "capabilities": [c.value for c in ModelCapability],
            "governance": {
                "local_only_enforcement": True,
                "token_budgets_enabled": False,
            },
            "distributed_endpoints": {
                name: model.endpoint for name, model in sorted(self._models.items())
            },
        }

    def get_telemetry(self) -> Dict[str, Any]:
        """Return telemetry snapshot for endpoints and model availability."""
        return {
            "models": {
                name: {
                    "endpoint": telemetry.endpoint,
                    "available": telemetry.available,
                    "checks_total": telemetry.checks_total,
                    "checks_failed": telemetry.checks_failed,
                    "last_checked_utc": telemetry.last_checked_utc,
                    "last_latency_ms": telemetry.last_latency_ms,
                    "last_error": telemetry.last_error,
                }
                for name, telemetry in sorted(self._telemetry.items())
            }
        }
    
    def recent_decisions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return recent routing decisions."""
        decisions = []
        for d in self._decisions[-limit:]:
            decisions.append({
                "decision_id": d.decision_id,
                "timestamp_utc": d.timestamp_utc,
                "task_type": d.task_type.value,
                "selected_model": d.selected_model,
                "selected_endpoint": d.selected_endpoint,
                "reason": d.reason,
                "alternatives": d.alternatives,
                "confidence": d.confidence,
                "actor": d.actor,
            })
        return decisions
