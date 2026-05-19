from __future__ import annotations
from typing import Any

from andie_backend.andie.action.intent_router import classify, Intent
from andie_backend.andie.action.artifact_pipeline import run_artifact_pipeline
from andie_backend.andie.diagnostics.probe_runner import run_domain, run_all as _run_all_probes


async def route(message: str, ollama_chat_fn, system_prompt: str) -> dict[str, Any]:
    """
    Classify the message and route to the appropriate pipeline.
    Returns a unified response dict with intent metadata attached.
    """
    intent: Intent = classify(message)

    if intent.type == "ARTIFACT_BUILD":
        result = await run_artifact_pipeline(intent.target, ollama_chat_fn, system_prompt)
        if result["status"] == "ok":
            files_list = "\n".join(f"  - {f}" for f in result["files"])
            result["response"] = (
                f"[PIPELINE EXECUTED]\n\n"
                f"Artifact: {result.get('project_name', intent.target)}\n"
                f"Workspace: {result['workspace']}\n\n"
                f"Files generated:\n{files_list}\n\n"
                f"Run: {result.get('run_command') or 'see workspace'}\n\n"
                f"Status: READY"
            )
        else:
            result["response"] = f"[BUILD FAILED] {result.get('error', 'unknown error')}"
        result["intent"] = intent.type
        result["confidence"] = intent.confidence
        return result

    if intent.type == "DIAGNOSTIC":
        domain = intent.target if intent.target != "all" else None
        # Map common synonyms to domain names
        _domain_map = {
            "ollama": "gpu", "redis": "containers", "disk": "storage",
            "container": "containers", "model": "models",
        }
        if domain in _domain_map:
            domain = _domain_map[domain]
        try:
            if domain and domain in ("containers", "network", "gpu", "storage", "models"):
                result_data = await run_domain(domain)
                domains_block = {domain: result_data}
                overall = result_data["status"]
            else:
                result_data = await _run_all_probes()
                domains_block = result_data["domains"]
                overall = result_data["status"]

            # Format human-readable summary
            lines = [f"[DIAGNOSTIC — {overall.upper()}]\n"]
            for dom, dr in domains_block.items():
                lines.append(f"▸ {dom}: {dr['status']} ({dr.get('elapsed_ms', '?')}ms)")
                for c in dr.get("checks", []):
                    icon = {"healthy": "✓", "degraded": "⚠", "failed": "✗", "unreachable": "✗"}.get(c["status"], "?")
                    lines.append(f"  {icon} {c['check']}: {c['detail']}")
            summary = "\n".join(lines)
        except Exception as e:
            summary = f"[DIAGNOSTIC FAILED] {type(e).__name__}: {e}"
            domains_block = {}
            overall = "failed"

        return {
            "status": "ok",
            "intent": "DIAGNOSTIC",
            "confidence": intent.confidence,
            "response": summary,
            "diagnostic": domains_block,
            "overall_health": overall,
            "meta": {"source": "probe_runner"},
        }

    # For all other intents, fall through to standard chat but annotate
    chat_result = await ollama_chat_fn(
        messages=[{"role": "user", "content": message}],
        system=system_prompt,
    )
    return {
        "status": "ok",
        "intent": intent.type,
        "confidence": intent.confidence,
        "response": chat_result.get("response", ""),
        "meta": {
            "source": "ollama",
            "model": chat_result.get("model", ""),
            "node": chat_result.get("node", ""),
            "latency_ms": chat_result.get("latency_ms", 0),
        },
    }
