"""
observer.py — Lightweight post-chat observation loop.
Runs non-blocking after every /chat turn.
Extracts signals from operator message + ANDIE response.
Updates behavioral/patterns.json and operator/preferences.json.
"""

import re
import json
import asyncio
from pathlib import Path
from datetime import date

MEMORY_ROOT = Path(__file__).resolve().parents[3] / "andie_memory"

# ── helpers ──────────────────────────────────────────────────────────────────

def _load(path: Path) -> dict | list:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {} if path.suffix == ".json" and "highlights" not in path.name else []

def _save(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))

# ── signal extractors ─────────────────────────────────────────────────────────

_PREF_PATTERNS = [
    # (regex on user msg, preference_key, value_extractor_fn)
    (r"\bdon['\u2019]?t (explain|summarize|add preamble|add intro)\b", "no_preamble", True),
    (r"\b(always|never) (show|include|add)\b (.+)", None, None),   # generic — handled below
    (r"\bjust (do it|act|execute|build)\b", "autonomy", "act_first"),
    (r"\bno (explanation|summary|preamble)\b", "no_preamble", True),
    (r"\bshow (raw|full) (error|output)\b", "error_handling", "show_raw"),
    (r"\bproduction[- ]grade\b", "code_style", "production_no_todos"),
    (r"\bno todo\b", "code_style", "no_todos"),
]

_INFRA_PATTERNS = [
    r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b",        # IP address
    r"\bport (\d{4,5})\b",                                  # port mention
    r"\b(blaqtower\d|blaqbox\w*)\b",                       # known host names
]

_NOTABLE_VERBS = re.compile(
    r"\b(built|deployed|fixed|shipped|launched|connected|verified|confirmed|completed)\b",
    re.IGNORECASE,
)


def _extract_preference_signals(user_msg: str) -> dict:
    """Return {key: value} pairs inferred from operator message."""
    signals = {}
    lower = user_msg.lower()
    for pattern, key, value in _PREF_PATTERNS:
        m = re.search(pattern, lower)
        if m and key:
            signals[key] = value if value is not True else True
    return signals


def _extract_notable_event(user_msg: str, andie_response: str) -> str | None:
    """Return a short event string if this turn is notable, else None."""
    # If ANDIE response contains artifact confirmation
    if "ANDIE_BUILD.json" in andie_response or "workspace/artifacts" in andie_response:
        return None  # artifact_pipeline already records via record_episode

    # If user message contains completion verb
    m = _NOTABLE_VERBS.search(user_msg)
    if m:
        snippet = user_msg[:120].strip()
        return snippet
    return None


# ── main observe function ─────────────────────────────────────────────────────

async def observe(user_message: str, andie_response: str):
    """
    Non-blocking observer. Called via asyncio.create_task from /chat endpoint.
    Gracefully swallows all errors to never affect chat response.
    """
    try:
        await asyncio.get_event_loop().run_in_executor(None, _observe_sync, user_message, andie_response)
    except Exception:
        pass


def _observe_sync(user_message: str, andie_response: str):
    _update_preferences(user_message)
    _update_patterns(user_message, andie_response)
    _maybe_record_episode(user_message, andie_response)


def _update_preferences(user_message: str):
    signals = _extract_preference_signals(user_message)
    if not signals:
        return
    prefs_path = MEMORY_ROOT / "operator" / "preferences.json"
    prefs = _load(prefs_path)
    if not isinstance(prefs, dict):
        prefs = {}
    changed = False
    for k, v in signals.items():
        if prefs.get(k) != v:
            prefs[k] = v
            changed = True
    if changed:
        _save(prefs_path, prefs)


def _update_patterns(user_message: str, andie_response: str):
    patterns_path = MEMORY_ROOT / "behavioral" / "patterns.json"
    data = _load(patterns_path)
    if not isinstance(data, dict):
        data = {}

    observations = data.setdefault("observations", [])

    # Count message length as a proxy for verbosity preference
    msg_len = len(user_message)
    resp_len = len(andie_response)

    # Track rolling avg response length preference (proxy: if user sends short terse messages)
    metrics = data.setdefault("metrics", {})
    count = metrics.get("turn_count", 0) + 1
    metrics["turn_count"] = count
    metrics["avg_user_msg_len"] = round(
        (metrics.get("avg_user_msg_len", msg_len) * (count - 1) + msg_len) / count, 1
    )

    # Detect new infra references
    for pat in _INFRA_PATTERNS:
        for m in re.finditer(pat, user_message, re.IGNORECASE):
            ref = m.group(0)
            entry = f"infra_ref:{ref}"
            if entry not in observations:
                observations.append(entry)

    # Keep observations list bounded
    if len(observations) > 100:
        data["observations"] = observations[-100:]

    _save(patterns_path, data)


def _maybe_record_episode(user_message: str, andie_response: str):
    event = _extract_notable_event(user_message, andie_response)
    if not event:
        return
    highlights_path = MEMORY_ROOT / "episodic" / "highlights.json"
    highlights = _load(highlights_path)
    if not isinstance(highlights, list):
        highlights = []
    highlights.append({
        "date": date.today().isoformat(),
        "event": event[:200],
        "detail": andie_response[:300],
    })
    # Keep last 50
    _save(highlights_path, highlights[-50:])
