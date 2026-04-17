
import logging
import sys
import importlib
import inspect
import requests
import os
import asyncio
import json
import shlex
import subprocess
import sys as runtime_sys
import tempfile
from datetime import datetime, timezone

_log = logging.getLogger(__name__)
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Any, Dict, List
from pathlib import Path
from autonomy.agent_runner import AgentRunner
from autonomy.autonomy_profiles import DEFAULT_PROFILE, PROFILES
from autonomy.confidence_engine import evaluate_overseer_decision, evaluate_plan
from autonomy.decision_engine import DecisionLayer
from autonomy.governance import evaluate_go_no_go
from autonomy.autonomy_controller import decide_execution_mode
from autonomy.learning_engine import memory as skill_learning_memory
from autonomy.learning_engine import score_skill as _score_skill
from autonomy.learning_engine import skill_memory_snapshot
from autonomy.memory_store import MemoryStore
from autonomy.plan_optimizer import (
    MIN_TRUST_THRESHOLD,
    apply_replacements,
    optimize_plan,
    prune_plan_with_reasons,
)
from autonomy.runtime_config import get_runtime_config, update_runtime_config
from autonomy.simulation_engine import simulate_with_feedback
from autonomy.trust_engine import compute_trust
from autonomy.rule_evaluator import get_nested_value
from autonomy.trigger_engine import TriggerEngine

workspace_root = Path(__file__).resolve().parent.parent.parent.parent
if str(workspace_root) not in sys.path:
    sys.path.insert(0, str(workspace_root))

backend_root = Path(__file__).resolve().parent.parent.parent
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

from event_bus import event_bus as websocket_event_bus
from interfaces.api.autonomy_engine import (
    get_autonomy_status,
    start_autonomy,
    stop_autonomy,
    disable_autonomy,
    enable_autonomy,
)
from interfaces.api.guardrails import guardrail_status
from interfaces.api.event_bus import emit_event, recent_events as recent_stream_events, subscribe as subscribe_stream, unsubscribe as unsubscribe_stream
from interfaces.api.event_schema import validate_event_payload
from interfaces.api.node_metrics import system_metrics
from interfaces.api.node_monitor import check_node_health
from interfaces.api.skill_control import blocked_primary_skill, describe_suppressed_skills, get_skill_control_state, list_routable_skills, skill_suppression_reason, update_skill_control_state
from interfaces.api.plan_store import load_latest_plan_snapshot, load_plan_snapshot, list_plan_snapshots, save_plan_snapshot
from autonomy.control_plane_metrics import control_plane_metrics
from interfaces.api.workflow_engine import workflow_engine
from scheduler.queue import add_task, cancel_task, claim_task, clear_tasks, complete, fail, get_task, queue_metrics, recent_tasks, request_manual_retry
from interfaces.api.orchestrator_runtime import handle_task, run_command_interface
from interfaces.api.security_sentinel import security_audit_log_path
from interfaces.api.trading_approvals import (
    get_trade_approval,
    list_trade_approvals,
    process_trading_approval_event,
    resolve_trade_approval,
)
from interfaces.api.knowledge import router as knowledge_router
from interfaces.api.autonomy_explainer import router as autonomy_explainer_router
from interfaces.api.identity import SEMANTIC_BOOTSTRAP_DEFAULTS
from interfaces.api.memory import (
    build_memory_context,
    extract_json_payload as extract_memory_json_payload,
    init_db as init_memory_db,
    memory_snapshot,
    seed_semantic_defaults,
    save_episode,
    save_procedural,
    save_semantic,
)
from interfaces.api.outcome_tracking import derive_replaced_from, record_skill_outcome_internal
from interfaces.api.self_build import (
    append_growth_entry,
    read_growth_log,
    read_skill_registry,
    run_improve,
    run_self_review,
    self_review_after_task_enabled,
    start_self_build_loop,
    stop_self_build_loop,
)
from interfaces.api.ws_state import schedule_broadcast as _ws_broadcast
from autonomy.trading_agent import execute_approved_trade
from andie.brain.system_prompt import build_system_prompt
from andie.trading.orchestrator import get_capital_state, list_cycle_history, run_capital_orchestration
from andie.brain.llm_router import call_llm
from andie.memory.memory_service import MemoryService
from skills import register_builtin_skills
from skills.executor import execute_skill, execute_skill_plan
from skills.registry import registry
from skills.router import build_execution_plan, build_skill_proposal, select_skill
from skills.tool_adapter import registry_to_tools

# --- Import new async orchestrator ---
from andie_core.async_core.orchestrator import AsyncOrchestrator
from andie_core.async_core.task_queue import AsyncTaskQueue
from andie_core.async_core.event_system import EventSystem

# --- Dynamic import helpers ---
andie_core_path = str(Path(__file__).resolve().parent.parent.parent / "andie" / "core")
if andie_core_path not in sys.path:
    sys.path.insert(0, andie_core_path)

agents_path = str(Path(__file__).resolve().parent.parent.parent / "andie" / "agents")
if agents_path not in sys.path:
    sys.path.insert(0, agents_path)

malk_agents_path = str(Path(__file__).resolve().parent.parent.parent / "services" / "malk" / "agents")
if malk_agents_path not in sys.path:
    sys.path.insert(0, malk_agents_path)

# --- FastAPI app ---
app = FastAPI()
memory_service = MemoryService()
register_builtin_skills()
app.include_router(knowledge_router)
app.include_router(autonomy_explainer_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup_self_build_loop() -> None:
    init_memory_db()
    seed_semantic_defaults(SEMANTIC_BOOTSTRAP_DEFAULTS)
    await start_self_build_loop()


@app.on_event("shutdown")
async def _shutdown_self_build_loop() -> None:
    await stop_self_build_loop()

SETTINGS_SCHEMA_VERSION = 1
SETTINGS_CONFIG_PATH = backend_root / "storage" / "config" / "control_plane_settings.json"

# --- Models ---
class OrchestratorRequest(BaseModel):
    task: str
    context: str = ""
    params: Dict[str, Any] = Field(default_factory=dict)

class AgentRequest(BaseModel):
    input: Any = None
    params: Dict[str, Any] = {}


class WorkflowRequest(BaseModel):
    task: str
    context: str = ""
    steps: list[str] = Field(default_factory=list)
    memory: Dict[str, Any] = Field(default_factory=dict)
    allowRecovery: bool = False

class TaskRequest(BaseModel):
    type: str
    payload: Any = None
    priority: int = 5
    preferredNode: str | None = None
    metadata: Dict[str, Any] = {}


class QueryRequest(BaseModel):
    query: str
    top_k: int = 5
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentRunRequest(BaseModel):
    task: str | None = None
    prompt: str | None = None
    input: Any = None
    agent: str | None = None
    system: str | None = None
    context: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CryptoniaOverseerRequest(BaseModel):
    task: str
    profile: str | None = "balanced"
    data_capability: str = "crypto_data"
    strategy_capability: str = "crypto_strategy"
    data_agent: str = "cryptonia_historical_agent"
    strategy_agent: str = "cryptonia_strategy_agent"
    constraints: Dict[str, Any] = Field(default_factory=dict)
    context: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CapitalOrchestrationRequest(BaseModel):
    current_balance: float
    monthly_deposit: float = 0.0
    deposit_amount: float = 0.0
    realized_pnl: float = 0.0
    symbol: str = "BTC"
    interval: str = "daily"
    timeframe: str = "1h"
    risk_level: str = "moderate"
    start: str | None = None
    end: str | None = None
    convert: str = "USD"
    count: int = 120
    active_ratio: float = 0.60
    weekly_deploy_rate: float = 0.25
    risk_per_trade_pct: float = 0.005
    fee_bps: float = 60.0
    target_balance: float = 100000.0
    horizon_months: int = 12


class SkillProposalRequest(BaseModel):
    task: str
    context: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SkillExecutionRequest(BaseModel):
    skill: str
    params: Dict[str, Any] = Field(default_factory=dict)
    actor: str | None = None
    replaced_from: str | None = None


class SkillPlanExecutionRequest(BaseModel):
    task: str
    params: Dict[str, Any] = Field(default_factory=dict)
    actor: str | None = None


class SkillControlRequest(BaseModel):
    incident_mode: bool | None = None
    blacklisted_skills: List[str] | None = None
    actor: str | None = None
    reason: str | None = None
    request_id: str | None = None


class SkillStepExecutionRequest(BaseModel):
    step: str
    params: Dict[str, Any] = Field(default_factory=dict)
    actor: str | None = None
    approved: bool = False
    override: bool = False
    reason: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    replaced_from: str | None = None


class AutonomyConfigUpdateRequest(BaseModel):
    profile: str | None = None
    exploration_rate: float | None = None
    trust_smoothing: float | None = None
    forced_mode: str | None = None
    drift_detected: bool | None = None
    drift_reason: str | None = None
    outcome_weighting_enabled: bool | None = None
    runtime_outcome_emission_enabled: bool | None = None
    observability_alerts_enabled: bool | None = None
    score_drift_spike_threshold: float | None = None


class PlanOptimizationRequest(BaseModel):
    plan: List[Any] = Field(default_factory=list)
    context_key: str | None = None
    profile: str | None = None
    min_trust_threshold: float | None = None
    context_match_min: float = 0.6


class SimulationRequest(BaseModel):
    plan: List[Any] = Field(default_factory=list)
    context_key: str | None = None
    failure_rate: float = 0.2
    seed: int | None = None
    apply_feedback: bool = False
    predictive: bool = True


class FrontendIssueRequest(BaseModel):
    issue: str
    context: str = ""
    files: List[str] = Field(default_factory=list)
    priority: int = 3
    preferredNode: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SelfReviewRequest(BaseModel):
    task_output: Dict[str, Any] = Field(default_factory=dict)
    output: str | None = None
    source: str | None = None


class SaveSessionRequest(BaseModel):
    session_id: str
    transcript: str


class EventPublishRequest(BaseModel):
    type: str
    severity: str | None = None
    status: str | None = None
    workflowId: str | None = None
    step: str | None = None
    result: Dict[str, Any] | None = None
    issue: Dict[str, Any] | None = None
    decision: str | None = None
    evaluation: Dict[str, Any] | None = None
    state: Dict[str, Any] | None = None
    attempt: int | None = None
    reason: str | None = None
    task: Dict[str, Any] | None = None
    retry: Dict[str, Any] | None = None
    failureClass: str | None = None
    retryDisposition: str | None = None
    level: str | None = None
    target: str | None = None
    message: str | None = None
    action: str | None = None
    metadata: Dict[str, Any] = {}


class TradeApprovalDecisionRequest(BaseModel):
    actor: str | None = None
    reason: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SettingsConfigRequest(BaseModel):
    schemaVersion: int = SETTINGS_SCHEMA_VERSION
    config: Dict[str, Any] = Field(default_factory=dict)
    updatedBy: str | None = None


class AutonomyControlRequest(BaseModel):
    pass


class AutonomyRulesUpdateRequest(BaseModel):
    rules: List[Dict[str, Any]] = Field(default_factory=list)
    updatedBy: str | None = None


class AutonomyRuleValidationRequest(BaseModel):
    rule: Dict[str, Any] = Field(default_factory=dict)
    existingRules: List[Dict[str, Any]] = Field(default_factory=list)


class AutonomyRuleSimulationRequest(BaseModel):
    rule: Dict[str, Any] = Field(default_factory=dict)
    event: Dict[str, Any] = Field(default_factory=dict)


class OperatorOverrideRequest(BaseModel):
    type: str  # "swap" | "skip"
    from_skill: str | None = None
    to_skill: str | None = None
    original: str | None = None  # alias for from_skill
    selected: str | None = None  # alias for to_skill
    skill_name: str | None = None  # alias for from_skill when type == "skip"
    context_key: str | None = None
    plan_id: str | None = None
    step_id: str | None = None
    reason: str | None = None
    source: str | None = None  # "replacement_candidate" | "selector" | "manual"


class SkillOutcomeRequest(BaseModel):
    skill: str
    result: str
    replaced_from: str | None = None
    context_key: str | None = None
    latency: float | None = None
    error: str | None = None
    record_execution: bool = True
    source: str = "live"  # "live" | "synthetic"


def _schedule_background_self_review(task_output: Dict[str, Any]) -> None:
    if not self_review_after_task_enabled():
        return

    async def _runner() -> None:
        try:
            await run_self_review(task_output)
        except Exception as exc:
            _log.warning("self-review failed: %s", exc)

    asyncio.create_task(_runner(), name="andie-task-self-review")


def default_settings_payload() -> Dict[str, Any]:
    return {
        "schemaVersion": SETTINGS_SCHEMA_VERSION,
        "savedAt": None,
        "updatedBy": None,
        "config": {},
    }


def read_settings_payload() -> Dict[str, Any]:
    if not SETTINGS_CONFIG_PATH.exists():
        return default_settings_payload()

    try:
        payload = json.loads(SETTINGS_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default_settings_payload()

    if not isinstance(payload, dict):
        return default_settings_payload()

    baseline = default_settings_payload()
    baseline["schemaVersion"] = payload.get("schemaVersion", SETTINGS_SCHEMA_VERSION)
    baseline["savedAt"] = payload.get("savedAt")
    baseline["updatedBy"] = payload.get("updatedBy")
    baseline["config"] = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    return baseline


def write_settings_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    SETTINGS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_CONFIG_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload



# --- Async orchestrator and event system instances ---
async_orchestrator = AsyncOrchestrator()
event_system = EventSystem()
_autonomy_trigger_engine: TriggerEngine | None = None
AUTONOMY_AGENT_ALIASES = {
    "self_healing_agent": "recovery_agent",
    "diagnostic_agent": "health_agent",
    "cryptonia_historical_agent": "coinmarketcap_agent",
    "cryptonia_market_data_agent": "coinmarketcap_agent",
    "cryptonia_coinmarketcap_agent": "coinmarketcap_agent",
    "cryptonia_decision_agent": "cryptonia_strategy_agent",
}

CAPABILITY_MAP = {
    "crypto_data": "coinmarketcap_agent",
    "crypto_strategy": "cryptonia_strategy_agent",
    "system_health": "health_agent",
    "self_recovery": "recovery_agent",
}

CAPABILITY_META = {
    "crypto_data": {"type": "read_only", "priority": 1},
    "crypto_strategy": {"type": "analytical", "priority": 2},
    "system_health": {"type": "monitoring", "priority": 3},
    "self_recovery": {"type": "maintenance", "priority": 4},
}

ALLOWED_ACTIVE_CAPABILITIES = ["crypto_data", "crypto_strategy"]


def resolve_agent_name(agent_name: str | None) -> str | None:
    if not agent_name:
        return None
    resolved_agent_name = AUTONOMY_AGENT_ALIASES.get(agent_name, agent_name)
    if resolved_agent_name != agent_name:
        _log.info("agent alias resolved: %s -> %s", agent_name, resolved_agent_name)
    return resolved_agent_name


def resolve_capability(capability_name: str) -> str:
    resolved = CAPABILITY_MAP.get(capability_name)
    if not resolved:
        raise HTTPException(status_code=400, detail=f"Unknown capability: {capability_name}")
    return resolved


def load_agent_module(agent_name: str):
    resolved_agent_name = resolve_agent_name(agent_name) or agent_name
    candidates = [
        resolved_agent_name,
        f"andie_core.agents.{resolved_agent_name}",
        f"andie.agents.{resolved_agent_name}",
        f"services.malk.agents.{resolved_agent_name}",
    ]
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return importlib.import_module(candidate)
        except ImportError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise ImportError(f"Unable to load agent module: {agent_name}")


def compatibility_agents() -> List[Dict[str, str]]:
    return [
        {"name": "agent_alpha", "role": "general"},
        {"name": "coinmarketcap_agent", "role": "crypto_historical_data"},
        {"name": "cryptonia_historical_agent", "role": "cryptonia_crypto_historical_data"},
        {"name": "cryptonia_strategy_agent", "role": "cryptonia_strategy_decision"},
        {"name": "frontend_ui_agent", "role": "frontend_issue_triage"},
        {"name": "health_agent", "role": "health"},
        {"name": "process_agent", "role": "process"},
        {"name": "recovery_agent", "role": "recovery"},
    ]


async def invoke_agent_module(agent_name: str, payload: Dict[str, Any]) -> Any:
    agent_mod = load_agent_module(agent_name)
    if hasattr(agent_mod, "run_agent"):
        if inspect.iscoroutinefunction(agent_mod.run_agent):
            return await agent_mod.run_agent(payload)
        return agent_mod.run_agent(payload)
    if hasattr(agent_mod, "main"):
        if inspect.iscoroutinefunction(agent_mod.main):
            return await agent_mod.main(payload)
        return agent_mod.main(payload)
    raise Exception(f"No run_agent or main() in {agent_name}")


async def run_capability(capability_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    resolved_agent = resolve_capability(capability_name)
    result = await invoke_agent_module(resolved_agent, payload)
    if isinstance(result, dict):
        result.setdefault("agent", resolved_agent)
        return result
    return {
        "status": "ok",
        "agent": resolved_agent,
        "result": result,
    }


def read_security_logs(limit: int = 50) -> List[Dict[str, Any]]:
    log_path = security_audit_log_path()
    if not log_path.exists():
        return [{"event": "No threats detected"}]

    entries: List[Dict[str, Any]] = []
    with log_path.open("r", encoding="utf-8") as handle:
        lines = handle.readlines()[-max(limit, 1) :]

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            entries.append({"event": "unparsed_log_line", "raw": line})
    return entries or [{"event": "No threats detected"}]


def enrich_scored_plan(
    scored_plan: List[Dict[str, Any]],
    global_mode: str = "assisted",
    profile: str = DEFAULT_PROFILE,
) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for step in scored_plan:
        step_name = str(step.get("step") or "").strip()
        skill = registry.get(step_name)
        snapshot = skill_memory_snapshot(
            step_name,
            context_key=step.get("context_key"),
            replaced_from=step.get("replacement_for"),
        )
        confidence = float(step.get("confidence", 0.0) or 0.0)
        risk = skill.risk_level if skill else "unknown"
        requires_approval = bool(skill.requires_approval) if skill else False
        blocked = risk == "high" and confidence < 0.6 and not requires_approval
        recommended_action = (
            "block"
            if blocked
            else "approve"
            if requires_approval or snapshot.get("unstable") or confidence < 0.75
            else "auto_execute"
        )
        trust = compute_trust(step_name, context_key=step.get("context_key"))
        execution_mode = decide_execution_mode(
            {"step": step_name, "risk": risk},
            context_key=step.get("context_key"),
            global_mode=global_mode,
            profile=profile,
        )
        enriched.append(
            {
                **step,
                "risk": risk,
                "requires_approval": requires_approval,
                "instability": bool(snapshot.get("unstable")),
                "failure_signatures": snapshot.get("failure_signatures") or {},
                "executions": snapshot.get("executions", 0),
                "successes": snapshot.get("successes", 0),
                "failures": snapshot.get("failures", 0),
                "avg_latency": snapshot.get("avg_latency", 0.0),
                "replacement_outcomes": snapshot.get("replacement_outcomes") or {},
                "replacement_success_rate": snapshot.get("replacement_success_rate"),
                "replacement_pair": snapshot.get("replacement_pair") or {},
                "pair_success_rate": snapshot.get("pair_success_rate"),
                "recommended_action": recommended_action,
                "blocked": blocked,
                "trust": trust,
                "execution_mode": execution_mode,
            }
        )
    return enriched


def resolve_runtime_policy(control_state: Dict[str, Any]) -> tuple[str, str]:
    config = get_runtime_config()
    profile = str(config.get("profile") or DEFAULT_PROFILE).strip().lower()
    if profile not in PROFILES:
        profile = DEFAULT_PROFILE

    if control_state.get("incident_mode"):
        return "incident", profile

    forced_mode = str(config.get("forced_mode") or "").strip().lower()
    if forced_mode in {"manual", "assisted", "auto", "incident"}:
        return forced_mode, profile

    global_mode = "assisted"
    return global_mode, profile


def maybe_trigger_safe_mode(plan_stability: float, replaced_count: int, pruned_count: int) -> Dict[str, Any]:
    metrics = control_plane_metrics.snapshot()
    total_exec = float(metrics.get("plan_execute_total", 0.0) or 0.0)
    failed_exec = float(metrics.get("plan_execute_failed", 0.0) or 0.0)
    failure_rate = (failed_exec / total_exec) if total_exec else 0.0

    replacement_pressure_den = float(replaced_count + pruned_count)
    replacement_pressure = (float(replaced_count) / replacement_pressure_den) if replacement_pressure_den else 0.0
    drift_intensity = max(
        0.0,
        min(
            (failure_rate * 0.40) + (replacement_pressure * 0.35) + ((1.0 - float(plan_stability)) * 0.25),
            1.0,
        ),
    )
    if drift_intensity >= 0.75:
        drift_severity = "severe"
    elif drift_intensity >= 0.50:
        drift_severity = "moderate"
    elif drift_intensity >= 0.25:
        drift_severity = "mild"
    else:
        drift_severity = "stable"

    recovery_ready = bool(
        plan_stability >= 0.70
        and replacement_pressure < 0.30
        and failure_rate <= 0.10
    )

    config = get_runtime_config()
    drift_detected = bool(plan_stability < 0.50 and replacement_pressure > 0.50 and failure_rate > 0.20)
    recovered = False
    if drift_detected:
        reason = (
            f"drift_detected stability={plan_stability:.3f} "
            f"replacement_pressure={replacement_pressure:.3f} failure_rate={failure_rate:.3f}"
        )
        config = update_runtime_config(
            {
                "forced_mode": "manual",
                "drift_detected": True,
                "drift_reason": reason,
                "drift_intensity": drift_intensity,
                "drift_severity": drift_severity,
            }
        )
    elif (
        bool(config.get("drift_detected"))
        and str(config.get("forced_mode") or "").strip().lower() == "manual"
        and recovery_ready
    ):
        config = update_runtime_config(
            {
                "forced_mode": None,
                "drift_detected": False,
                "drift_reason": None,
                "drift_intensity": drift_intensity,
                "drift_severity": drift_severity,
            }
        )
        recovered = True
    else:
        config = update_runtime_config(
            {
                "drift_intensity": drift_intensity,
                "drift_severity": drift_severity,
            }
        )

    return {
        "detected": drift_detected,
        "recovered": recovered,
        "planStability": round(float(plan_stability), 4),
        "replacementPressure": round(float(replacement_pressure), 4),
        "failureRate": round(float(failure_rate), 4),
        "intensity": round(float(drift_intensity), 4),
        "severity": drift_severity,
        "forcedMode": config.get("forced_mode"),
        "reason": config.get("drift_reason") if config.get("drift_detected") else None,
    }


def optimize_skill_plan(
    plan_steps: List[Any],
    context_key: str | None,
    min_trust_threshold: float | None = None,
    profile: str = DEFAULT_PROFILE,
    global_mode: str = "assisted",
    candidate_skills: List[Dict[str, Any]] | None = None,
    context_match_min: float = 0.6,
) -> Dict[str, Any]:
    pruned_result = prune_plan_with_reasons(
        plan_steps or [],
        context_key=context_key,
        min_trust_threshold=min_trust_threshold,
        profile=profile,
        global_mode=global_mode,
    )
    replacement_result = apply_replacements(
        pruned_result["kept"],
        pruned_result["pruned"],
        candidate_skills or [],
        context_key=context_key,
        profile=profile,
        global_mode=global_mode,
        context_match_min=context_match_min,
    )
    optimized = optimize_plan(replacement_result["kept"], context_key=context_key)
    return {
        "kept": optimized,
        "avoided": replacement_result["avoided"],
        "replaced": replacement_result["replaced"],
        "pruned": replacement_result["pruned"],
        "threshold": pruned_result["threshold"],
        "planStability": pruned_result["plan_stability"],
        "inputSteps": len(plan_steps or []),
        "outputSteps": len(optimized),
    }


def track_replacement_outcomes(
    replaced: List[Dict[str, Any]],
    execution_completed: List[Dict[str, Any]],
    context_key: str | None = None,
) -> Dict[str, int]:
    replaced_map: Dict[str, str] = {}
    for entry in replaced or []:
        original = str(entry.get("step") or "").strip()
        replacement = str((entry.get("replacement") or {}).get("skill") or "").strip()
        if original and replacement:
            replaced_map[replacement] = original

    success_count = 0
    failure_count = 0
    for execution in execution_completed or []:
        executed_skill = str(execution.get("skill") or execution.get("step") or "").strip()
        if executed_skill not in replaced_map:
            continue
        original_skill = replaced_map[executed_skill]
        failed = str(execution.get("status") or "").strip().lower() == "failed"
        if failed:
            failure_count += 1
            skill_learning_memory.log_replacement_outcome(
                executed_skill,
                result="failure",
                replaced_from=original_skill,
                context_key=context_key,
            )
            skill_learning_memory.log_operator_feedback("skip", skill_name=executed_skill, context_key=context_key)
            skill_learning_memory.log_operator_feedback("swap", from_skill=executed_skill, to_skill=original_skill, context_key=context_key)
            continue

        success_count += 1
        skill_learning_memory.log_replacement_outcome(
            executed_skill,
            result="success",
            replaced_from=original_skill,
            context_key=context_key,
        )
        skill_learning_memory.log_operator_feedback("swap", from_skill=original_skill, to_skill=executed_skill, context_key=context_key)

    if success_count:
        control_plane_metrics.increment("replacement_success_count", by=success_count)
    if failure_count:
        control_plane_metrics.increment("replacement_failure_count", by=failure_count)

    return {"success": success_count, "failure": failure_count}


def build_candidate_skill_pool(skills: List[Any]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for skill in skills or []:
        candidates.append(
            {
                "name": skill.name,
                "keywords": list(skill.keywords or []),
                "risk": skill.risk_level,
                "requires_approval": bool(skill.requires_approval),
                "depends_on": list(getattr(skill, "depends_on", []) or []),
                "context_tags": list(getattr(skill, "context_tags", []) or []),
            }
        )
    return candidates


def normalize_memory_results(results: List[Any]) -> List[Dict[str, Any]]:
    normalized = []
    for entry in results:
        if isinstance(entry, dict) and "section" in entry and "item" in entry:
            normalized.append(entry)
            continue
        normalized.append({"section": "memory", "item": entry})
    return normalized


def first_non_empty(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return None


def _is_generic_api_credentials_boilerplate(text: str) -> bool:
    normalized = str(text or "").lower()
    if len(normalized) < 180:
        return False
    marker_pool = [
        "obtain api credentials",
        "provide secure access",
        "configure api access",
        "define access permissions",
        "integration setup",
        "api key generation",
        "secure storage",
        "permission configuration",
        "set permissions",
        "securely store credentials",
        "integrate api client",
        "test api access",
        "share these credentials",
        "api key and secret",
        "if you provide me with the necessary credentials",
        "if you can provide the api credentials",
    ]
    marker_hits = sum(1 for marker in marker_pool if marker in normalized)
    numbered_steps = ("\n1." in normalized or "1. **" in normalized) and (
        "\n2." in normalized or "2. **" in normalized
    )
    credentials_context = (
        "api" in normalized
        and "credential" in normalized
        and ("secure" in normalized or "permission" in normalized)
    )
    primary_trigger = "obtain api credentials" in normalized
    return (primary_trigger or marker_hits >= 2) and numbered_steps and credentials_context


def _boilerplate_replacement() -> str:
    return (
        "I can help you configure API access, but I will not repeat the generic credential checklist. "
        "For this workspace, set credentials once in environment variables and then I will proceed with concrete steps: "
        "validate connection, run a capability test, and execute the requested workflow with clear pass/fail output."
    )


def _sanitize_repetitive_chat_output(value: Any) -> Any:
    if isinstance(value, str):
        if _is_generic_api_credentials_boilerplate(value):
            return _boilerplate_replacement()
        return value

    if isinstance(value, dict):
        cleaned = dict(value)
        # Common text fields returned by agents or LLM wrappers.
        for key in ("response", "result", "message", "output"):
            if key in cleaned and isinstance(cleaned.get(key), str):
                cleaned[key] = _sanitize_repetitive_chat_output(cleaned[key])
        return cleaned

    return value


def build_memory_augmented_system(base_system: str | None) -> str:
    if base_system is not None and str(base_system).strip():
        return str(base_system)
    return build_system_prompt()


def extract_workflow_id(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None

    return first_non_empty(
        value.get("workflowId"),
        value.get("workflow_id"),
        (value.get("result") or {}).get("workflowId") if isinstance(value.get("result"), dict) else None,
        (value.get("result") or {}).get("workflow_id") if isinstance(value.get("result"), dict) else None,
        (value.get("metadata") or {}).get("workflowId") if isinstance(value.get("metadata"), dict) else None,
        (value.get("metadata") or {}).get("workflow_id") if isinstance(value.get("metadata"), dict) else None,
    )
def build_agent_run_correlation(
    task: Dict[str, Any] | None,
    workflow_id: str | None,
    agent_name: str | None,
    resolved_agent_name: str | None = None,
) -> Dict[str, Any]:
    return {
        "agent": agent_name,
        "resolvedAgent": resolved_agent_name or agent_name,
        "taskId": task.get("id") if isinstance(task, dict) else None,
        "workflowId": workflow_id,
    }


async def publish_agent_run_event(
    *,
    status: str,
    task: Dict[str, Any] | None,
    agent_name: str | None,
    resolved_agent_name: str | None = None,
    prompt: str,
    workflow_id: str | None = None,
    result: Any = None,
    error: str | None = None,
) -> None:
    if task is None:
        return

    metadata = {
        "source": "agents_run_api",
        "agent": agent_name or "llm",
        "resolvedAgent": resolved_agent_name or agent_name or "llm",
        "prompt": prompt[:280],
    }
    if agent_name and resolved_agent_name and resolved_agent_name != agent_name:
        metadata["agentResolutionReason"] = "Alias mapping from semantic/domain agent name"
    if workflow_id:
        metadata["workflowId"] = workflow_id

    await publish_backend_event(
        {
            "type": "task_update",
            "status": status,
            "task": task,
            "workflowId": workflow_id,
            "result": result if isinstance(result, dict) else None,
            "reason": error,
            "metadata": metadata,
        }
    )

# Example event handler: queues a task in orchestrator
async def handle_event_task(payload):
    async def agent_task():
        agent_name = payload.get("agent")
        params = payload.get("params", {})
        agent_mod = load_agent_module(agent_name)
        if hasattr(agent_mod, "run_agent"):
            if inspect.iscoroutinefunction(agent_mod.run_agent):
                return await agent_mod.run_agent(params)
            else:
                return agent_mod.run_agent(params)
        elif hasattr(agent_mod, "main"):
            if inspect.iscoroutinefunction(agent_mod.main):
                return await agent_mod.main(params)
            else:
                return agent_mod.main(params)
        else:
            raise Exception(f"No run_agent or main() in {agent_name}")
    await async_orchestrator.add_task(agent_task(), priority=payload.get("priority", 1))

# Register the handler for a generic event type
event_system.register("agent_task", handle_event_task)


def task_stream_snapshot(limit: int = 8) -> Dict[str, Any]:
    return {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "queue": queue_metrics(),
        "tasks": recent_tasks(limit),
    }


def enrich_event_payload(payload: Dict[str, Any], snapshot_limit: int = 8) -> Dict[str, Any]:
    snapshot = task_stream_snapshot(snapshot_limit)
    task = payload.get("task")
    retry = payload.get("retry") or ((task or {}).get("retry") if isinstance(task, dict) else None)
    failure_class = payload.get("failureClass") or ((task or {}).get("failureClass") if isinstance(task, dict) else None)
    retry_disposition = payload.get("retryDisposition") or ((task or {}).get("retryDisposition") if isinstance(task, dict) else None)
    return {
        **payload,
        "type": payload.get("type", "task_update"),
        "status": payload.get("status"),
        "task": task,
        "retry": retry,
        "failureClass": failure_class,
        "retryDisposition": retry_disposition,
        "severity": payload.get("severity") or payload.get("level"),
        "level": payload.get("level"),
        "target": payload.get("target"),
        "message": payload.get("message"),
        "action": payload.get("action"),
        "metadata": payload.get("metadata") or {},
        "snapshot": snapshot,
        "updatedAt": snapshot["updatedAt"],
    }


async def publish_backend_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    is_valid, issues = validate_event_payload(payload)
    for issue in issues:
        _log.warning("event schema issue (type=%r): %s", payload.get("type"), issue)
    if not is_valid:
        raise ValueError(
            f"Rejected malformed event payload: {'; '.join(issues)}"
        )
    event = enrich_event_payload(payload)
    event = process_trading_approval_event(event)
    await websocket_event_bus.publish(event)
    await emit_event(event)
    await get_trigger_engine().process_event(event)
    return event


async def run_autonomy_agent(agent_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    resolved_agent_name = resolve_agent_name(agent_name) or agent_name
    agent_mod = load_agent_module(resolved_agent_name)
    if hasattr(agent_mod, "run_agent"):
        if inspect.iscoroutinefunction(agent_mod.run_agent):
            result = await agent_mod.run_agent(payload)
        else:
            result = agent_mod.run_agent(payload)
    elif hasattr(agent_mod, "main"):
        if inspect.iscoroutinefunction(agent_mod.main):
            result = await agent_mod.main(payload)
        else:
            result = agent_mod.main(payload)
    else:
        raise Exception(f"No run_agent or main() in {resolved_agent_name}")

    if isinstance(result, dict):
        result.setdefault("agent", resolved_agent_name)
        return result
    return {"result": result, "agent": resolved_agent_name}


async def publish_autonomy_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return await publish_backend_event(
        {
            **payload,
            "metadata": {
                **metadata,
                "autonomySource": metadata.get("autonomySource") or "trigger_engine",
            },
        }
    )


def get_trigger_engine() -> TriggerEngine:
    global _autonomy_trigger_engine

    if _autonomy_trigger_engine is not None:
        return _autonomy_trigger_engine

    rules_path = backend_root / "autonomy" / "rules" / "default_rules.json"
    decision_layer = DecisionLayer(memory_limit=50)
    runner = AgentRunner(run_agent=run_autonomy_agent, publish_event=publish_autonomy_event)
    _autonomy_trigger_engine = TriggerEngine(
        rules_path=rules_path,
        decision_layer=decision_layer,
        agent_runner=runner,
        publish_event=publish_autonomy_event,
    )
    return _autonomy_trigger_engine


def autonomy_rules_path() -> Path:
    return backend_root / "autonomy" / "rules" / "default_rules.json"


def persist_autonomy_rules(rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    payload = {"rules": rules}
    path = autonomy_rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return rules


SUPPORTED_AUTONOMY_OPERATORS = {"==", "!=", ">", "<"}
SUPPORTED_AUTONOMY_ACTIONS = {"TRIGGER_AGENT", "TRIGGER_AGENT_PLAN"}


def evaluate_autonomy_condition(condition: Dict[str, Any], event: Dict[str, Any], index: int) -> Dict[str, Any]:
    field = str(condition.get("field") or "").strip()
    operator = str(condition.get("operator") or "==").strip()
    expected = condition.get("value")

    if not field:
        return {
            "index": index,
            "field": field,
            "operator": operator,
            "expected": expected,
            "actual": None,
            "passed": False,
            "error": "Condition field is required",
        }

    actual = get_nested_value(event, field)
    if operator == "==":
        passed = actual == expected
    elif operator == "!=":
        passed = actual != expected
    elif operator in {">", "<"}:
        try:
            left = float(actual)
            right = float(expected)
            passed = left > right if operator == ">" else left < right
        except (TypeError, ValueError):
            passed = False
    else:
        passed = False

    return {
        "index": index,
        "field": field,
        "operator": operator,
        "expected": expected,
        "actual": actual,
        "passed": passed,
        "error": None if operator in SUPPORTED_AUTONOMY_OPERATORS else f"Unsupported operator: {operator}",
    }


def validate_autonomy_rule(rule: Dict[str, Any], existing_rules: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    existing_rules = existing_rules or []

    rule_id = str(rule.get("id") or "").strip()
    if not rule_id:
      errors.append("Rule id is required.")

    when = rule.get("when") if isinstance(rule.get("when"), dict) else {}
    event_type = str(when.get("eventType") or "").strip()
    if not event_type:
        errors.append("Rule.when.eventType is required.")

    conditions = when.get("conditions")
    if conditions is not None and not isinstance(conditions, list):
        errors.append("Rule.when.conditions must be a list when provided.")
        conditions = []
    elif conditions is None:
        conditions = []

    for index, condition in enumerate(conditions):
        if not isinstance(condition, dict):
            errors.append(f"Condition {index + 1} must be an object.")
            continue
        field = str(condition.get("field") or "").strip()
        operator = str(condition.get("operator") or "==").strip()
        if not field:
            errors.append(f"Condition {index + 1} is missing a field.")
        if operator not in SUPPORTED_AUTONOMY_OPERATORS:
            errors.append(f"Condition {index + 1} uses unsupported operator '{operator}'.")

    then = rule.get("then") if isinstance(rule.get("then"), dict) else {}
    action = str(then.get("action") or "").strip()
    if action and action not in SUPPORTED_AUTONOMY_ACTIONS:
        errors.append(f"Unsupported then.action '{action}'.")

    if action == "TRIGGER_AGENT":
        if not str(then.get("agent") or "").strip():
            errors.append("TRIGGER_AGENT requires then.agent.")

    if action == "TRIGGER_AGENT_PLAN":
        agents = then.get("agents")
        if not isinstance(agents, list) or not agents:
            errors.append("TRIGGER_AGENT_PLAN requires a non-empty then.agents list.")
        else:
            for index, agent_entry in enumerate(agents):
                if isinstance(agent_entry, dict):
                    agent_name = str(agent_entry.get("agent") or "").strip()
                else:
                    agent_name = str(agent_entry or "").strip()
                if not agent_name:
                    errors.append(f"Plan step {index + 1} is missing an agent name.")

    for numeric_field in ("priority", "cooldownMs"):
        value = rule.get(numeric_field)
        if value is None:
            continue
        try:
            if int(value) < 0:
                errors.append(f"{numeric_field} must be zero or greater.")
        except (TypeError, ValueError):
            errors.append(f"{numeric_field} must be numeric.")

    for sibling in existing_rules:
        if not isinstance(sibling, dict):
            continue
        sibling_id = str(sibling.get("id") or "").strip()
        if not sibling_id or sibling_id == rule_id:
            continue
        sibling_when = sibling.get("when") if isinstance(sibling.get("when"), dict) else {}
        sibling_conditions = sibling_when.get("conditions") if isinstance(sibling_when.get("conditions"), list) else []
        if sibling_when.get("eventType") == event_type and sibling_conditions == conditions:
            warnings.append(f"Rule overlaps with {sibling_id}: same event type and conditions.")

    return {
        "ruleId": rule_id or "unsaved_rule",
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
    }


def restart_command() -> str:
    backend_port = os.environ.get("ANDIE_THINKPAD_BACKEND_PORT") or os.environ.get("ANDIE_BACKEND_PORT") or "8000"
    backend_log = os.environ.get("ANDIE_THINKPAD_BACKEND_LOG", str(workspace_root / "logs" / "andie-backend.log"))
    escaped_backend_dir = shlex.quote(str(Path(__file__).resolve().parent.parent.parent))
    escaped_python = shlex.quote(runtime_sys.executable)
    escaped_log = shlex.quote(backend_log)
    return (
        "sleep 1; "
        "pkill -f 'uvicorn interfaces.api.main:app' >/dev/null 2>&1 || true; "
        f"cd {escaped_backend_dir} && "
        f"nohup {escaped_python} -m uvicorn interfaces.api.main:app --reload --host 0.0.0.0 --port {backend_port} > {escaped_log} 2>&1 < /dev/null &"
    )

# --- Async Orchestrator endpoint ---
@app.post("/orchestrator/run")
async def run_async_orchestrator(req: OrchestratorRequest):
    try:
        if req.params.get("queueOnly"):
            async def agent_task():
                agent_mod = load_agent_module(req.task)
                if hasattr(agent_mod, "run_agent"):
                    if inspect.iscoroutinefunction(agent_mod.run_agent):
                        return await agent_mod.run_agent(req.params)
                    return agent_mod.run_agent(req.params)
                if hasattr(agent_mod, "main"):
                    if inspect.iscoroutinefunction(agent_mod.main):
                        return await agent_mod.main(req.params)
                    return agent_mod.main(req.params)
                raise Exception(f"No run_agent or main() in {req.task}")

            await async_orchestrator.add_task(agent_task(), priority=req.params.get("priority", 1))
            return {"status": "queued"}

        result = await handle_task(req.task, req.context, req.params, restart_command())
        await publish_backend_event(
            {
                "type": "alert",
                "level": "info",
                "target": "orchestrator",
                "message": f"Command executed via {result.get('route', 'thinkpad')}.",
                "action": "orchestrator_run",
                "metadata": {
                    "task": req.task[:140],
                    "route": result.get("route"),
                    "targetNode": result.get("targetNode"),
                    "status": result.get("status"),
                    "type": result.get("type", "command"),
                    "dispatchReason": result.get("dispatchReason"),
                    "dispatchScore": result.get("dispatchScore"),
                    "rankedCandidates": result.get("rankedCandidates") or [],
                },
            }
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Async Agent endpoint (dynamic) ---
@app.post("/agent/{agent_name}")
async def run_agent_async(agent_name: str, req: AgentRequest):
    try:
        resolved_agent_name = resolve_agent_name(agent_name) or agent_name

        llm_input = {
            "prompt": req.input if isinstance(req.input, str) else str(req.input),
            "system": req.params.get("system", build_system_prompt()),
            "context": req.params.get("context", ""),
            "metadata": {"agent": agent_name, **req.params.get("metadata", {})}
        }

        result = await invoke_agent_module(agent_name, llm_input)
        if isinstance(result, dict):
            result.setdefault("agent", resolved_agent_name)
        return {
            "status": "executed",
            "result": result,
            "agentResolution": {
                "requested": agent_name,
                "resolved": resolved_agent_name,
                "reason": "Alias mapping from semantic/domain agent name" if resolved_agent_name != agent_name else "Direct agent resolution",
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/agents")
def list_agents_compat():
    return compatibility_agents()


@app.get("/agents/aliases")
def list_agent_aliases() -> Dict[str, str]:
    return dict(sorted(AUTONOMY_AGENT_ALIASES.items()))


@app.get("/agents/capabilities")
def list_agent_capabilities() -> Dict[str, Any]:
    entries = {}
    for capability, agent in CAPABILITY_MAP.items():
        entries[capability] = {
            "agent": agent,
            "meta": CAPABILITY_META.get(capability, {}),
        }
    return {
        "capabilities": entries,
        "allowedActiveCapabilities": list(ALLOWED_ACTIVE_CAPABILITIES),
    }


@app.get("/skills")
def list_skills() -> Dict[str, Any]:
    _, suppressed_map = list_routable_skills(registry.list())
    skills_payload = []
    for skill in registry.list():
        suppression_reason = suppressed_map.get(skill.name)
        skills_payload.append(
            {
                "name": skill.name,
                "description": skill.description,
                "input_schema": skill.input_schema,
                "risk_level": skill.risk_level,
                "requires_approval": skill.requires_approval,
                "keywords": skill.keywords,
                "suppressed": bool(suppression_reason),
                "suppression_reason": suppression_reason,
            }
        )
    return {
        "skills": skills_payload,
        "controlState": get_skill_control_state(),
    }


@app.get("/skills/trust")
def get_skills_with_trust() -> Dict[str, Any]:
    """Return all registered skills enriched with their current trust score
    and accumulated operator-feedback signals.  Used by the Skills Trust panel.
    """
    feedback_summary = skill_learning_memory.get_feedback_summary()
    _, suppressed_map = list_routable_skills(registry.list())
    skills_payload = []
    for skill in registry.list():
        ctx = None
        score = _score_skill(skill.name, context_key=ctx)
        # Find any feedback entries that match this skill name
        fb_entry = next(
            (v for v in feedback_summary.values() if v.get("skill") == skill.name and not v.get("context_key")),
            {}
        )
        skills_payload.append({
            "name": skill.name,
            "description": skill.description,
            "risk_level": skill.risk_level,
            "requires_approval": skill.requires_approval,
            "suppressed": bool(suppressed_map.get(skill.name)),
            "suppression_reason": suppressed_map.get(skill.name),
            "trust_score": score,
            "swaps_to": fb_entry.get("swaps_to", 0),
            "swaps_from": fb_entry.get("swaps_from", 0),
            "skips": fb_entry.get("skips", 0),
            "reorders_up": fb_entry.get("reorders_up", 0),
            "reorders_down": fb_entry.get("reorders_down", 0),
            "last_feedback": fb_entry.get("last_feedback"),
            "replacement_outcomes": fb_entry.get("replacement_outcomes"),
        })
    skills_payload.sort(key=lambda s: s["trust_score"], reverse=True)
    return {
        "skills": skills_payload,
        "total": len(skills_payload),
        "feedback_summary": feedback_summary,
    }


@app.get("/skills/control")
def get_skill_controls() -> Dict[str, Any]:
    return {
        "status": "ok",
        "controlState": get_skill_control_state(),
        "suppressedSkills": describe_suppressed_skills(registry.list()),
    }


@app.put("/skills/control")
async def update_skill_controls(req: SkillControlRequest) -> Dict[str, Any]:
    try:
        control_state = update_skill_control_state(
            incident_mode=req.incident_mode,
            blacklisted_skills=req.blacklisted_skills,
            updated_by=req.actor or "timeline-operator",
            reason=req.reason,
            request_id=req.request_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    suppressed_skills = describe_suppressed_skills(registry.list())
    await publish_backend_event(
        {
            "type": "alert",
            "level": "info",
            "target": "skills",
            "message": "Skill control policy updated.",
            "action": "skill_control_update",
            "metadata": {
                "source": "skills_api",
                "incidentMode": control_state.get("incident_mode"),
                "blacklistedSkills": control_state.get("blacklisted_skills") or [],
                "suppressedSkills": suppressed_skills,
                "updatedBy": req.actor or "timeline-operator",
                "reason": req.reason,
                "requestId": req.request_id,
            },
        }
    )
    return {
        "status": "saved",
        "controlState": control_state,
        "suppressedSkills": suppressed_skills,
    }


@app.get("/skills/tools")
def list_skill_tools() -> Dict[str, Any]:
    return {"tools": registry_to_tools()}


@app.get("/skills/learning")
def list_skill_learning() -> Dict[str, Any]:
    snapshots = [skill_memory_snapshot(skill.name) for skill in registry.list()]
    entries = []
    for key, value in sorted(skill_learning_memory.data.items()):
        entries.append(
            {
                "key": key,
                "skill": value.get("skill") or key,
                "context_key": value.get("context_key"),
                "executions": int(value.get("executions", 0) or 0),
                "successes": int(value.get("successes", 0) or 0),
                "failures": int(value.get("failures", 0) or 0),
                "avg_latency": round(float(value.get("avg_latency", 0.0) or 0.0), 4),
                "last_updated": value.get("last_updated"),
                "failure_signatures": value.get("failure_signatures") or {},
                "operator_feedback": value.get("operator_feedback") or {},
                "replacement_outcomes": value.get("replacement_outcomes") or {"total": 0, "success": 0, "failure": 0},
                "replacement_pairs": value.get("replacement_pairs") or {},
            }
        )
    return {
        "skills": snapshots,
        "entries": entries,
        "memoryPath": str(skill_learning_memory.path),
    }


@app.post("/skills/propose")
async def propose_skill(req: SkillProposalRequest) -> Dict[str, Any]:
    blocked_skill = blocked_primary_skill(req.task, registry.list())
    routable_skills, suppressed_map = list_routable_skills(registry.list())
    selected = None if blocked_skill else select_skill(req.task, routable_skills)
    proposal = build_skill_proposal(req.task, selected)
    await publish_backend_event(
        {
            "type": "task_update",
            "status": "queued",
            "message": "Skill proposal evaluated.",
            "metadata": {
                "source": "skills_api",
                "task": req.task[:140],
                "selectedSkill": proposal.get("selectedSkill"),
                "confidence": proposal.get("confidence"),
                "risk": proposal.get("risk"),
                "requiresApproval": proposal.get("requiresApproval"),
            },
        }
    )
    return {
        "status": "ok",
        "task": req.task,
        "proposal": proposal,
        "controlState": get_skill_control_state(),
        "suppressedSkills": [{"skill": skill, "reason": reason} for skill, reason in sorted(suppressed_map.items())],
        "blockedSkill": blocked_skill,
    }


@app.post("/skills/plan")
async def plan_skill(req: SkillProposalRequest) -> Dict[str, Any]:
    blocked_skill = blocked_primary_skill(req.task, registry.list())
    routable_skills, suppressed_map = list_routable_skills(registry.list())
    plan = {"selectedSkill": None, "confidence": 0.0, "requiresApproval": False, "risk": None, "plan": []} if blocked_skill else build_execution_plan(req.task, routable_skills)
    context_key = req.metadata.get("context_key") if isinstance(req.metadata, dict) else None
    control_state = get_skill_control_state()
    global_mode, profile = resolve_runtime_policy(control_state)

    optimization = optimize_skill_plan(
        plan.get("plan") or [],
        context_key=context_key,
        profile=profile,
        global_mode=global_mode,
        candidate_skills=build_candidate_skill_pool(routable_skills),
        context_match_min=0.6,
    )
    optimized_plan = optimization["kept"]
    plan["plan"] = optimized_plan
    if plan.get("selectedSkill") not in [str(step.get("step") if isinstance(step, dict) else step) for step in optimized_plan]:
        first_step = optimized_plan[0] if optimized_plan else None
        plan["selectedSkill"] = first_step.get("step") if isinstance(first_step, dict) else first_step
    if optimization["pruned"]:
        control_plane_metrics.increment("pruned_step_count", by=len(optimization["pruned"]))
        control_plane_metrics.increment(
            "pruned_predicted_failures",
            by=sum(float(item.get("failure_probability", 0.0) or 0.0) for item in optimization["pruned"]),
        )
    if optimization["replaced"]:
        control_plane_metrics.increment("replaced_step_count", by=len(optimization["replaced"]))
    drift = maybe_trigger_safe_mode(
        optimization["planStability"],
        len(optimization["replaced"]),
        len(optimization["pruned"]),
    )

    scored_plan = enrich_scored_plan(
        evaluate_plan(plan.get("plan") or [], context_key=context_key),
        global_mode=global_mode,
        profile=profile,
    )
    await publish_backend_event(
        {
            "type": "task_update",
            "status": "queued",
            "message": "Skill execution plan built.",
            "metadata": {
                "source": "skills_api",
                "task": req.task[:140],
                "selectedSkill": plan.get("selectedSkill"),
                "plan": plan.get("plan") or [],
                "confidence": plan.get("confidence"),
                "scoredPlan": scored_plan,
            },
        }
    )
    return {
        "status": "ok",
        "task": req.task,
        "plan": plan,
        "scoredPlan": scored_plan,
        "profile": profile,
        "avoided": optimization["avoided"],
        "replaced": optimization["replaced"],
        "pruned": optimization["pruned"],
        "minTrustThreshold": optimization["threshold"],
        "planStability": optimization["planStability"],
        "drift": drift,
        "controlState": get_skill_control_state(),
        "suppressedSkills": [{"skill": skill, "reason": reason} for skill, reason in sorted(suppressed_map.items())],
        "blockedSkill": blocked_skill,
    }


@app.post("/skills/execute")
async def execute_skill_endpoint(req: SkillExecutionRequest) -> Dict[str, Any]:
    skill = registry.get(req.skill)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Unknown skill: {req.skill}")
    suppression_reason = skill_suppression_reason(req.skill)
    if suppression_reason:
        return {
            "status": "blocked",
            "skill": req.skill,
            "reason": suppression_reason,
            "controlState": get_skill_control_state(),
        }
    if skill.requires_approval:
        return {
            "status": "pending_approval",
            "skill": req.skill,
            "risk": skill.risk_level,
            "requiresApproval": True,
        }

    context_key = req.params.get("context_key") if isinstance(req.params, dict) else None
    replaced_from = derive_replaced_from(req.replaced_from, req.params)
    try:
        execution = execute_skill(req.skill, req.params)
    except Exception as exc:
        record_skill_outcome_internal(
            req.skill,
            result="failure",
            context_key=context_key,
            replaced_from=replaced_from,
            error=str(exc),
            record_execution=False,
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    outcome = record_skill_outcome_internal(
        req.skill,
        result="success",
        context_key=context_key,
        replaced_from=replaced_from,
        latency=execution.get("latency") if isinstance(execution, dict) else None,
        record_execution=False,
    )
    await publish_backend_event(
        {
            "type": "task_update",
            "status": "done",
            "message": f"Skill {req.skill} executed.",
            "metadata": {
                "source": "skills_api",
                "skill": req.skill,
                "actor": req.actor or "operator",
                "risk": skill.risk_level,
            },
        }
    )
    return {"status": "ok", "execution": execution, "outcome": outcome}


@app.post("/skills/plan/execute")
async def execute_skill_plan_endpoint(req: SkillPlanExecutionRequest) -> Dict[str, Any]:
    blocked_skill = blocked_primary_skill(req.task, registry.list())
    routable_skills, suppressed_map = list_routable_skills(registry.list())
    plan = {"selectedSkill": None, "confidence": 0.0, "requiresApproval": False, "risk": None, "plan": []} if blocked_skill else build_execution_plan(req.task, routable_skills)
    context_key = req.params.get("context_key") if isinstance(req.params, dict) else None
    control_state = get_skill_control_state()
    global_mode, profile = resolve_runtime_policy(control_state)

    optimization = optimize_skill_plan(
        plan.get("plan") or [],
        context_key=context_key,
        profile=profile,
        global_mode=global_mode,
        candidate_skills=build_candidate_skill_pool(routable_skills),
        context_match_min=0.6,
    )
    optimized_plan = optimization["kept"]
    plan["plan"] = optimized_plan
    if plan.get("selectedSkill") not in [str(step.get("step") if isinstance(step, dict) else step) for step in optimized_plan]:
        first_step = optimized_plan[0] if optimized_plan else None
        plan["selectedSkill"] = first_step.get("step") if isinstance(first_step, dict) else first_step
    if optimization["pruned"]:
        control_plane_metrics.increment("pruned_step_count", by=len(optimization["pruned"]))
        control_plane_metrics.increment(
            "pruned_predicted_failures",
            by=sum(float(item.get("failure_probability", 0.0) or 0.0) for item in optimization["pruned"]),
        )
    if optimization["replaced"]:
        control_plane_metrics.increment("replaced_step_count", by=len(optimization["replaced"]))
    drift = maybe_trigger_safe_mode(
        optimization["planStability"],
        len(optimization["replaced"]),
        len(optimization["pruned"]),
    )

    scored_plan = enrich_scored_plan(
        evaluate_plan(plan.get("plan") or [], context_key=context_key),
        global_mode=global_mode,
        profile=profile,
    )
    selected_skill = plan.get("selectedSkill")
    if not selected_skill:
        return {
            "status": "no_skill",
            "task": req.task,
            "plan": plan,
            "scoredPlan": [],
            "profile": profile,
            "avoided": optimization["avoided"],
            "replaced": optimization["replaced"],
            "pruned": optimization["pruned"],
            "minTrustThreshold": optimization["threshold"],
            "planStability": optimization["planStability"],
            "drift": drift,
            "controlState": get_skill_control_state(),
            "suppressedSkills": [{"skill": skill, "reason": reason} for skill, reason in sorted(suppressed_map.items())],
            "blockedSkill": blocked_skill,
        }

    execution = execute_skill_plan(plan.get("plan") or [], req.params)
    status = execution.get("status") or "ok"
    replacement_outcomes = track_replacement_outcomes(
        optimization["replaced"],
        execution.get("completed") or [],
        context_key=context_key,
    )
    await publish_backend_event(
        {
            "type": "task_update",
            "status": "done" if status == "ok" else status,
            "message": f"Skill plan execution processed for {selected_skill}.",
            "metadata": {
                "source": "skills_api",
                "skill": selected_skill,
                "actor": req.actor or "operator",
                "plan": plan.get("plan") or [],
                "scoredPlan": scored_plan,
                "executionStatus": status,
            },
        }
    )
    return {
        "status": status,
        "task": req.task,
        "plan": plan,
        "scoredPlan": scored_plan,
        "profile": profile,
        "avoided": optimization["avoided"],
        "replaced": optimization["replaced"],
        "pruned": optimization["pruned"],
        "minTrustThreshold": optimization["threshold"],
        "planStability": optimization["planStability"],
        "drift": drift,
        "replacementOutcomes": replacement_outcomes,
        "execution": execution,
        "controlState": get_skill_control_state(),
        "suppressedSkills": [{"skill": skill, "reason": reason} for skill, reason in sorted(suppressed_map.items())],
        "blockedSkill": blocked_skill,
    }


@app.post("/skills/execute-step")
async def execute_skill_step_endpoint(req: SkillStepExecutionRequest) -> Dict[str, Any]:
    skill = registry.get(req.step)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Unknown skill step: {req.step}")
    suppression_reason = skill_suppression_reason(req.step)
    if suppression_reason:
        return {
            "status": "blocked",
            "step": req.step,
            "reason": suppression_reason,
            "controlState": get_skill_control_state(),
        }

    context_key = None
    if isinstance(req.metadata, dict):
        context_key = req.metadata.get("context_key")
    if not context_key and isinstance(req.params, dict):
        context_key = req.params.get("context_key")

    control_state = get_skill_control_state()
    global_mode, profile = resolve_runtime_policy(control_state)
    step_meta = enrich_scored_plan(
        evaluate_plan([{"step": req.step, "context_key": context_key}], context_key=context_key),
        global_mode=global_mode,
        profile=profile,
    )[0]

    if req.override and not req.reason:
        raise HTTPException(status_code=400, detail="Override reason is required")

    explicit_rejection = bool(req.reason) and not req.approved and not req.override

    if explicit_rejection and (skill.requires_approval or step_meta.get("blocked")):
        await publish_backend_event(
            {
                "type": "task_update",
                "status": "blocked",
                "message": f"Skill step {req.step} rejected.",
                "metadata": {
                    "source": "skills_api",
                    "skill": req.step,
                    "actor": req.actor or "operator",
                    "decision": "rejected",
                    "reason": req.reason or "operator_rejected",
                    "step": step_meta,
                },
            }
        )
        return {
            "status": "rejected",
            "step": req.step,
            "stepMeta": step_meta,
            "reason": req.reason or "operator_rejected",
        }

    if skill.requires_approval and not req.approved and not req.override:
        return {
            "status": "pending_approval",
            "step": req.step,
            "stepMeta": step_meta,
            "requiresApproval": True,
        }

    if step_meta.get("blocked") and not req.override:
        return {
            "status": "blocked",
            "step": req.step,
            "stepMeta": step_meta,
            "reason": "high risk step requires override",
        }

    replaced_from = derive_replaced_from(req.replaced_from, req.metadata, req.params)
    try:
        execution = execute_skill(req.step, req.params)
    except Exception as exc:
        record_skill_outcome_internal(
            req.step,
            result="failure",
            context_key=context_key,
            replaced_from=replaced_from,
            error=str(exc),
            record_execution=False,
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    outcome = record_skill_outcome_internal(
        req.step,
        result="success",
        context_key=context_key,
        replaced_from=replaced_from,
        latency=execution.get("latency") if isinstance(execution, dict) else None,
        record_execution=False,
    )
    await publish_backend_event(
        {
            "type": "task_update",
            "status": "done",
            "message": f"Skill step {req.step} executed.",
            "metadata": {
                "source": "skills_api",
                "skill": req.step,
                "actor": req.actor or "operator",
                "approved": req.approved,
                "override": req.override,
                "reason": req.reason,
                "step": step_meta,
                "execution": execution,
            },
        }
    )
    return {
        "status": "ok",
        "step": req.step,
        "stepMeta": step_meta,
        "execution": execution,
        "outcome": outcome,
    }


# ---------------------------------------------------------------------------
# Plan snapshot persistence
# ---------------------------------------------------------------------------

def _record_edit_trail_feedback(edit_trail: list, context_key: str | None = None) -> int:
    """Parse an edit_trail list and log dampened operator-feedback signals.

    Accepted edit_trail entry shapes (all optional fields are tolerated)::

        {"type": "swap", "from": "skill_a", "to": "skill_b"}
        {"type": "skip", "step": "skill_name"}
        {"type": "reorder", ...}  # currently a no-op; reserved for future use

    Returns the number of feedback events recorded.
    """
    recorded = 0
    for edit in (edit_trail or []):
        if not isinstance(edit, dict):
            continue
        metadata = edit.get("metadata") if isinstance(edit.get("metadata"), dict) else {}
        etype = str(edit.get("type") or edit.get("action") or metadata.get("type") or "").strip().lower()
        ctx = edit.get("context_key") or metadata.get("context_key") or context_key
        if etype == "swap":
            from_skill = str(
                edit.get("from")
                or edit.get("from_skill")
                or metadata.get("from")
                or metadata.get("from_skill")
                or metadata.get("previousSkill")
                or ""
            ).strip()
            to_skill = str(
                edit.get("to")
                or edit.get("to_skill")
                or metadata.get("to")
                or metadata.get("to_skill")
                or metadata.get("newSkill")
                or ""
            ).strip()
            if from_skill and to_skill:
                skill_learning_memory.log_operator_feedback(
                    "swap", from_skill=from_skill, to_skill=to_skill, context_key=ctx
                )
                recorded += 1
        elif etype == "skip":
            step = str(edit.get("step") or edit.get("skill") or metadata.get("step") or metadata.get("skill") or "").strip()
            if step:
                skill_learning_memory.log_operator_feedback("skip", skill_name=step, context_key=ctx)
                recorded += 1
    return recorded


class PlanSnapshotRequest(BaseModel):
    name: str
    task: str
    edited_plan: List[Dict[str, Any]]
    edit_trail: List[Dict[str, Any]] = Field(default_factory=list)
    actor: str | None = None
    request_id: str | None = None


@app.post("/skills/plan/save")
async def save_plan_snapshot_endpoint(req: PlanSnapshotRequest) -> Dict[str, Any]:
    if not str(req.name).strip():
        raise HTTPException(status_code=400, detail="Snapshot name is required")
    snapshot = save_plan_snapshot(
        name=req.name,
        task=req.task,
        editable_plan=req.edited_plan,
        edit_trail=req.edit_trail,
        actor=req.actor or "operator",
        request_id=req.request_id,
    )
    control_plane_metrics.increment("plan_snapshots_saved")
    feedback_events = _record_edit_trail_feedback(req.edit_trail)
    return {"status": "saved", "snapshot": snapshot, "feedbackRecorded": feedback_events}


@app.get("/skills/plan/snapshots")
async def list_plan_snapshots_endpoint() -> Dict[str, Any]:
    return {"snapshots": list_plan_snapshots()}


@app.get("/skills/plan/snapshots/latest")
async def load_latest_plan_snapshot_endpoint() -> Dict[str, Any]:
    snapshot = load_latest_plan_snapshot()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return {"snapshot": snapshot}


@app.get("/skills/plan/snapshots/{filename}")
async def load_plan_snapshot_endpoint(filename: str) -> Dict[str, Any]:
    snapshot = load_plan_snapshot(filename)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return {"snapshot": snapshot}


# ---------------------------------------------------------------------------
# Execute edited plan (non-skipped steps in operator order)
# ---------------------------------------------------------------------------

class EditedPlanExecutionRequest(BaseModel):
    task: str
    edited_plan: List[Dict[str, Any]]
    params: Dict[str, Any] = Field(default_factory=dict)
    actor: str | None = None
    request_id: str | None = None


@app.post("/skills/plan/execute-edited")
async def execute_edited_plan_endpoint(req: EditedPlanExecutionRequest) -> Dict[str, Any]:
    control_state = get_skill_control_state()
    control_plane_metrics.increment("edited_plan_executions")
    control_plane_metrics.increment("plan_execute_total")

    results = []
    skipped_steps = []
    blocked_steps = []
    failure_count = 0

    non_skipped = [step for step in (req.edited_plan or []) if not step.get("skipped")]

    # Record skip-feedback for every explicitly skipped step so that the
    # learning engine can down-weight those skills over time.
    for skipped_step in (req.edited_plan or []):
        if skipped_step.get("skipped"):
            sname = str(skipped_step.get("step") or "").strip()
            if sname:
                skill_learning_memory.log_operator_feedback(
                    "skip",
                    skill_name=sname,
                    context_key=skipped_step.get("context_key"),
                )

    for step_def in non_skipped:
        skill_name = str(step_def.get("step") or "").strip()
        if not skill_name:
            continue

        step_context_key = first_non_empty(
            step_def.get("context_key") if isinstance(step_def, dict) else None,
            req.params.get("context_key") if isinstance(req.params, dict) else None,
        )
        replaced_from = derive_replaced_from(step_def, req.params)

        # Check suppression
        suppression_reason = skill_suppression_reason(skill_name)
        if suppression_reason:
            blocked_steps.append({"step": skill_name, "reason": suppression_reason})
            control_plane_metrics.increment("plan_execute_blocked")
            continue

        skill = registry.get(skill_name)
        if skill is None:
            blocked_steps.append({"step": skill_name, "reason": "skill_not_found"})
            continue

        if skill.requires_approval:
            # Halt and surface pending_approval — operator must approve via execute-step
            return {
                "status": "pending_approval",
                "blockedOn": skill_name,
                "completed": results,
                "skipped": skipped_steps,
                "blocked": blocked_steps,
                "remaining": [s.get("step") for s in non_skipped[len(results) + len(blocked_steps):]],
                "controlState": control_state,
            }

        try:
            execution = execute_skill(skill_name, req.params)
            outcome = record_skill_outcome_internal(
                skill_name,
                result="success",
                context_key=step_context_key,
                replaced_from=replaced_from,
                latency=execution.get("latency") if isinstance(execution, dict) else None,
                record_execution=False,
            )
            results.append(
                {
                    "step": skill_name,
                    "status": "ok",
                    "execution": execution,
                    "outcome": outcome,
                }
            )
            step_action = step_def.get("recommended_action", "")
            if step_action == "auto_execute":
                control_plane_metrics.increment("plan_execute_auto")
            else:
                control_plane_metrics.increment("plan_execute_approved")
        except Exception as exc:
            failure_count += 1
            outcome = record_skill_outcome_internal(
                skill_name,
                result="failure",
                context_key=step_context_key,
                replaced_from=replaced_from,
                error=str(exc),
                record_execution=False,
            )
            results.append({"step": skill_name, "status": "failed", "error": str(exc)})
            results[-1]["outcome"] = outcome
            control_plane_metrics.increment("plan_execute_failed")
            if failure_count > 2:
                await publish_backend_event(
                    {
                        "type": "task_update",
                        "status": "aborted",
                        "message": f"Edited plan aborted: failure cascade at {skill_name}",
                        "metadata": {
                            "source": "skills_api",
                            "actor": req.actor or "operator",
                            "request_id": req.request_id,
                            "task": req.task,
                        },
                    }
                )
                return {
                    "status": "aborted_failure_cascade",
                    "reason": "failure cascade detected",
                    "failureCount": failure_count,
                    "completed": results,
                    "skipped": skipped_steps,
                    "blocked": blocked_steps,
                    "controlState": control_state,
                }

    # Collect skipped step names for the response
    skipped_steps = [step.get("step") for step in (req.edited_plan or []) if step.get("skipped")]

    await publish_backend_event(
        {
            "type": "task_update",
            "status": "done",
            "message": f"Edited plan executed: {len(results)} step(s) completed.",
            "metadata": {
                "source": "skills_api",
                "actor": req.actor or "operator",
                "request_id": req.request_id,
                "task": req.task,
                "completedSteps": [r["step"] for r in results],
                "skippedSteps": skipped_steps,
                "blockedSteps": blocked_steps,
            },
        }
    )

    return {
        "status": "done",
        "completed": results,
        "skipped": skipped_steps,
        "blocked": blocked_steps,
        "controlState": control_state,
    }


# ---------------------------------------------------------------------------
# Control-plane observability metrics
# ---------------------------------------------------------------------------


@app.get("/skills/feedback")
async def get_operator_feedback_endpoint() -> Dict[str, Any]:
    """Return a live view of accumulated operator-feedback signals.

    Each entry shows how many times an operator swapped a skill away from a
    plan, chose a skill as a replacement, or skipped it — together with the
    skill's current confidence score so you can see the learned influence.
    """
    summary = skill_learning_memory.get_feedback_summary()
    enriched: Dict[str, Any] = {}
    for key, entry in summary.items():
        skill_name = entry.get("skill", key)
        ctx = entry.get("context_key")
        enriched[key] = {
            **entry,
            "current_score": _score_skill(skill_name, context_key=ctx),
        }
    return {"feedback": enriched, "total_skills": len(enriched)}


@app.post("/skills/outcome")
async def record_skill_outcome_endpoint(req: SkillOutcomeRequest) -> Dict[str, Any]:
    return record_skill_outcome_internal(
        req.skill,
        result=req.result,
        context_key=req.context_key,
        replaced_from=req.replaced_from,
        latency=req.latency,
        error=req.error,
        record_execution=bool(req.record_execution),
        source=req.source,
    )


@app.post("/operator/override")
@app.post("/skills/override")
async def post_operator_override_endpoint(req: OperatorOverrideRequest) -> Dict[str, Any]:
    """Record an operator-override signal immediately, without a plan save.

    The feedback is stored in the skill learning memory and influences future
    plan scoring via the trust engine.  Returns the before/after score so the
    UI can surface the learning effect instantly.
    """
    otype = str(req.type or "").strip().lower()
    if otype not in ("swap", "skip", "reorder"):
        raise HTTPException(status_code=400, detail="type must be 'swap', 'skip', or 'reorder'")

    if otype == "reorder":
        skill_name = str(req.skill_name or req.from_skill or "").strip()
        direction = str(req.to_skill or "").strip().lower()  # to_skill reused as direction: 'up'|'down'
        if not skill_name:
            raise HTTPException(status_code=400, detail="skill_name is required for reorder")
        ctx = str(req.context_key or "").strip() or None
        prev_score = _score_skill(skill_name, context_key=ctx)
        skill_learning_memory.log_operator_feedback("reorder", skill_name=skill_name, from_skill=direction or "up", context_key=ctx)
        control_plane_metrics.increment("operator_overrides")
        return {
            "recorded": True,
            "type": "reorder",
            "direction": direction or "up",
            "source": req.source,
            "plan_id": req.plan_id,
            "step_id": req.step_id,
            "skill": {"name": skill_name, "previous_score": prev_score, "updated_score": _score_skill(skill_name, context_key=ctx)},
        }

    ctx = str(req.context_key or "").strip() or None

    if otype == "swap":
        from_skill = str(req.from_skill or req.original or "").strip()
        to_skill = str(req.to_skill or req.selected or "").strip()
        if not from_skill or not to_skill:
            raise HTTPException(status_code=400, detail="from_skill and to_skill are required for swap")
        prev_from = _score_skill(from_skill, context_key=ctx)
        prev_to = _score_skill(to_skill, context_key=ctx)
        skill_learning_memory.log_operator_feedback("swap", from_skill=from_skill, to_skill=to_skill, context_key=ctx)
        control_plane_metrics.increment("operator_overrides")
        return {
            "recorded": True,
            "type": "swap",
            "source": req.source,
            "plan_id": req.plan_id,
            "step_id": req.step_id,
            "reason": req.reason,
            "from": {"skill": from_skill, "previous_score": prev_from, "updated_score": _score_skill(from_skill, context_key=ctx)},
            "to": {"skill": to_skill, "previous_score": prev_to, "updated_score": _score_skill(to_skill, context_key=ctx)},
        }

    # type == "skip"
    skill_name = str(req.skill_name or req.from_skill or "").strip()
    if not skill_name:
        raise HTTPException(status_code=400, detail="skill_name is required for skip")
    prev_score = _score_skill(skill_name, context_key=ctx)
    skill_learning_memory.log_operator_feedback("skip", skill_name=skill_name, context_key=ctx)
    control_plane_metrics.increment("operator_overrides")
    return {
        "recorded": True,
        "type": "skip",
        "source": req.source,
        "skill": {"name": skill_name, "previous_score": prev_score, "updated_score": _score_skill(skill_name, context_key=ctx)},
    }



async def get_skill_trust_endpoint(skill: str, context_key: str | None = None) -> Dict[str, Any]:
    """Return the current operator trust score for a skill.

    Trust reflects both execution history and accumulated operator-feedback
    signals (swaps, skips).  Range is [0.0, 1.0].
    """
    if not str(skill or "").strip():
        raise HTTPException(status_code=400, detail="skill parameter is required")
    trust = compute_trust(skill, context_key=context_key)
    control_state = get_skill_control_state()
    global_mode, profile = resolve_runtime_policy(control_state)
    return {
        "skill": skill,
        "context_key": context_key,
        "trust": trust,
        "execution_mode": decide_execution_mode(
            {"step": skill, "risk": "unknown"},
            context_key=context_key,
            global_mode=global_mode,
            profile=profile,
        ),
    }


@app.get("/autonomy/config")
async def get_autonomy_config_endpoint() -> Dict[str, Any]:
    return {"config": get_runtime_config()}


@app.post("/autonomy/config")
async def update_autonomy_config_endpoint(req: AutonomyConfigUpdateRequest) -> Dict[str, Any]:
    updates = req.model_dump(exclude_none=True)
    config = update_runtime_config(updates)
    return {"status": "updated", "config": config}


@app.post("/autonomy/profile")
async def set_autonomy_profile_endpoint(profile: str) -> Dict[str, Any]:
    profile_name = str(profile or "").strip().lower()
    if profile_name not in PROFILES:
        raise HTTPException(status_code=400, detail=f"Unknown profile: {profile}")
    config = update_runtime_config({"profile": profile_name})
    return {"status": "updated", "profile": config.get("profile")}


@app.get("/autonomy/drift")
async def get_autonomy_drift_endpoint() -> Dict[str, Any]:
    config = get_runtime_config()
    metrics = control_plane_metrics.to_dict()
    return {
        "drift_detected": bool(config.get("drift_detected")),
        "forced_mode": config.get("forced_mode"),
        "drift_reason": config.get("drift_reason"),
        "drift_intensity": config.get("drift_intensity"),
        "drift_severity": config.get("drift_severity"),
        "metrics": metrics,
    }


@app.post("/autonomy/safe-mode/reset")
async def reset_autonomy_safe_mode_endpoint() -> Dict[str, Any]:
    config = update_runtime_config(
        {
            "forced_mode": None,
            "drift_detected": False,
            "drift_reason": None,
            "drift_intensity": 0.0,
            "drift_severity": "stable",
        }
    )
    return {
        "status": "updated",
        "forced_mode": config.get("forced_mode"),
        "drift_detected": config.get("drift_detected"),
        "drift_intensity": config.get("drift_intensity"),
        "drift_severity": config.get("drift_severity"),
    }


@app.post("/skills/plan/optimize")
async def optimize_plan_endpoint(req: PlanOptimizationRequest) -> Dict[str, Any]:
    control_state = get_skill_control_state()
    runtime_mode, runtime_profile = resolve_runtime_policy(control_state)
    requested_profile = str(req.profile or runtime_profile).strip().lower() or runtime_profile
    profile = requested_profile if requested_profile in PROFILES else runtime_profile
    optimization = optimize_skill_plan(
        req.plan,
        context_key=req.context_key,
        min_trust_threshold=req.min_trust_threshold,
        profile=profile,
        global_mode=runtime_mode,
        candidate_skills=build_candidate_skill_pool(registry.list()),
        context_match_min=req.context_match_min,
    )
    if optimization["pruned"]:
        control_plane_metrics.increment("pruned_step_count", by=len(optimization["pruned"]))
        control_plane_metrics.increment(
            "pruned_predicted_failures",
            by=sum(float(item.get("failure_probability", 0.0) or 0.0) for item in optimization["pruned"]),
        )
    if optimization["replaced"]:
        control_plane_metrics.increment("replaced_step_count", by=len(optimization["replaced"]))
    drift = maybe_trigger_safe_mode(
        optimization["planStability"],
        len(optimization["replaced"]),
        len(optimization["pruned"]),
    )
    return {
        "status": "ok",
        "plan": optimization["kept"],
        "kept": optimization["kept"],
        "avoided": optimization["avoided"],
        "replaced": optimization["replaced"],
        "pruned": optimization["pruned"],
        "profile": profile,
        "inputSteps": optimization["inputSteps"],
        "outputSteps": optimization["outputSteps"],
        "minTrustThreshold": optimization["threshold"],
        "planStability": optimization["planStability"],
        "drift": drift,
    }


@app.post("/skills/simulate")
async def simulate_skills_endpoint(req: SimulationRequest) -> Dict[str, Any]:
    control_plane_metrics.increment("simulation_runs")
    # Simulations are always isolated from production learning memory.
    with tempfile.TemporaryDirectory(prefix="andie-sim-") as temp_dir:
        sim_memory = MemoryStore(path=str(Path(temp_dir) / "skill_memory.json"))
        simulation = simulate_with_feedback(
            req.plan,
            failure_rate=req.failure_rate,
            seed=req.seed,
            apply_feedback=req.apply_feedback,
            context_key=req.context_key,
            memory_store=sim_memory,
            predictive=req.predictive,
        )
    return {
        "status": "ok",
        "simulation": simulation,
        "stepCount": len(req.plan or []),
        "isolated": True,
    }

@app.get("/metrics/control-plane")
async def control_plane_metrics_endpoint() -> Dict[str, Any]:
    return control_plane_metrics.to_dict()


@app.get("/trust/dashboard")
async def trust_dashboard_endpoint() -> Dict[str, Any]:
    metrics = control_plane_metrics.to_dict()
    counters = (metrics or {}).get("counters") or {}
    rates = (metrics or {}).get("rates") or {}
    config = get_runtime_config()

    total_outcomes = int(counters.get("outcome_events_total", 0) or 0)
    real_outcomes = int(counters.get("real_outcome_events_total", 0) or 0)
    synthetic_outcomes = max(total_outcomes - real_outcomes, 0)

    governance = evaluate_go_no_go(
        {
            "replacement_success_rate": float(rates.get("replacement_success_rate") or 0.0),
            "sample_size": total_outcomes,
            "real_sample_size": real_outcomes,
            "drift_rate": float(config.get("drift_intensity") or 0.0),
            "learning_density": float(rates.get("learning_signal_density") or 0.0),
        }
    )

    real_ratio = round(real_outcomes / float(total_outcomes), 4) if total_outcomes > 0 else None
    synthetic_ratio = round(synthetic_outcomes / float(total_outcomes), 4) if total_outcomes > 0 else None

    return {
        "confidence_tier": governance.get("confidence_tier"),
        "decision": governance.get("decision"),
        "real_vs_synthetic": {
            "real": real_outcomes,
            "synthetic": synthetic_outcomes,
            "total": total_outcomes,
            "real_ratio": real_ratio,
            "synthetic_ratio": synthetic_ratio,
        },
        "learning_velocity": {
            "real_signal_density": rates.get("real_signal_density"),
            "learning_signal_density": rates.get("learning_signal_density"),
        },
    }


@app.post("/agents/run")
async def run_agents_compat(req: AgentRunRequest):
    prompt = req.prompt or req.task
    if prompt is None and req.input is not None:
        prompt = req.input if isinstance(req.input, str) else str(req.input)
    prompt = str(prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt or task is required")

    resolved_agent_name = resolve_agent_name(req.agent) if req.agent else None
    requested_workflow_id = first_non_empty(
        req.metadata.get("workflowId"),
        req.metadata.get("workflow_id"),
    )
    task_record = add_task(
        {
            "type": "agent_run",
            "payload": {
                "prompt": prompt,
                "agent": req.agent,
                "context": req.context,
            },
            "priority": 4,
            "preferredNode": "thinkpad",
            "metadata": {
                "source": "agents_run_api",
                "agent": req.agent or "llm",
                "resolvedAgent": resolved_agent_name or req.agent or "llm",
                "workflowId": requested_workflow_id,
                **req.metadata,
            },
        }
    )
    await publish_agent_run_event(
        status="queued",
        task=task_record,
        agent_name=req.agent,
        resolved_agent_name=resolved_agent_name,
        prompt=prompt,
        workflow_id=requested_workflow_id,
    )

    running_task = claim_task(task_record["id"], "thinkpad", "agents_run_api") or get_task(task_record["id"])
    await publish_agent_run_event(
        status="running",
        task=running_task,
        agent_name=req.agent,
        resolved_agent_name=resolved_agent_name,
        prompt=prompt,
        workflow_id=requested_workflow_id,
    )

    _ws_broadcast("thinking", (resolved_agent_name or req.agent or "llm"))
    try:
        if req.agent:
            llm_input = {
                "prompt": prompt,
                "system": build_memory_augmented_system(req.system),
                "context": req.context,
                "metadata": {
                    "agent": req.agent,
                    "resolvedAgent": resolved_agent_name or req.agent,
                    **req.metadata,
                },
            }
            result = await invoke_agent_module(req.agent, llm_input)
        else:
            result = call_llm(
                prompt,
                system=build_memory_augmented_system(req.system),
                context=req.context,
            )

        result = _sanitize_repetitive_chat_output(result)

        if isinstance(result, dict):
            result.setdefault("agent", resolved_agent_name or req.agent or "llm")

        resolved_workflow_id = first_non_empty(requested_workflow_id, extract_workflow_id(result))
        complete(task_record["id"], result=result)
        completed_task = get_task(task_record["id"])
        await publish_agent_run_event(
            status="done",
            task=completed_task,
            agent_name=req.agent,
            resolved_agent_name=resolved_agent_name,
            prompt=prompt,
            workflow_id=resolved_workflow_id,
            result=result,
        )
        _ws_broadcast("speaking", (resolved_agent_name or req.agent or "llm"))
        _schedule_background_self_review(
            {
                "source": "agents_run",
                "task": prompt,
                "agent": resolved_agent_name or req.agent or "llm",
                "workflowId": resolved_workflow_id,
                "result": result,
            }
        )

        return {
            "status": "ok",
            "result": result,
            "task": completed_task,
            "taskId": completed_task.get("id") if completed_task else task_record["id"],
            "workflowId": resolved_workflow_id,
            "correlation": build_agent_run_correlation(
                completed_task,
                resolved_workflow_id,
                req.agent or "llm",
                resolved_agent_name or req.agent or "llm",
            ),
            "agentResolution": {
                "requested": req.agent or "llm",
                "resolved": resolved_agent_name or req.agent or "llm",
                "reason": "Alias mapping from semantic/domain agent name" if resolved_agent_name and req.agent and resolved_agent_name != req.agent else "Direct agent resolution",
            },
        }
    except HTTPException as exc:
        fail(task_record["id"], error=str(exc.detail))
        failed_task = get_task(task_record["id"])
        await publish_agent_run_event(
            status="failed",
            task=failed_task,
            agent_name=req.agent,
            resolved_agent_name=resolved_agent_name,
            prompt=prompt,
            workflow_id=requested_workflow_id,
            error=str(exc.detail),
        )
        _ws_broadcast("error", str(exc.detail)[:60])
        raise
    except Exception as exc:
        fail(task_record["id"], error=str(exc))
        failed_task = get_task(task_record["id"])
        await publish_agent_run_event(
            status="failed",
            task=failed_task,
            agent_name=req.agent,
            prompt=prompt,
            workflow_id=requested_workflow_id,
            error=str(exc),
        )
        _ws_broadcast("error", str(exc)[:60])
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/cryptonia/overseer/run")
async def run_cryptonia_overseer(req: CryptoniaOverseerRequest):
    task_text = str(req.task or "").strip()
    if not task_text:
        raise HTTPException(status_code=400, detail="task is required")

    data_capability = req.data_capability or "crypto_data"
    strategy_capability = req.strategy_capability or "crypto_strategy"

    active_capabilities = [data_capability, strategy_capability]
    if sorted(active_capabilities) != sorted(ALLOWED_ACTIVE_CAPABILITIES):
        raise HTTPException(
            status_code=400,
            detail=(
                "Overseer enforces exactly two active capabilities: "
                f"{', '.join(ALLOWED_ACTIVE_CAPABILITIES)}"
            ),
        )

    requested_data_agent = req.data_agent
    requested_strategy_agent = req.strategy_agent
    resolved_data_agent = resolve_capability(data_capability)
    resolved_strategy_agent = resolve_capability(strategy_capability)

    data_payload = {
        "prompt": task_text,
        "system": "You are a factual crypto market data agent. Return only normalized market data.",
        "context": req.context,
        "metadata": {
            "agent": requested_data_agent,
            "resolvedAgent": resolved_data_agent,
            "capability": data_capability,
            "constraints": req.constraints,
            **req.metadata,
        },
    }

    try:
        data_result = await run_capability(data_capability, data_payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Data agent failed: {exc}")

    if not isinstance(data_result, dict):
        raise HTTPException(status_code=500, detail="Data agent returned non-object payload")

    immutable_market_data = json.loads(json.dumps(data_result))

    strategy_payload = {
        "prompt": task_text,
        "system": "You are a strategy-only crypto agent. Use provided market data only and return confidence/risk.",
        "context": req.context,
        "metadata": {
            "agent": requested_strategy_agent,
            "resolvedAgent": resolved_strategy_agent,
            "capability": strategy_capability,
            "constraints": req.constraints,
            "market_data": immutable_market_data,
            **req.metadata,
        },
    }

    try:
        strategy_result = await run_capability(strategy_capability, strategy_payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Strategy agent failed: {exc}")

    if not isinstance(strategy_result, dict):
        raise HTTPException(status_code=500, detail="Strategy agent returned non-object payload")

    confidence = float(strategy_result.get("confidence", 0.0) or 0.0)
    risk_score = float(strategy_result.get("risk_score", 1.0) or 1.0)
    data_quality = float(strategy_result.get("data_quality", 0.0) or 0.0)
    data_coverage = float((strategy_result.get("signals") or {}).get("data_coverage", 0.0) or 0.0)
    volatility = float((strategy_result.get("signals") or {}).get("volatility_score", 0.0) or 0.0)

    confidence_threshold = float(req.constraints.get("confidence_threshold", 0.75))
    max_risk_score = float(req.constraints.get("max_risk_score", 0.45))
    min_data_quality = float(req.constraints.get("min_data_quality", 0.60))
    time_horizon = req.constraints.get("timeframe") or req.metadata.get("time_horizon")

    decision_eval = evaluate_overseer_decision(
        confidence=confidence,
        risk_score=risk_score,
        data_quality=data_quality,
        data_coverage=data_coverage,
        volatility=volatility,
        profile=req.profile,
        time_horizon=time_horizon,
        confidence_threshold=confidence_threshold,
        max_risk_score=max_risk_score,
        min_data_quality=min_data_quality,
    )
    decision = decision_eval["decision"]
    approve = decision == "approve"

    asset = str(req.metadata.get("symbol") or req.metadata.get("asset") or "BTC").upper()
    data_points = immutable_market_data.get("series") if isinstance(immutable_market_data.get("series"), list) else []

    normalized_data_output = {
        "type": "market_data",
        "asset": asset,
        "data_points": data_points,
        "source": "coinmarketcap",
        "quality_score": round(data_quality, 4),
    }

    normalized_strategy_output = {
        "type": "strategy",
        "action": strategy_result.get("action", "hold"),
        "confidence": round(confidence, 4),
        "risk_score": round(risk_score, 4),
        "time_horizon": str(time_horizon or "unknown"),
        "reasoning": strategy_result.get("insight") or strategy_result.get("reasoning") or "No reasoning provided.",
    }

    andie_decision_output = {
        "decision": decision_eval["decision"],
        "execution": decision_eval["execution"],
        "profile": decision_eval["profile"],
        "final_confidence": decision_eval["final_confidence"],
        "composite_score": decision_eval["composite_score"],
        "risk_adjusted": decision_eval["risk_adjusted"],
        "weights": decision_eval["weights"],
        "signals": decision_eval["signals"],
        "risk_guardrail_triggered": decision_eval["risk_guardrail_triggered"],
        "notes": decision_eval["notes"],
        "reason_trace": decision_eval["reason_trace"],
    }

    await publish_backend_event(
        {
            "type": "task_update",
            "status": "done",
            "message": "Cryptonia overseer completed dual-agent evaluation.",
            "metadata": {
                "source": "cryptonia_overseer",
                "task": task_text[:140],
                "activeCapabilities": active_capabilities,
                "requestedDataAgent": requested_data_agent,
                "resolvedDataAgent": resolved_data_agent,
                "requestedStrategyAgent": requested_strategy_agent,
                "resolvedStrategyAgent": resolved_strategy_agent,
                "decision": decision,
                "profile": decision_eval["profile"],
                "confidence": confidence,
                "riskScore": risk_score,
                "dataQuality": data_quality,
                "compositeScore": decision_eval["composite_score"],
                "riskAdjusted": decision_eval["risk_adjusted"],
            },
        }
    )

    return {
        "status": "ok",
        "mode": "dual_agent_overseer",
        "delegation": {
            "task": task_text,
            "profile": req.profile or "balanced",
            "data_capability": data_capability,
            "strategy_capability": strategy_capability,
            "data_agent": requested_data_agent,
            "strategy_agent": requested_strategy_agent,
            "constraints": req.constraints,
        },
        "activeCapabilities": active_capabilities,
        "agentResolution": {
            "data": {
                "requested": requested_data_agent,
                "resolved": resolved_data_agent,
                "capability": data_capability,
            },
            "strategy": {
                "requested": requested_strategy_agent,
                "resolved": resolved_strategy_agent,
                "capability": strategy_capability,
            },
        },
        "data": {
            "agent": resolved_data_agent,
            "result": immutable_market_data,
            "normalized": normalized_data_output,
        },
        "strategy": {
            "agent": resolved_strategy_agent,
            "result": strategy_result,
            "normalized": normalized_strategy_output,
        },
        "evaluation": {
            "decision": decision,
            "approve": approve,
            "profile": decision_eval["profile"],
            "confidence": confidence,
            "risk_score": risk_score,
            "data_quality": data_quality,
            "data_coverage": data_coverage,
            "volatility": volatility,
            "composite_score": decision_eval["composite_score"],
            "risk_adjusted": decision_eval["risk_adjusted"],
            "weights": decision_eval["weights"],
            "reason_trace": decision_eval["reason_trace"],
            "thresholds": {
                "confidence_threshold": confidence_threshold,
                "max_risk_score": max_risk_score,
                "min_data_quality": min_data_quality,
            },
        },
        "andieDecision": andie_decision_output,
    }


@app.get("/security/logs")
def security_logs_compat(limit: int = 50):
    return read_security_logs(limit)


@app.post("/cryptonia/capital/orchestrate")
async def orchestrate_capital_cycle(req: CapitalOrchestrationRequest):
    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()

    _ws_broadcast("thinking", "capital_orchestration")
    await publish_backend_event(
        {
            "type": "task_update",
            "status": "running",
            "level": "info",
            "target": "trading",
            "message": "Capital orchestration cycle started.",
            "action": "capital_orchestration_start",
            "metadata": {
                "source": "cryptonia_capital_orchestrator",
                "symbol": payload.get("symbol", "BTC"),
                "timeframe": payload.get("timeframe", "1h"),
            },
        }
    )

    try:
        result = await asyncio.to_thread(run_capital_orchestration, payload)
    except Exception as exc:
        _ws_broadcast("error", str(exc)[:60])
        await publish_backend_event(
            {
                "type": "task_update",
                "status": "failed",
                "level": "error",
                "target": "trading",
                "message": "Capital orchestration cycle failed.",
                "action": "capital_orchestration_failed",
                "metadata": {
                    "source": "cryptonia_capital_orchestrator",
                    "error": str(exc),
                },
            }
        )
        raise HTTPException(status_code=500, detail=str(exc))

    _ws_broadcast("improved", "capital_orchestration")
    await publish_backend_event(
        {
            "type": "task_update",
            "status": "done",
            "level": "info",
            "target": "trading",
            "message": "Capital orchestration cycle completed.",
            "action": "capital_orchestration_done",
            "metadata": {
                "source": "cryptonia_capital_orchestrator",
                "decision": ((result.get("cycle") or {}).get("execution") or {}).get("decision"),
                "allocatedRiskUsd": ((result.get("cycle") or {}).get("execution") or {}).get("allocated_risk_usd"),
                "activeCapital": ((result.get("cycle") or {}).get("capital") or {}).get("active_capital"),
                "reserveCapital": ((result.get("cycle") or {}).get("capital") or {}).get("reserve_capital"),
            },
        }
    )
    return result


@app.get("/cryptonia/capital/state")
def cryptonia_capital_state_api():
    return {
        "status": "ok",
        "state": get_capital_state(),
    }


@app.get("/cryptonia/capital/history")
def cryptonia_capital_history_api(limit: int = 20):
    items = list_cycle_history(limit=limit)
    return {
        "status": "ok",
        "items": items,
        "count": len(items),
    }


@app.post("/memory/query")
def query_memory_compat(req: QueryRequest):
    try:
        result = memory_service.query_memory(req.query, top_k=req.top_k)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"results": normalize_memory_results(result.get("results") or [])}


@app.post("/memory/save-session")
async def save_memory_session(req: SaveSessionRequest) -> Dict[str, Any]:
    session_id = str(req.session_id or "").strip()
    transcript = str(req.transcript or "").strip()

    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    if not transcript:
        raise HTTPException(status_code=400, detail="transcript is required")

    prompt = f"""
Analyze this session transcript and extract:
1. A 2-sentence episode summary of what happened
2. Up to 5 semantic facts learned (key: value format)
3. Up to 3 procedural improvements (skill, method, confidence 0-1)

Transcript:
{transcript[:20000]}

Respond ONLY in this JSON format:
{{
  "episode": "summary here",
  "tags": ["tag1", "tag2"],
  "semantic": {{"key": "value"}},
  "procedural": [{{"skill": "x", "method": "y", "confidence": 0.8}}]
}}
""".strip()

    model_name = os.getenv("ANDIE_MEMORY_MODEL", os.getenv("ANDIE_CHAT_MODEL", "gpt-4o"))
    try:
        raw = await asyncio.to_thread(
            call_llm,
            prompt,
            "You extract long-term memory from transcripts. Return valid JSON only.",
            None,
            model_name,
        )
        data = extract_memory_json_payload(raw if isinstance(raw, str) else str(raw))
    except Exception:
        # Fallback keeps persistence working even when the configured LLM model is unavailable.
        first_lines = [line.strip() for line in transcript.splitlines() if line.strip()][:2]
        fallback_episode = " ".join(first_lines)[:280] or "Session captured."
        data = {
            "episode": fallback_episode,
            "tags": ["session"],
            "semantic": {},
            "procedural": [],
        }

    episode = str(data.get("episode") or "").strip() or "Session summary unavailable."
    tags = data.get("tags") if isinstance(data.get("tags"), list) else []
    semantic = data.get("semantic") if isinstance(data.get("semantic"), dict) else {}
    procedural = data.get("procedural") if isinstance(data.get("procedural"), list) else []

    save_episode(session_id, episode, [str(tag) for tag in tags][:12])

    for key, value in semantic.items():
        semantic_key = str(key).strip()
        semantic_value = str(value).strip()
        if semantic_key and semantic_value:
            save_semantic(semantic_key, semantic_value)

    saved_procedural = 0
    for item in procedural[:10]:
        if not isinstance(item, dict):
            continue
        skill = str(item.get("skill") or "").strip()
        method = str(item.get("method") or "").strip()
        if not skill or not method:
            continue
        try:
            confidence = float(item.get("confidence", 0.5))
        except Exception:
            confidence = 0.5
        save_procedural(skill, method, confidence)
        saved_procedural += 1

    return {
        "status": "saved",
        "session_id": session_id,
        "episode_saved": True,
        "semantic_saved": len(semantic),
        "procedural_saved": saved_procedural,
    }


@app.get("/memory/context")
def get_memory_context() -> Dict[str, Any]:
    return {"context": build_memory_context()}


@app.get("/memory/snapshot")
def get_memory_snapshot(limit: int = 8) -> Dict[str, Any]:
    bounded = max(1, min(limit, 50))
    snapshot = memory_snapshot(bounded)
    return {
        "status": "ok",
        "snapshot": snapshot,
    }


@app.post("/frontend/issues")
async def enqueue_frontend_issue(req: FrontendIssueRequest):
    task = add_task(
        {
            "type": "frontend_issue",
            "payload": {
                "issue": req.issue,
                "context": req.context,
                "files": req.files,
            },
            "priority": req.priority,
            "preferredNode": req.preferredNode or "thinkpad",
            "metadata": {
                "source": "frontend_issue_api",
                "agent": "frontend_ui_agent",
                **req.metadata,
            },
        }
    )
    await publish_backend_event(
        {
            "type": "task_update",
            "status": "queued",
            "task": task,
            "metadata": {
                "source": "frontend_issue_api",
                "agent": "frontend_ui_agent",
            },
        }
    )
    return {"status": "queued", "task": task}


@app.get("/settings/config")
def get_settings_config():
    try:
        payload = read_settings_payload()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return {
        "status": "ok",
        **payload,
    }


@app.put("/settings/config")
async def update_settings_config(req: SettingsConfigRequest):
    if not isinstance(req.config, dict):
        raise HTTPException(status_code=400, detail="config payload must be an object")

    payload = {
        "schemaVersion": req.schemaVersion or SETTINGS_SCHEMA_VERSION,
        "savedAt": datetime.now(timezone.utc).isoformat(),
        "updatedBy": req.updatedBy or "operator-ui",
        "config": req.config,
    }

    try:
        persisted = write_settings_payload(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    await publish_backend_event(
        {
            "type": "alert",
            "level": "info",
            "target": "settings",
            "message": "Control-plane settings updated.",
            "action": "settings_update",
            "metadata": {
                "updatedBy": persisted["updatedBy"],
                "schemaVersion": persisted["schemaVersion"],
            },
        }
    )

    return {
        "status": "saved",
        **persisted,
    }

# --- Health endpoint ---
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics_api():
    return system_metrics("brain")


@app.post("/workflow/run")
async def run_workflow(req: WorkflowRequest):
    try:
        result = await workflow_engine.run_workflow_stream(
            task=req.task,
            workflow_id=req.memory.get("workflowId") or f"workflow-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
            steps=req.steps or None,
            context_text=req.context,
            memory=req.memory,
            allow_recovery=req.allowRecovery,
        )
        await publish_backend_event(
            {
                "type": "alert",
                "level": "info",
                "target": "workflow",
                "message": f"Workflow completed with status {result.get('evaluation', {}).get('status', 'unknown')}.",
                "action": "workflow_run",
                "metadata": {
                    "task": req.task[:140],
                    "steps": result.get("workflow"),
                    "status": result.get("evaluation", {}).get("status"),
                },
            }
        )
        _schedule_background_self_review(
            {
                "source": "workflow_run",
                "task": req.task,
                "context": req.context,
                "steps": req.steps,
                "result": result,
            }
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

# --- System status endpoint ---
# --- System status endpoint ---
@app.get("/system/status")
def system_status():
    # Example: ping memory API, check orchestrator, etc.
    try:
        mem_status = requests.get("http://localhost:8000/health").json()
    except Exception:
        mem_status = {"status": "unreachable"}
    stream_snapshot = task_stream_snapshot(8)
    nodes = check_node_health()
    return {
        "orchestrator": "ready",
        "memory": mem_status.get("status", "unknown"),
        "agents": "ready",
        "workflowEngine": "ready",
        "autonomy": get_autonomy_status(),
        "nodes": nodes,
        "schedulerQueue": stream_snapshot["queue"],
        "recentTasks": stream_snapshot["tasks"],
        "taskStreamUpdatedAt": stream_snapshot["updatedAt"],
    }


@app.get("/nodes/status")
def nodes_status_api():
    return {"nodes": check_node_health()}


@app.post("/autonomy/start")
def start_autonomy_api():
    return start_autonomy()


@app.post("/autonomy/stop")
def stop_autonomy_api():
    return stop_autonomy()


# ─────────────────────────────────────────────────────────────────────────────
# AUTONOMOUS BUILD ENDPOINT
# Accepts a plain-language task. ANDIE generates a ToolChain plan via LLM,
# then executes it autonomously — bash → write → read → fix — without a human
# in the loop. Returns the full step trace plus final output.
# ─────────────────────────────────────────────────────────────────────────────

class AutonomousBuildRequest(BaseModel):
    brief: str
    max_iterations: int = 5


@app.post("/build/autonomous")
async def autonomous_build(req: AutonomousBuildRequest):
    from andie.builder.autonomous_builder import autonomous_build as run_autonomous_build

    brief = (req.brief or "").strip()
    if not brief:
        raise HTTPException(status_code=400, detail="brief is required")

    max_iterations = max(1, min(int(req.max_iterations or 5), 10))

    async def _build_event_cb(payload: Dict[str, Any]) -> None:
        phase = str(payload.get("phase") or "build_step")
        detail = str(payload.get("job_id") or "autonomous build")

        if phase in {"job_start", "plan_ready"}:
            _ws_broadcast("thinking", detail)
        elif phase in {"iteration_start", "file_written", "execute_start", "execute_result", "diagnosis"}:
            _ws_broadcast("improving", detail)

        await publish_backend_event(
            {
                "type": "build_step",
                "status": phase,
                "level": "info",
                "target": "builder",
                "message": f"build:{phase}",
                "action": "build_autonomous",
                "metadata": payload,
            }
        )

    try:
        result = await run_autonomous_build(
            brief=brief,
            max_iterations=max_iterations,
            event_cb=_build_event_cb,
        )
    except Exception as exc:
        _ws_broadcast("error", str(exc)[:60])
        await publish_backend_event(
            {
                "type": "build_step",
                "status": "error",
                "level": "error",
                "target": "builder",
                "message": "build:error",
                "action": "build_autonomous",
                "metadata": {
                    "brief": brief[:240],
                    "error": str(exc),
                },
            }
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    result_payload = result.as_dict()
    success = result_payload.get("status") == "success"
    _ws_broadcast("improved" if success else "error", str(result_payload.get("job_id") or "build"))
    await publish_backend_event(
        {
            "type": "build_step",
            "status": result_payload.get("status", "unknown"),
            "level": "info" if success else "error",
            "target": "builder",
            "message": "build:complete",
            "action": "build_autonomous",
            "metadata": {
                "brief": brief[:240],
                "result": result_payload,
            },
        }
    )
    return result_payload




@app.post("/autonomy/disable")
def disable_autonomy_api(reason: str = "operator_request"):
    """Hard kill-switch: blocks all decision execution (loop keeps running, decisions are vetoed)."""
    return disable_autonomy(reason=reason)


@app.post("/autonomy/enable")
def enable_autonomy_api():
    """Re-enable after a hard kill or auto-disable without restarting the loop."""
    return enable_autonomy()


@app.get("/autonomy/guardrails")
def autonomy_guardrails_api():
    """Live guardrail state: kill-switch status, rate, oscillation, recent decisions."""
    return guardrail_status()


@app.get("/autonomy/status")
def autonomy_status_api():
    return get_autonomy_status()


@app.post("/self-review")
async def self_review_api(req: SelfReviewRequest):
    payload = dict(req.task_output or {})
    if req.output and "output" not in payload:
        payload["output"] = req.output
    if req.source and "source" not in payload:
        payload["source"] = req.source

    result = await run_self_review(payload)
    return {
        "status": "ok",
        **result,
    }


@app.post("/improve")
async def improve_api():
    result = await run_improve()
    return {
        "status": "ok",
        **result,
    }


@app.get("/self-build/skills")
def self_build_skills_api():
    return {
        "status": "ok",
        "registry": read_skill_registry(),
    }


@app.get("/self-build/growth")
def self_build_growth_api(limit: int = 50):
    bounded = max(1, min(limit, 500))
    log = read_growth_log()
    return {
        "status": "ok",
        "items": log[-bounded:],
        "count": min(len(log), bounded),
        "total": len(log),
    }


@app.post("/self-build/snapshot")
def self_build_snapshot_api(note: str = "manual_snapshot"):
    entry = append_growth_entry(
        {
            "type": "snapshot",
            "skills_added": [],
            "skills_improved": [],
            "failed_attempts": [],
            "andie_note": note,
        }
    )
    return {
        "status": "ok",
        "entry": entry,
    }


def recent_autonomy_events(limit: int = 50) -> List[Dict[str, Any]]:
    bounded_limit = max(1, min(limit, 200))
    # Pull a wider slice first because non-autonomy events may be interleaved.
    source_pool = recent_stream_events(limit=max(50, min(200, bounded_limit * 5)))
    autonomy_items = [
        item
        for item in source_pool
        if str(item.get("type") or "").startswith("autonomy_")
    ]
    return autonomy_items[-bounded_limit:]


def _compact_autonomy_event(item: Dict[str, Any]) -> Dict[str, Any]:
    """Strip the heavy nested node/metrics payload for compact log responses."""
    out = {k: v for k, v in item.items() if k not in ("state", "evaluation")}
    state = item.get("state")
    if isinstance(state, dict):
        nodes_raw = state.get("nodes") or {}
        out["state"] = {
            "cpu": state.get("cpu"),
            "memory": state.get("memory"),
            "queue": state.get("queue"),
            "running": state.get("running"),
            "failed": state.get("failed"),
            "nodes": {
                node_id: {
                    "status": node.get("status"),
                    "available": node.get("available"),
                    "overloaded": node.get("overloaded"),
                    "score": node.get("score"),
                }
                for node_id, node in nodes_raw.items()
            },
        }
    return out


@app.get("/autonomy/logs")
def autonomy_logs_api(tail: int = 20, compact: bool = True):
    items = recent_autonomy_events(limit=tail)
    if compact:
        items = [_compact_autonomy_event(i) for i in items]
    return {
        "items": items,
        "count": len(items),
    }


@app.get("/autonomy/decision/latest")
def autonomy_latest_decision_api():
    items = recent_autonomy_events(limit=200)
    for item in reversed(items):
        if item.get("type") != "autonomy_cycle_complete":
            continue
        if item.get("decision") is None and item.get("evaluation") is None:
            continue
        return {
            "item": item,
            "found": True,
        }

    return {
        "item": None,
        "found": False,
        "detail": "No autonomy decision found in recent event history.",
    }


@app.get("/autonomy/decision/history")
def autonomy_decision_history_api(limit: int = 50):
    bounded = max(1, min(limit, 200))
    items = recent_autonomy_events(limit=200)
    decisions = [
        item
        for item in items
        if item.get("type") == "autonomy_cycle_complete"
        and (item.get("decision") is not None or item.get("evaluation") is not None)
    ]
    page = decisions[-bounded:]
    return {
        "items": page,
        "count": len(page),
    }


@app.get("/autonomy/rules")
def autonomy_rules_api():
    engine = get_trigger_engine()
    return {
        "status": "ok",
        "rules": engine.rules,
        "triggerHistory": engine.history[-50:],
    }


@app.post("/autonomy/rules/reload")
def reload_autonomy_rules_api():
    engine = get_trigger_engine()
    rules = engine.reload_rules()
    return {
        "status": "reloaded",
        "count": len(rules),
        "rules": rules,
    }


@app.put("/autonomy/rules")
def update_autonomy_rules_api(req: AutonomyRulesUpdateRequest):
    cleaned: List[Dict[str, Any]] = []
    seen_rule_ids = set()
    validation_errors: List[str] = []

    for raw_rule in req.rules:
        if not isinstance(raw_rule, dict):
            raise HTTPException(status_code=400, detail="Each rule must be an object")
        rule_id = str(raw_rule.get("id") or "").strip()
        if not rule_id:
            raise HTTPException(status_code=400, detail="Each rule must include a non-empty id")
        if rule_id in seen_rule_ids:
            raise HTTPException(status_code=400, detail=f"Duplicate rule id: {rule_id}")
        seen_rule_ids.add(rule_id)
        cleaned.append(raw_rule)

    for raw_rule in cleaned:
        result = validate_autonomy_rule(raw_rule, cleaned)
        validation_errors.extend(result["errors"])

    if validation_errors:
        raise HTTPException(status_code=400, detail="; ".join(validation_errors))

    persist_autonomy_rules(cleaned)
    engine = get_trigger_engine()
    rules = engine.reload_rules()
    return {
        "status": "updated",
        "updatedBy": req.updatedBy,
        "count": len(rules),
        "rules": rules,
    }


@app.post("/autonomy/rules/validate")
def validate_autonomy_rule_api(req: AutonomyRuleValidationRequest):
    return {
        "status": "validated",
        **validate_autonomy_rule(req.rule if isinstance(req.rule, dict) else {}, req.existingRules),
    }


@app.post("/autonomy/rules/simulate")
def simulate_autonomy_rule_api(req: AutonomyRuleSimulationRequest):
    rule = req.rule if isinstance(req.rule, dict) else {}
    event = req.event if isinstance(req.event, dict) else {}
    rule_id = str(rule.get("id") or "unsaved_rule")

    when = rule.get("when") if isinstance(rule.get("when"), dict) else {}
    event_type_required = when.get("eventType")
    incoming_event_type = event.get("type")
    event_type_match = True if not event_type_required else incoming_event_type == event_type_required

    conditions = when.get("conditions") if isinstance(when.get("conditions"), list) else []
    condition_results: List[Dict[str, Any]] = []

    for index, condition in enumerate(conditions):
        if not isinstance(condition, dict):
            condition_results.append(
                {
                    "index": index,
                    "field": None,
                    "operator": None,
                    "expected": None,
                    "actual": None,
                    "passed": False,
                    "error": "Condition must be an object",
                }
            )
            continue
        condition_results.append(evaluate_autonomy_condition(condition, event, index))

    conditions_passed = all(item.get("passed") for item in condition_results) if condition_results else True
    rule_match = bool(event_type_match and conditions_passed)

    return {
        "status": "simulated",
        "ruleId": rule_id,
        "eventTypeRequired": event_type_required,
        "eventTypeReceived": incoming_event_type,
        "eventTypeMatched": event_type_match,
        "conditionResults": condition_results,
        "conditionsPassed": conditions_passed,
        "ruleMatched": rule_match,
    }


@app.post("/task")
def create_task(req: TaskRequest):
    payload = req.model_dump(exclude_none=True) if hasattr(req, "model_dump") else req.dict(exclude_none=True)
    return add_task(payload)


@app.get("/tasks")
def list_tasks(limit: int = 25):
    snapshot = task_stream_snapshot(limit)
    return {
        "tasks": snapshot["tasks"],
        "metrics": snapshot["queue"],
        "updatedAt": snapshot["updatedAt"],
    }


@app.post("/tasks/clear")
async def clear_tasks_api():
    result = clear_tasks()
    snapshot = task_stream_snapshot()
    await publish_backend_event(
        {
            "type": "task_queue_cleared",
            "status": "cleared",
            "result": result,
            "queue": snapshot["queue"],
            "metadata": {"source": "operator"},
        }
    )
    return {
        "status": "cleared",
        "result": result,
        "queue": snapshot["queue"],
    }


@app.get("/tasks/stream")
async def stream_tasks(request: Request, limit: int = 8):
    queue = await subscribe_stream()

    async def event_generator():
        previous_payload = None
        try:
            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    pass

                payload = task_stream_snapshot(limit)
                serialized = json.dumps(payload)
                if serialized != previous_payload:
                    previous_payload = serialized
                    yield f"event: task-update\ndata: {serialized}\n\n"
        finally:
            await unsubscribe_stream(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/tasks/{task_id}")
def get_task_by_id(task_id: int):
    task = get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.post("/events/publish")
async def publish_event(req: EventPublishRequest):
    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    event = await publish_backend_event(payload)
    return {"status": "published", "updatedAt": event["updatedAt"]}


@app.get("/events/recent")
def recent_events_api(limit: int = 50):
    return {
        "items": recent_stream_events(limit=max(1, min(limit, 200))),
        "count": len(recent_stream_events(limit=max(1, min(limit, 200)))),
    }


@app.get("/trading/approvals")
def list_trading_approvals(include_resolved: bool = False):
    return {
        "items": list_trade_approvals(include_resolved=include_resolved),
        "count": len(list_trade_approvals(include_resolved=include_resolved)),
    }


@app.get("/trading/approvals/{approval_id}")
def get_trading_approval(approval_id: str):
    approval = get_trade_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    return approval


@app.post("/trading/approvals/{approval_id}/reject")
async def reject_trading_approval(approval_id: str, req: TradeApprovalDecisionRequest):
    approval = get_trade_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.get("status") != "pending":
        raise HTTPException(status_code=409, detail="Approval is already resolved")

    resolved = resolve_trade_approval(
        approval_id,
        status="rejected",
        actor=req.actor,
        reason=req.reason or "operator_rejected",
    )
    await publish_backend_event(
        {
            "type": "APPROVAL_REJECTED",
            "status": "rejected",
            "target": "trading",
            "message": f"Trading approval {approval_id} rejected.",
            "reason": req.reason or "operator_rejected",
            "metadata": {
                "approvalId": approval_id,
                "trade": approval.get("trade"),
                "actor": req.actor,
                **req.metadata,
            },
        }
    )
    return {"status": "rejected", "approval": resolved}


@app.post("/trading/approvals/{approval_id}/approve")
async def approve_trading_approval(approval_id: str, req: TradeApprovalDecisionRequest):
    approval = get_trade_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval.get("status") != "pending":
        raise HTTPException(status_code=409, detail="Approval is already resolved")

    trade = approval.get("trade") if isinstance(approval.get("trade"), dict) else {}
    if not trade.get("symbol") or not trade.get("action"):
        raise HTTPException(status_code=400, detail="Approval is missing trade symbol/action")

    execution_metadata = dict(approval.get("metadata") or {})
    execution_metadata.update(req.metadata or {})

    result = await execute_approved_trade(
        approval_id=approval_id,
        trade=trade,
        metadata=execution_metadata,
        actor=req.actor,
    )

    mapped_status = {
        "ok": "approved",
        "blocked": "blocked",
        "failed": "failed",
    }.get(result.get("status") or "", "approved")
    resolved = resolve_trade_approval(
        approval_id,
        status=mapped_status,
        actor=req.actor,
        reason=req.reason,
    )

    await publish_backend_event(
        {
            "type": "APPROVAL_DECIDED",
            "status": mapped_status,
            "target": "trading",
            "message": f"Trading approval {approval_id} approved.",
            "reason": req.reason,
            "result": result if isinstance(result, dict) else None,
            "metadata": {
                "approvalId": approval_id,
                "trade": trade,
                "actor": req.actor,
                **req.metadata,
            },
        }
    )
    return {"status": mapped_status, "approval": resolved, "result": result}


@app.post("/task/{task_id}/retry")
async def retry_task_api(task_id: int):
    current = get_task(task_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Task not found")
    updated = request_manual_retry(task_id)
    if updated is None:
        raise HTTPException(status_code=409, detail="Task must be failed or cancelled before it can be retried")
    await publish_backend_event(
        {
            "type": "task_update",
            "status": "retrying",
            "task": updated,
            "retry": updated.get("retry"),
            "failureClass": updated.get("failureClass"),
            "retryDisposition": updated.get("retryDisposition"),
            "metadata": {"source": "operator"},
        }
    )
    await publish_backend_event(
        {
            "type": "alert",
            "level": "warning",
            "target": "queue",
            "message": f"Operator retried task {task_id}.",
            "action": "operator_retry",
            "task": updated,
            "metadata": {"source": "operator"},
        }
    )
    return {"status": "retry triggered", "task": updated}


@app.post("/task/{task_id}/cancel")
async def cancel_task_api(task_id: int):
    current = get_task(task_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Task not found")
    updated = cancel_task(task_id)
    if updated is None:
        raise HTTPException(status_code=409, detail="Only pending or failed tasks can be cancelled safely")
    await publish_backend_event(
        {
            "type": "task_update",
            "status": "cancelled",
            "task": updated,
            "retry": updated.get("retry"),
            "failureClass": updated.get("failureClass"),
            "retryDisposition": updated.get("retryDisposition"),
            "metadata": {"source": "operator"},
        }
    )
    await publish_backend_event(
        {
            "type": "alert",
            "level": "warning",
            "target": "queue",
            "message": f"Operator cancelled task {task_id}.",
            "action": "operator_cancel",
            "task": updated,
            "metadata": {"source": "operator"},
        }
    )
    return {"status": "cancelled", "task": updated}


@app.post("/system/restart")
async def restart_backend():
    command = restart_command()
    subprocess.Popen(["bash", "-lc", command], start_new_session=True)
    await publish_backend_event(
        {
            "type": "alert",
            "level": "warning",
            "target": "backend",
            "message": "Operator requested backend restart.",
            "action": "system_restart",
            "metadata": {"source": "operator"},
        }
    )
    return {"status": "restarting", "command": "uvicorn interfaces.api.main:app --reload --host 0.0.0.0 --port 8000"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    await websocket_event_bus.subscribe(websocket)
    await websocket.send_json(
        enrich_event_payload(
            {
                "type": "system_snapshot",
                "status": "connected",
                "metadata": {"transport": "websocket"},
            }
        )
    )

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await websocket_event_bus.unsubscribe(websocket)
    except Exception:
        await websocket_event_bus.unsubscribe(websocket)


# --- Event trigger endpoint ---
class EventTriggerRequest(BaseModel):
    event_type: str
    payload: Dict[str, Any] = {}

@app.post("/event/trigger")
async def trigger_event(req: EventTriggerRequest):
    try:
        await event_system.emit(req.event_type, req.payload)
        return {"status": "event_triggered"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
