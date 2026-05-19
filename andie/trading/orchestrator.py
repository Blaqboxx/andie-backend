from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from andie_backend.trading.capital_manager import CapitalManager
from andie_backend.trading.growth_tracker import GrowthTracker
from andie_backend.trading.strategy_stack import StrategyStack
from ..core.agents.coinmarketcap_agent import run_agent as run_data_agent
from ..core.agents.cryptonia_strategy_agent import run_agent as run_strategy_agent


_STORAGE_DIR = Path(__file__).resolve().parents[2] / "storage" / "trading"
_STATE_PATH = _STORAGE_DIR / "capital_state.json"
_CYCLE_LOG_PATH = _STORAGE_DIR / "cycle_log.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_storage() -> None:
    _STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    if not _STATE_PATH.exists():
        _STATE_PATH.write_text("{}", encoding="utf-8")
    if not _CYCLE_LOG_PATH.exists():
        _CYCLE_LOG_PATH.write_text("[]", encoding="utf-8")


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_state() -> dict:
    _ensure_storage()
    state = _read_json(_STATE_PATH, {})
    if not isinstance(state, dict):
        return {}
    return state


def _append_cycle_log(entry: dict) -> None:
    _ensure_storage()
    log = _read_json(_CYCLE_LOG_PATH, [])
    if not isinstance(log, list):
        log = []
    log.append(entry)
    _write_json(_CYCLE_LOG_PATH, log[-500:])


def get_capital_state() -> dict:
    state = _load_state()
    if not state:
        return {
            "updated_at": None,
            "start_balance": 0.0,
            "total_balance": 0.0,
            "active_capital": 0.0,
            "reserve_capital": 0.0,
            "pending_deploy_capital": 0.0,
            "monthly_deposit": 0.0,
            "active_ratio": 0.60,
            "weekly_deploy_rate": 0.25,
            "risk_per_trade_pct": 0.005,
            "cumulative_deposits": 0.0,
            "target_balance": 100000.0,
            "horizon_months": 12,
        }
    return state


def list_cycle_history(limit: int = 20) -> list[dict]:
    _ensure_storage()
    log = _read_json(_CYCLE_LOG_PATH, [])
    if not isinstance(log, list):
        return []
    bounded = max(1, min(int(limit), 200))
    return log[-bounded:]


def run_capital_orchestration(config: Dict[str, Any]) -> Dict[str, Any]:
    state = _load_state()

    current_balance = float(config.get("current_balance") or state.get("total_balance") or 0.0)
    monthly_deposit = float(config.get("monthly_deposit") or state.get("monthly_deposit") or 0.0)
    deposit_amount = float(config.get("deposit_amount") or 0.0)
    active_ratio = float(config.get("active_ratio") or state.get("active_ratio") or 0.60)
    weekly_deploy_rate = float(config.get("weekly_deploy_rate") or state.get("weekly_deploy_rate") or 0.25)
    risk_per_trade_pct = float(config.get("risk_per_trade_pct") or state.get("risk_per_trade_pct") or 0.005)
    realized_pnl = float(config.get("realized_pnl") or 0.0)

    target_balance = float(config.get("target_balance") or 100000.0)
    horizon_months = int(config.get("horizon_months") or 12)

    capital = CapitalManager.from_balance(
        total_balance=current_balance,
        active_ratio=active_ratio,
        monthly_deposit=monthly_deposit,
        weekly_deploy_rate=weekly_deploy_rate,
        risk_per_trade_pct=risk_per_trade_pct,
    )

    if isinstance(state.get("pending_deploy_capital"), (int, float)):
        capital.pending_deploy_capital = float(state.get("pending_deploy_capital") or 0.0)

    # NEXUS: capital policy orchestration
    deposit_registered = capital.apply_deposit(deposit_amount)
    deployed_this_cycle = capital.activate_pending_capital(weeks=1)
    if realized_pnl != 0:
        capital.apply_realized_pnl(realized_pnl)

    # CIPHER: market data retrieval
    symbol = str(config.get("symbol") or "BTC").upper()
    interval = str(config.get("interval") or "daily")
    start = config.get("start")
    end = config.get("end")
    data_payload = {
        "prompt": f"Get {symbol} market data from {start or 'last month'} to {end or 'today'} with {interval} interval",
        "metadata": {
            "symbol": symbol,
            "interval": interval,
            "start": start,
            "end": end,
            "count": int(config.get("count") or 120),
            "convert": str(config.get("convert") or "USD"),
        },
    }
    data_result = run_data_agent(data_payload)

    market_series = data_result.get("series") if isinstance(data_result, dict) else None
    if not isinstance(market_series, list):
        market_series = []

    # NEXUS + strategy stack: decision on fixed risk budget
    strategy_payload = {
        "metadata": {
            "constraints": {
                "risk_level": str(config.get("risk_level") or "moderate"),
                "timeframe": str(config.get("timeframe") or "1h"),
            },
            "market_data": {
                "series": market_series,
            },
        }
    }
    strategy_result = run_strategy_agent(strategy_payload)

    stack = StrategyStack(fee_bps=float(config.get("fee_bps") or 60.0))
    execution = stack.score_execution(
        strategy_result if isinstance(strategy_result, dict) else {},
        risk_budget_usd=capital.position_risk_usd(),
        timeframe=str(config.get("timeframe") or "1h"),
    )

    # HERALD: growth and performance telemetry
    tracker = GrowthTracker(
        start_balance=float(state.get("start_balance") or current_balance),
        target_balance=target_balance,
        horizon_months=horizon_months,
        cumulative_deposits=float(state.get("cumulative_deposits") or 0.0),
    )
    tracker.register_deposit(deposit_registered)
    growth = tracker.evaluate(capital.total_balance)

    cycle = {
        "ts": _utc_now(),
        "roles": {
            "orchestrator": "NEXUS",
            "data": "CIPHER",
            "monitor": "HERALD",
        },
        "capital": {
            **capital.snapshot(),
            "deposit_registered": round(deposit_registered, 8),
            "deployed_this_cycle": round(deployed_this_cycle, 8),
        },
        "market": {
            "symbol": symbol,
            "interval": interval,
            "data_status": data_result.get("status") if isinstance(data_result, dict) else "error",
            "points": len(market_series),
            "query": data_result.get("query") if isinstance(data_result, dict) else None,
        },
        "strategy": strategy_result,
        "execution": execution,
        "growth": growth,
        "guardrails": {
            "risk_based_on": "active_capital",
            "deposit_deploy_policy": "weekly_gradual_20_30pct",
            "no_risk_escalation_on_deposit": True,
        },
    }

    new_state = {
        "updated_at": cycle["ts"],
        "start_balance": growth["start_balance"],
        "total_balance": capital.total_balance,
        "active_capital": capital.active_capital,
        "reserve_capital": capital.reserve_capital,
        "pending_deploy_capital": capital.pending_deploy_capital,
        "monthly_deposit": monthly_deposit,
        "active_ratio": active_ratio,
        "weekly_deploy_rate": weekly_deploy_rate,
        "risk_per_trade_pct": risk_per_trade_pct,
        "cumulative_deposits": growth["cumulative_deposits"],
        "target_balance": target_balance,
        "horizon_months": horizon_months,
    }

    _write_json(_STATE_PATH, new_state)
    _append_cycle_log(cycle)

    return {
        "status": "ok",
        "mode": "capital_orchestration_cycle",
        "cycle": cycle,
        "state": new_state,
    }
