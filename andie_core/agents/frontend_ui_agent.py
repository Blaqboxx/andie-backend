from __future__ import annotations

from typing import Any, Dict, List


DEFAULT_TARGET_FILES = [
    "andie-ui/package.json",
    "andie-ui/src/pages/Dashboard.jsx",
    "andie-ui/src/components/dashboard",
    "andie-ui/vite.config.js",
]


def _extract_issue(payload: Dict[str, Any]) -> str:
    prompt = str(payload.get("prompt") or "").strip()
    context = str(payload.get("context") or "").strip()
    if context:
        return f"{prompt}\n\nContext:\n{context}".strip()
    return prompt


def _collect_target_files(payload: Dict[str, Any]) -> List[str]:
    metadata = payload.get("metadata") or {}
    files = metadata.get("files") or []
    normalized = [str(path).strip() for path in files if str(path).strip()]
    return normalized or list(DEFAULT_TARGET_FILES)


def _build_execution_plan(issue_text: str) -> List[str]:
    lowered = issue_text.lower()
    steps = [
        "Reproduce the frontend failure on the local Vite app.",
        "Trace the failure to either dev-server boot, API proxying, or dashboard rendering state.",
        "Patch the smallest frontend or Vite integration surface that fixes the operator workflow.",
        "Verify the dashboard renders the decision trace panel from live backend metadata.",
    ]
    if "5173" in lowered or "vite" in lowered:
        steps.insert(1, "Stabilize the Vite dev server on 127.0.0.1:5173 before validating UI behavior.")
    if "decision" in lowered or "why this node" in lowered:
        steps[-1] = "Verify the 'Why This Node?' panel renders selected node, reason, score, and ranked candidates from SSE metadata."
    return steps


def _build_success_criteria(issue_text: str) -> List[str]:
    criteria = [
        "The frontend dev server starts and stays reachable on port 5173.",
        "The dashboard loads without 502 or connection-refused errors.",
        "The targeted UI behavior is visible in the operator dashboard.",
    ]
    lowered = issue_text.lower()
    if "decision" in lowered or "why this node" in lowered:
        criteria.append("The 'Why This Node?' panel shows dispatchReason, dispatchScore, and rankedCandidates from a real orchestrator event.")
    return criteria


def run_agent(payload: Dict[str, Any]) -> Dict[str, Any]:
    issue_text = _extract_issue(payload)
    target_files = _collect_target_files(payload)

    return {
        "status": "ready",
        "agent": "frontend_ui_agent",
        "issue": issue_text,
        "targetFiles": target_files,
        "executionPlan": _build_execution_plan(issue_text),
        "successCriteria": _build_success_criteria(issue_text),
        "suggestedTask": (
            "Investigate and fix the frontend UI issue in andie-ui, prioritize Vite stability and dashboard rendering, "
            "then verify the operator-facing fix against the live backend event stream."
        ),
    }