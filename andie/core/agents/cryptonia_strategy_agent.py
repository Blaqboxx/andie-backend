from __future__ import annotations

from math import sqrt
from typing import Any, Dict, List


def _to_float(value: Any) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n != n:
        return None
    return n


def _extract_closes(series: List[Dict[str, Any]]) -> List[float]:
    closes: List[float] = []
    for point in series:
        if not isinstance(point, dict):
            continue
        close = _to_float(point.get("close"))
        if close is not None and close > 0:
            closes.append(close)
    return closes


def _sma(values: List[float], window: int) -> float | None:
    if window <= 0 or len(values) < window:
        return None
    section = values[-window:]
    return sum(section) / len(section)


def _volatility(returns: List[float]) -> float:
    if len(returns) < 2:
        return 0.0
    avg = sum(returns) / len(returns)
    variance = sum((x - avg) ** 2 for x in returns) / max(len(returns) - 1, 1)
    return sqrt(max(variance, 0.0))


def _bounded(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def run_agent(payload: Dict[str, Any]) -> Dict[str, Any]:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    constraints = metadata.get("constraints") if isinstance(metadata.get("constraints"), dict) else {}
    market_data = metadata.get("market_data") if isinstance(metadata.get("market_data"), dict) else {}

    series = market_data.get("series") if isinstance(market_data.get("series"), list) else []
    closes = _extract_closes(series)

    if len(closes) < 5:
        return {
            "status": "error",
            "agent": "cryptonia_strategy_agent",
            "error": "Insufficient market data for strategy analysis.",
            "required": "At least 5 valid close points",
            "received": len(closes),
            "confidence": 0.0,
            "risk_score": 1.0,
            "data_quality": 0.0,
            "action": "wait",
        }

    first_close = closes[0]
    last_close = closes[-1]
    trend_ratio = (last_close - first_close) / first_close if first_close else 0.0
    trend_strength = _bounded(abs(trend_ratio) * 5.0)

    returns = []
    for idx in range(1, len(closes)):
        prev_value = closes[idx - 1]
        if prev_value <= 0:
            continue
        returns.append((closes[idx] - prev_value) / prev_value)

    volatility = _volatility(returns)
    volatility_score = _bounded(volatility * 12.0)

    sma_short = _sma(closes, 7) or last_close
    sma_long = _sma(closes, 21) or sma_short

    if last_close > sma_short > sma_long:
        trend = "bullish"
    elif last_close < sma_short < sma_long:
        trend = "bearish"
    else:
        trend = "sideways"

    data_coverage = _bounded(len(closes) / max(30, len(series) or 1))
    data_quality = _bounded(0.55 + 0.45 * data_coverage)

    risk_level = str(constraints.get("risk_level") or "moderate").lower()
    if risk_level == "low":
        risk_bias = 0.15
    elif risk_level == "high":
        risk_bias = -0.08
    else:
        risk_bias = 0.0

    base_risk = 0.25 + (0.55 * volatility_score)
    if trend == "bearish":
        base_risk += 0.12
    risk_score = _bounded(base_risk + risk_bias)

    confidence = 0.48 + (0.30 * trend_strength) + (0.22 * data_quality) - (0.35 * volatility_score)
    if trend == "sideways":
        confidence -= 0.08
    confidence = _bounded(confidence)

    timeframe = str(constraints.get("timeframe") or "swing").lower()

    if trend == "bullish" and confidence >= 0.70 and risk_score <= 0.45:
        action = "accumulate"
    elif trend == "bearish" and risk_score >= 0.55:
        action = "reduce_risk"
    elif trend == "sideways" or confidence < 0.62:
        action = "hold"
    else:
        action = "dca"

    insight = f"{trend.upper()} trend with volatility score {volatility_score:.2f} on {timeframe} horizon"

    return {
        "status": "ok",
        "agent": "cryptonia_strategy_agent",
        "insight": insight,
        "trend": trend,
        "action": action,
        "confidence": round(confidence, 4),
        "risk_score": round(risk_score, 4),
        "data_quality": round(data_quality, 4),
        "signals": {
            "first_close": first_close,
            "last_close": last_close,
            "sma_short": round(sma_short, 6),
            "sma_long": round(sma_long, 6),
            "trend_ratio": round(trend_ratio, 6),
            "volatility": round(volatility, 6),
            "volatility_score": round(volatility_score, 6),
            "data_coverage": round(data_coverage, 6),
        },
        "constraints": {
            "risk_level": risk_level,
            "timeframe": timeframe,
        },
    }
