from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Tuple

import requests


CMC_OHLCV_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/ohlcv/historical"


def _extract_dates_from_prompt(prompt: str) -> Tuple[str | None, str | None]:
    matches = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", prompt)
    if len(matches) >= 2:
        return matches[0], matches[1]
    if len(matches) == 1:
        return matches[0], None
    return None, None


def _extract_symbol_from_prompt(prompt: str) -> str | None:
    # Prefer explicit ticker format like $BTC, fallback to a standalone uppercase token.
    explicit = re.search(r"\$([A-Za-z]{2,10})\b", prompt)
    if explicit:
        return explicit.group(1).upper()

    upper_token = re.search(r"\b([A-Z]{2,10})\b", prompt)
    if upper_token:
        return upper_token.group(1).upper()

    lowered = prompt.lower()
    common = {
        "bitcoin": "BTC",
        "ethereum": "ETH",
        "solana": "SOL",
        "cardano": "ADA",
        "dogecoin": "DOGE",
        "bnb": "BNB",
        "ripple": "XRP",
    }
    for name, ticker in common.items():
        if name in lowered:
            return ticker
    return None


def _extract_interval(prompt: str, metadata: Dict[str, Any]) -> str:
    value = str(metadata.get("interval") or "").strip().lower()
    if value:
        return value

    lowered = prompt.lower()
    for interval in ["hourly", "daily", "weekly", "monthly"]:
        if interval in lowered:
            return interval
    return "daily"


def _extract_query(payload: Dict[str, Any]) -> Dict[str, Any]:
    prompt = str(payload.get("prompt") or "").strip()
    metadata = payload.get("metadata") or {}

    now = datetime.now(timezone.utc)
    default_start = (now - timedelta(days=30)).date().isoformat()
    default_end = now.date().isoformat()

    parsed_start, parsed_end = _extract_dates_from_prompt(prompt)

    symbol = str(metadata.get("symbol") or _extract_symbol_from_prompt(prompt) or "BTC").upper()
    start = str(metadata.get("start") or parsed_start or default_start)
    end = str(metadata.get("end") or parsed_end or default_end)
    convert = str(metadata.get("convert") or "USD").upper()
    interval = _extract_interval(prompt, metadata)
    count = int(metadata.get("count") or metadata.get("limit") or 120)
    count = max(1, min(count, 1000))

    return {
        "symbol": symbol,
        "time_start": start,
        "time_end": end,
        "convert": convert,
        "interval": interval,
        "count": count,
    }


def _api_key() -> str | None:
    return os.environ.get("COINMARKETCAP_API_KEY") or os.environ.get("CMC_API_KEY")


def run_agent(payload: Dict[str, Any]) -> Dict[str, Any]:
    query = _extract_query(payload)
    key = _api_key()

    if not key:
        return {
            "status": "error",
            "agent": "coinmarketcap_agent",
            "error": "Missing COINMARKETCAP_API_KEY (or CMC_API_KEY).",
            "query": query,
            "next": "Set COINMARKETCAP_API_KEY and retry.",
        }

    headers = {
        "Accepts": "application/json",
        "X-CMC_PRO_API_KEY": key,
    }

    try:
        response = requests.get(CMC_OHLCV_URL, params=query, headers=headers, timeout=20)
    except requests.RequestException as exc:
        return {
            "status": "error",
            "agent": "coinmarketcap_agent",
            "error": f"CoinMarketCap request failed: {exc}",
            "query": query,
        }

    if response.status_code >= 400:
        details = None
        try:
            details = response.json()
        except ValueError:
            details = {"raw": response.text[:500]}
        return {
            "status": "error",
            "agent": "coinmarketcap_agent",
            "httpStatus": response.status_code,
            "error": "CoinMarketCap returned an error response.",
            "details": details,
            "query": query,
        }

    body = response.json()
    quotes = (((body.get("data") or {}).get("quotes")) or [])

    series = []
    for point in quotes:
        quote_values = ((point.get("quote") or {}).get(query["convert"]) or {})
        series.append(
            {
                "timestamp": point.get("timestamp"),
                "open": quote_values.get("open"),
                "high": quote_values.get("high"),
                "low": quote_values.get("low"),
                "close": quote_values.get("close"),
                "volume": quote_values.get("volume"),
                "marketCap": quote_values.get("market_cap"),
            }
        )

    return {
        "status": "ok",
        "agent": "coinmarketcap_agent",
        "query": query,
        "points": len(series),
        "first": series[0] if series else None,
        "last": series[-1] if series else None,
        "series": series,
    }
