"""
ANDIE Persistent Cognitive State
Loads operator identity, project memory, episodic highlights, and behavioral
patterns from /app/andie_memory/ and formats them for system prompt injection.
"""
from __future__ import annotations
import json, logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("andie.memory")

MEMORY_ROOT = Path(__file__).resolve().parents[3] / "andie_memory"


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception as e:
        logger.warning("[cognitive] failed to load %s: %s", path, e)
        return {}


def build_cognitive_context() -> str:
    """Build the full operator/project/episodic context string for system prompt."""
    lines = []

    # ── Operator Identity ────────────────────────────────────────────────────
    identity = _load(MEMORY_ROOT / "operator" / "identity.json")
    prefs = _load(MEMORY_ROOT / "operator" / "preferences.json")

    if identity:
        lines.append("━━━ OPERATOR ━━━")
        lines.append(f"Handle: {identity.get('handle', '?')}  |  Org: {identity.get('org', '?')}")
        lines.append(f"Role: {identity.get('role', '?')}")
        focus = identity.get("focus", [])
        if focus:
            lines.append(f"Focus: {', '.join(focus)}")
        comm = identity.get("communication_style", {})
        if comm:
            lines.append(f"Style: {comm.get('preferred', '')}")
            lines.append(f"Execution vs Discussion: {comm.get('execution_vs_discussion', '')}")
        workflow = identity.get("workflow_preferences", {})
        if workflow:
            lines.append(f"Build approach: {workflow.get('build_approach', '')}")

    if prefs:
        lines.append("")
        lines.append("━━━ PREFERENCES ━━━")
        for k, v in prefs.items():
            lines.append(f"  {k}: {v}")

    # ── Infrastructure ───────────────────────────────────────────────────────
    infra = _load(MEMORY_ROOT / "projects" / "infrastructure.json")
    if infra:
        lines.append("")
        lines.append("━━━ INFRASTRUCTURE ━━━")
        for name, node in infra.get("nodes", {}).items():
            role = node.get("role", "")
            lan = node.get("lan", "")
            ts = node.get("tailscale", "")
            svcs = list(node.get("services", {}).keys())
            lines.append(f"  {name}: {role} | LAN {lan} | Tailscale {ts} | {', '.join(svcs)}")
        lines.append(f"  Artifact workspace: {infra.get('artifact_workspace', '')}")

    # ── Active Project ───────────────────────────────────────────────────────
    project = _load(MEMORY_ROOT / "projects" / "andie.json")
    if project:
        lines.append("")
        lines.append("━━━ ACTIVE PROJECT: ANDIE ━━━")
        lines.append(f"Phase: {project.get('phase', '')}")
        milestones = project.get("completed_milestones", [])
        if milestones:
            lines.append("Completed milestones:")
            for m in milestones[-5:]:  # last 5
                lines.append(f"  ✓ {m}")
        next_p = project.get("next_priorities", [])
        if next_p:
            lines.append("Next priorities:")
            for p in next_p[:3]:
                lines.append(f"  → {p}")
        issues = project.get("known_issues", {})
        if issues:
            lines.append("Known issues (handled):")
            for k, v in issues.items():
                lines.append(f"  ! {k}: {v}")

    # ── Episodic Highlights ─────────────────────────────────────────────────
    episodic = _load(MEMORY_ROOT / "episodic" / "highlights.json")
    entries = episodic.get("entries", [])
    if entries:
        lines.append("")
        lines.append("━━━ EPISODIC MEMORY ━━━")
        for e in entries[-6:]:  # most recent 6
            date = e.get("date", "")
            event = e.get("event", "")
            detail = e.get("detail", "")
            lines.append(f"  [{date}] {event}: {detail}")

    # ── Behavioral Patterns ─────────────────────────────────────────────────
    behavioral = _load(MEMORY_ROOT / "behavioral" / "patterns.json")
    observed = behavioral.get("observed", [])
    adaptations = behavioral.get("adaptation_notes", [])
    if observed or adaptations:
        lines.append("")
        lines.append("━━━ BEHAVIORAL CONTEXT ━━━")
        for o in observed:
            lines.append(f"  • {o}")
        for a in adaptations:
            lines.append(f"  ↳ {a}")

    return "\n".join(lines)


def record_episode(event: str, detail: str, date: str | None = None) -> None:
    """Append a new episode to the highlights log."""
    path = MEMORY_ROOT / "episodic" / "highlights.json"
    try:
        data = _load(path)
        entries = data.get("entries", [])
        entries.append({
            "date": date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "event": event,
            "detail": detail,
        })
        # Keep last 50 highlights
        data["entries"] = entries[-50:]
        path.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.warning("[cognitive] record_episode failed: %s", e)


def update_project(name: str, updates: dict) -> None:
    """Merge updates into a project memory file."""
    path = MEMORY_ROOT / "projects" / f"{name}.json"
    try:
        data = _load(path) if path.exists() else {}
        data.update(updates)
        path.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.warning("[cognitive] update_project failed: %s", e)
