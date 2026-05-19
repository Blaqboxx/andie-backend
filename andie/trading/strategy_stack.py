from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class StrategyStack:
    fee_bps: float = 60.0

    def classify_timeframe(self, timeframe: str) -> str:
        tf = str(timeframe or "").strip().lower()
        intraday = {"15m", "30m", "45m", "1h"}
        swing = {"2h", "4h", "6h", "8h", "12h", "1d"}
        if tf in intraday:
            return "intraday"
        if tf in swing:
            return "swing"
        return "swing"

    def score_execution(
        self,
        strategy_result: Dict[str, Any],
        *,
        risk_budget_usd: float,
        timeframe: str,
    ) -> Dict[str, Any]:
        confidence = float(strategy_result.get("confidence") or 0.0)
        risk_score = float(strategy_result.get("risk_score") or 1.0)
        action = str(strategy_result.get("action") or "hold").lower()

        tf_bucket = self.classify_timeframe(timeframe)
        if tf_bucket == "intraday":
            fee_penalty = 0.08
        else:
            fee_penalty = 0.03

        edge_score = max(confidence - risk_score - fee_penalty, -1.0)

        if action in {"reduce_risk", "hold", "wait"}:
            allocation_multiplier = 0.0
            decision = "hold"
        elif edge_score >= 0.30 and action in {"accumulate", "buy", "buy_strong"}:
            allocation_multiplier = 1.0
            decision = "buy"
        elif edge_score >= 0.15 and action in {"accumulate", "dca", "buy"}:
            allocation_multiplier = 0.6
            decision = "accumulate_small"
        elif edge_score >= 0.05 and action in {"dca", "accumulate"}:
            allocation_multiplier = 0.35
            decision = "probe"
        else:
            allocation_multiplier = 0.0
            decision = "hold"

        notional_risk = max(risk_budget_usd, 0.0) * allocation_multiplier

        return {
            "decision": decision,
            "edge_score": round(edge_score, 4),
            "fee_penalty": round(fee_penalty, 4),
            "timeframe_bucket": tf_bucket,
            "risk_budget_usd": round(max(risk_budget_usd, 0.0), 8),
            "allocated_risk_usd": round(notional_risk, 8),
            "input": {
                "action": action,
                "confidence": round(confidence, 4),
                "risk_score": round(risk_score, 4),
            },
        }
