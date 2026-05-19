from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = BACKEND_ROOT / "storage" / "andie-brain" / "memory.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS episodes (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                session   TEXT,
                summary   TEXT,
                tags      TEXT,
                timestamp TEXT
            );
            CREATE TABLE IF NOT EXISTS semantic (
                key       TEXT PRIMARY KEY,
                value     TEXT,
                updated   TEXT
            );
            CREATE TABLE IF NOT EXISTS procedural (
                skill      TEXT PRIMARY KEY,
                method     TEXT,
                confidence REAL,
                updated    TEXT
            );
            """
        )


def save_episode(session_id: str, summary: str, tags: list[str]) -> None:
    with get_db() as db:
        db.execute(
            "INSERT INTO episodes (session, summary, tags, timestamp) VALUES (?,?,?,?)",
            (session_id, summary, json.dumps(tags or []), _utc_now()),
        )


def save_semantic(key: str, value: str) -> None:
    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO semantic (key, value, updated) VALUES (?,?,?)",
            (key, value, _utc_now()),
        )


def seed_semantic_defaults(defaults: dict[str, str]) -> int:
    if not defaults:
        return 0

    with get_db() as db:
        existing_rows = db.execute("SELECT key FROM semantic").fetchall()
        existing = {str(row["key"]).strip().lower() for row in existing_rows}
        inserted = 0

        for key, value in defaults.items():
            normalized_key = str(key).strip()
            normalized_value = str(value).strip()
            if not normalized_key or not normalized_value:
                continue
            if normalized_key.lower() in existing:
                continue
            db.execute(
                "INSERT INTO semantic (key, value, updated) VALUES (?,?,?)",
                (normalized_key, normalized_value, _utc_now()),
            )
            inserted += 1

    return inserted


def save_procedural(skill: str, method: str, confidence: float) -> None:
    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO procedural (skill, method, confidence, updated) VALUES (?,?,?,?)",
            (skill, method, max(0.0, min(float(confidence), 1.0)), _utc_now()),
        )


def load_recent_episodes(limit: int = 5) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 100))
    with get_db() as db:
        rows = db.execute(
            "SELECT summary, tags, timestamp FROM episodes ORDER BY timestamp DESC LIMIT ?",
            (bounded,),
        ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        tags_raw = row["tags"]
        try:
            tags = json.loads(tags_raw) if tags_raw else []
        except Exception:
            tags = []
        out.append(
            {
                "summary": row["summary"],
                "tags": tags if isinstance(tags, list) else [],
                "timestamp": row["timestamp"],
            }
        )
    return out


def load_semantic_all() -> dict[str, str]:
    with get_db() as db:
        rows = db.execute("SELECT key, value FROM semantic").fetchall()
    return {str(r["key"]): str(r["value"]) for r in rows}


def load_procedural_all() -> list[dict[str, Any]]:
    with get_db() as db:
        rows = db.execute(
            "SELECT skill, method, confidence FROM procedural ORDER BY confidence DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def load_semantic_items() -> list[dict[str, str]]:
    semantic = load_semantic_all()
    return [
        {"key": key, "value": value}
        for key, value in sorted(semantic.items(), key=lambda item: item[0].lower())
    ]


def memory_snapshot(episode_limit: int = 8) -> dict[str, Any]:
    return {
        "episodic": load_recent_episodes(episode_limit),
        "semantic": load_semantic_items(),
        "procedural": load_procedural_all(),
    }


def build_memory_context() -> str:
    episodes = load_recent_episodes(5)
    semantic = load_semantic_all()
    procedural = load_procedural_all()

    lines = ["[ANDIE LONG-TERM MEMORY]", ""]

    if episodes:
        lines.append("RECENT SESSIONS:")
        for episode in episodes:
            stamp = str(episode.get("timestamp") or "")[:10]
            summary = str(episode.get("summary") or "")
            lines.append(f"  [{stamp}] {summary}")

    if semantic:
        lines.append("")
        lines.append("WHAT I KNOW ABOUT THIS PROJECT:")
        for key, value in semantic.items():
            lines.append(f"  {key}: {value}")

    if procedural:
        lines.append("")
        lines.append("HOW I HAVE LEARNED TO DO THINGS:")
        for proc in procedural:
            skill = str(proc.get("skill") or "unknown")
            method = str(proc.get("method") or "")
            confidence = int(float(proc.get("confidence") or 0) * 100)
            lines.append(f"  {skill} ({confidence}%): {method}")

    lines.append("")
    lines.append("[END MEMORY]")
    return "\n".join(lines)


def extract_json_payload(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {}

    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()

    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidate = text[start : end + 1]
        try:
            payload = json.loads(candidate)
            if isinstance(payload, dict):
                return payload
        except Exception:
            return {}

    return {}
