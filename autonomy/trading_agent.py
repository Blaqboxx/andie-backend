from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict

from .decision_engine import weighted_decision
from .explainer import remember_decision_context
from .knowledge_integration import enrich_autonomy_context, is_knowledge_query_disabled
from .learning import detect_pattern, recent_events, recent_success_rate, record_outcome
from .reasoning_engine import build_multi_agent_plan, build_reasoning_plan
from .scoring import SOURCE_WEIGHTS, compute_confidence, compute_trust


DEFAULT_EVENTS_URL = "http://127.0.0.1:8000/events/publish"
_RECENT_SIGNALS: dict[tuple[str, str], float] = {}


def _publish_event(payload: Dict[str, Any], timeout_seconds: float = 3.0) -> bool:
    url = os.environ.get("ANDIE_EVENTS_URL", DEFAULT_EVENTS_URL)
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False


def _risk_allows(trade: Dict[str, Any], metadata: Dict[str, Any]) -> tuple[bool, str | None]:
    max_daily_loss = float(os.environ.get("ANDIE_TRADING_MAX_DAILY_LOSS", "200"))
    max_open_positions = int(os.environ.get("ANDIE_TRADING_MAX_OPEN_POSITIONS", "3"))
    dedupe_seconds = int(os.environ.get("ANDIE_TRADING_SIGNAL_DEDUPE_SECONDS", "60"))

    daily_loss = float(metadata.get("dailyLoss", 0) or 0)
    open_positions = int(metadata.get("openPositions", 0) or 0)

    if daily_loss > max_daily_loss:
        return False, "daily_loss_limit"
    if open_positions >= max_open_positions:
        return False, "max_open_positions"

    key = (str(trade.get("symbol") or ""), str(trade.get("action") or ""))
    now = time.time()
    last_seen = _RECENT_SIGNALS.get(key, 0.0)
    if key[0] and key[1] and (now - last_seen) < dedupe_seconds:
        return False, "duplicate_signal"

    _RECENT_SIGNALS[key] = now
    return True, None


def _execute_trade_via_cryptonia(trade: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
    dry_run = bool(metadata.get("dryRun", True))
    if dry_run:
        return {
            "status": "simulated",
            "dryRun": True,
            "symbol": trade.get("symbol"),
            "action": trade.get("action"),
            "price": trade.get("price"),
        }

    backend_root = Path(__file__).resolve().parent.parent
    cryptonia_root = backend_root / "Cryptonia"
    if str(cryptonia_root) not in sys.path:
        sys.path.insert(0, str(cryptonia_root))

    from cryptonia.config import Config
    from cryptonia.exchange import Exchange

    config_path = os.environ.get("CRYPTONIA_CONFIG_PATH", str(cryptonia_root / "config.yaml"))
    config = Config(config_path)
    exchange = Exchange(
        config.exchange_name,
        config.get_exchange_config(),
        sandbox=config.get("exchange.sandbox", False),
    )

    amount = float(metadata.get("amount") or os.environ.get("ANDIE_TRADING_ORDER_SIZE", "0.001"))
    order = exchange.create_order(
        str(trade.get("symbol")),
        "market",
        str(trade.get("action")),
        amount,
    )
    return {
        "status": "executed",
        "dryRun": False,
        "symbol": trade.get("symbol"),
        "action": trade.get("action"),
        "amount": amount,
        "order": order,
    }


async def run_agent(context: Dict[str, Any]) -> Dict[str, Any]:
    # Enrich context with knowledge guidance
    if not is_knowledge_query_disabled():
        context = enrich_autonomy_context(context, knowledge_enabled=True)

    event = context.get("event") if isinstance(context.get("event"), dict) else {}
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}

    trade = {
        "symbol": metadata.get("symbol"),
        "action": metadata.get("signal") or event.get("action"),
        "price": metadata.get("price"),
        "strategy": metadata.get("strategy"),
    }

    mode = str(os.environ.get("TRADING_MODE", metadata.get("tradingMode", "SEMI_AUTO"))).upper()
    approval_id = metadata.get("approvalId")

    # Reasoning layer: publish visible step-by-step plan for timeline observability.
    knowledge = context.get("knowledge_guidance") if isinstance(context.get("knowledge_guidance"), dict) else {}
    plan = build_reasoning_plan(event, knowledge, llm=None)
    multi_agent_plan = build_multi_agent_plan(event)
    context["plan"] = plan
    context["multi_agent_plan"] = multi_agent_plan
    for step in plan:
        _publish_event(
            {
                "type": "PLAN_STEP",
                "status": "info",
                "target": "trading",
                "message": f"Plan step: {step.get('step')}",
                "metadata": {
                    "step": step.get("step"),
                    "why": step.get("why"),
                    "source": "trading_agent",
                    "approvalId": approval_id,
                },
            }
        )

    # Confidence and trust scoring.
    knowledge_results = knowledge.get("results") if isinstance(knowledge.get("results"), list) else []
    best_distance = None
    if knowledge_results:
        first = knowledge_results[0] if isinstance(knowledge_results[0], dict) else {}
        if first.get("distance") is not None:
            best_distance = float(first.get("distance") or 1.0)
    similarity = max(0.0, 1.0 - min(best_distance, 1.0)) if best_distance is not None else 0.6

    source_label = "local" if knowledge.get("relevant") else "web"
    source_weight = SOURCE_WEIGHTS.get(source_label, 0.5)
    success_rate = recent_success_rate("execute_trade")
    pattern = detect_pattern(recent_events(20))

    confidence = compute_confidence(
        similarity=similarity,
        source_weight=source_weight,
        success_rate=success_rate,
    )
    trust = compute_trust(source_label, recency_score=1.0)
    context["confidence"] = confidence
    context["trust"] = trust
    context["pattern"] = pattern
    context["trade"] = trade

    if confidence < 0.5:
        _publish_event(
            {
                "type": "LOW_CONFIDENCE",
                "status": "warning",
                "target": "trading",
                "message": f"Low confidence decision: {confidence}",
                "metadata": {
                    "confidence": confidence,
                    "trust": trust,
                    "source": source_label,
                    "approvalId": approval_id,
                },
            }
        )

    decision = weighted_decision(context)
    context["decision"] = decision
    remember_decision_context(context)
    if decision == "BLOCK":
        _publish_event(
            {
                "type": "TRADE_BLOCKED",
                "status": "blocked",
                "target": "trading",
                "message": "Trade blocked by weighted decision",
                "reason": "weighted_decision_block",
                "metadata": {
                    "trade": trade,
                    "mode": mode,
                    "decision": decision,
                    "confidence": confidence,
                    "trust": trust,
                    "source": "trading_agent",
                    "approvalId": approval_id,
                },
            }
        )
        record_outcome(event, "execute_trade", "blocked")
        return {
            "status": "blocked",
            "reason": "weighted_decision_block",
            "mode": mode,
            "trade": trade,
            "confidence": confidence,
            "trust": trust,
            "decision": decision,
        }

    allowed, reason = _risk_allows(trade, metadata)
    if not allowed:
        _publish_event(
            {
                "type": "TRADE_BLOCKED",
                "status": "blocked",
                "target": "trading",
                "message": f"Trade blocked by risk policy: {reason}",
                "reason": reason,
                "metadata": {
                    "trade": trade,
                    "mode": mode,
                    "confidence": confidence,
                    "trust": trust,
                    "source": "trading_agent",
                    "approvalId": approval_id,
                },
            }
        )
        record_outcome(event, "execute_trade", "blocked")
        return {
            "status": "blocked",
            "reason": reason,
            "mode": mode,
            "trade": trade,
            "confidence": confidence,
            "trust": trust,
            "decision": decision,
        }

    if decision == "REVIEW" and mode == "AUTO":
        _publish_event(
            {
                "type": "APPROVAL_REQUIRED",
                "status": "pending",
                "target": "trading",
                "message": f"Approval required by weighted decision for {trade.get('action')} {trade.get('symbol')}",
                "metadata": {
                    "trade": trade,
                    "mode": mode,
                    "decision": decision,
                    "confidence": confidence,
                    "trust": trust,
                    "source": "trading_agent",
                    "approvalId": approval_id,
                },
            }
        )
        record_outcome(event, "execute_trade", "review")
        return {
            "status": "approval_required",
            "mode": mode,
            "trade": trade,
            "confidence": confidence,
            "trust": trust,
            "decision": decision,
        }

    if mode == "SAFE":
        _publish_event(
            {
                "type": "APPROVAL_REQUIRED",
                "status": "pending",
                "target": "trading",
                "message": f"Approval required for trade {trade.get('action')} {trade.get('symbol')}",
                "metadata": {
                    "trade": trade,
                    "mode": mode,
                    "confidence": confidence,
                    "trust": trust,
                    "source": "trading_agent",
                    "approvalId": approval_id,
                },
            }
        )
        record_outcome(event, "execute_trade", "review")
        return {
            "status": "approval_required",
            "mode": mode,
            "trade": trade,
            "confidence": confidence,
            "trust": trust,
            "decision": decision,
        }

    if mode == "SEMI_AUTO":
        _publish_event(
            {
                "type": "APPROVAL_REQUIRED",
                "status": "pending",
                "target": "trading",
                "message": f"Semi-auto mode requires approval for {trade.get('action')} {trade.get('symbol')}",
                "metadata": {
                    "trade": trade,
                    "mode": mode,
                    "confidence": confidence,
                    "trust": trust,
                    "source": "trading_agent",
                    "approvalId": approval_id,
                },
            }
        )
        record_outcome(event, "execute_trade", "review")
        return {
            "status": "approval_required",
            "mode": mode,
            "trade": trade,
            "confidence": confidence,
            "trust": trust,
            "decision": decision,
        }

    try:
        execution = _execute_trade_via_cryptonia(trade, metadata)
        _publish_event(
            {
                "type": "TRADE_EXECUTED",
                "status": execution.get("status", "executed"),
                "target": "trading",
                "message": f"Trade executed: {trade.get('action')} {trade.get('symbol')}",
                "metadata": {
                    "trade": trade,
                    "execution": execution,
                    "mode": mode,
                    "decision": decision,
                    "confidence": confidence,
                    "trust": trust,
                    "source": "trading_agent",
                    "approvalId": approval_id,
                },
            }
        )
        record_outcome(event, "execute_trade", "success")
        return {
            "status": "ok",
            "mode": mode,
            "trade": trade,
            "execution": execution,
            "confidence": confidence,
            "trust": trust,
            "decision": decision,
        }
    except Exception as exc:
        _publish_event(
            {
                "type": "TRADE_EXECUTION_FAILED",
                "status": "failed",
                "target": "trading",
                "message": str(exc),
                "reason": "execution_error",
                "metadata": {
                    "trade": trade,
                    "mode": mode,
                    "decision": decision,
                    "confidence": confidence,
                    "trust": trust,
                    "source": "trading_agent",
                    "approvalId": approval_id,
                },
            }
        )
        record_outcome(event, "execute_trade", "failed")
        return {
            "status": "failed",
            "mode": mode,
            "trade": trade,
            "error": str(exc),
            "confidence": confidence,
            "trust": trust,
            "decision": decision,
        }


async def execute_approved_trade(
    approval_id: str,
    trade: Dict[str, Any],
    metadata: Dict[str, Any] | None = None,
    actor: str | None = None,
) -> Dict[str, Any]:
    details = dict(metadata or {})
    details["approvalId"] = approval_id

    allowed, reason = _risk_allows(trade, details)
    if not allowed:
        _publish_event(
            {
                "type": "TRADE_BLOCKED",
                "status": "blocked",
                "target": "trading",
                "message": f"Approved trade blocked by risk policy: {reason}",
                "reason": reason,
                "metadata": {
                    "trade": trade,
                    "source": "trading_agent",
                    "approvalId": approval_id,
                    "actor": actor,
                },
            }
        )
        return {"status": "blocked", "reason": reason, "approvalId": approval_id, "trade": trade}

    try:
        execution = _execute_trade_via_cryptonia(trade, details)
        _publish_event(
            {
                "type": "TRADE_EXECUTED",
                "status": execution.get("status", "executed"),
                "target": "trading",
                "message": f"Approved trade executed: {trade.get('action')} {trade.get('symbol')}",
                "metadata": {
                    "trade": trade,
                    "execution": execution,
                    "source": "trading_agent",
                    "approvalId": approval_id,
                    "actor": actor,
                },
            }
        )
        return {"status": "ok", "approvalId": approval_id, "trade": trade, "execution": execution}
    except Exception as exc:
        _publish_event(
            {
                "type": "TRADE_EXECUTION_FAILED",
                "status": "failed",
                "target": "trading",
                "message": str(exc),
                "reason": "execution_error",
                "metadata": {
                    "trade": trade,
                    "source": "trading_agent",
                    "approvalId": approval_id,
                    "actor": actor,
                },
            }
        )
        return {
            "status": "failed",
            "approvalId": approval_id,
            "trade": trade,
            "error": str(exc),
        }
