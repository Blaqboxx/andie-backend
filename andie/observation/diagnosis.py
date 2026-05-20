"""
observation/diagnosis.py — Correlate multi-domain probe results into a
human-readable diagnosis with recommended actions.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from andie_backend.andie.observation.loop import ObservationSnapshot


# ── Diagnosis rules ───────────────────────────────────────────────────────────
# Each rule: (condition_fn, cause, action, severity)
# Evaluated in order; first match wins per domain.

_RULES = [
    # Containers
    {
        "id": "backend-unreachable",
        "domain": "containers",
        "check": "andie-backend",
        "match_status": {"unreachable", "failed"},
        "cause": "Backend API container is down or unresponsive.",
        "action": "Restart andie-backend: `docker compose restart backend`",
        "severity": "critical",
    },
    {
        "id": "ui-unreachable",
        "domain": "containers",
        "check": "andie-ui",
        "match_status": {"unreachable", "failed"},
        "cause": "UI container is unreachable.",
        "action": "Restart andie-ui: `docker compose restart andie-ui` then rebuild if needed.",
        "severity": "high",
    },
    {
        "id": "ollama-unreachable",
        "domain": "containers",
        "check": "ollama",
        "match_status": {"unreachable", "failed"},
        "cause": "Ollama inference server on Blaqtower3 is unreachable.",
        "action": "Check Blaqtower3 network / `systemctl status ollama`.",
        "severity": "high",
    },
    # GPU / Models
    {
        "id": "gpu-cold",
        "domain": "gpu",
        "check": "gpu-warm-models",
        "match_status": {"degraded"},
        "cause": "No models are currently warm on the GPU — cold start latency expected.",
        "action": "Send a warmup inference request or wait for auto-load (~40s).",
        "severity": "low",
    },
    {
        "id": "ollama-api-down",
        "domain": "gpu",
        "check": "ollama-api",
        "match_status": {"unreachable", "failed"},
        "cause": "Ollama API is down — inference unavailable.",
        "action": "Check Blaqtower3: `systemctl restart ollama`",
        "severity": "critical",
    },
    # Models
    {
        "id": "model-missing",
        "domain": "models",
        "check": None,   # any check starting with 'model:'
        "match_status": {"failed"},
        "cause": "A required model is missing from the Ollama registry.",
        "action": "Pull the missing model: `ollama pull <model-name>`",
        "severity": "high",
    },
    {
        "id": "inference-slow",
        "domain": "models",
        "check": "inference-latency",
        "match_status": {"degraded"},
        "cause": "Inference latency probe exceeded threshold — possible cold start or resource pressure.",
        "action": "Monitor VRAM usage and consider evicting unused models.",
        "severity": "medium",
    },
    # Network
    {
        "id": "lan-unreachable",
        "domain": "network",
        "check": "blaqtower3-lan",
        "match_status": {"unreachable"},
        "cause": "Blaqtower3 (GPU node) is unreachable on LAN.",
        "action": "Check LAN cable / switch. Fallback to Tailscale route.",
        "severity": "high",
    },
    {
        "id": "tailscale-unreachable",
        "domain": "network",
        "check": "blaqtower2-tailscale",
        "match_status": {"unreachable"},
        "cause": "Blaqtower2 is unreachable over Tailscale — VPN may be down.",
        "action": "Run `tailscale up` on Blaqtower2.",
        "severity": "medium",
    },
    # Storage
    {
        "id": "disk-full",
        "domain": "storage",
        "check": None,
        "match_status": {"failed"},
        "cause": "A monitored filesystem is critically full (≥95%).",
        "action": "Free space: clear old artifact builds, Docker images, or logs.",
        "severity": "critical",
    },
    {
        "id": "disk-warn",
        "domain": "storage",
        "check": None,
        "match_status": {"degraded"},
        "cause": "A filesystem is approaching capacity (≥85%).",
        "action": "Review disk usage. Consider pruning Docker volumes or old builds.",
        "severity": "medium",
    },
]


def diagnose(snapshot: "ObservationSnapshot") -> dict:
    """
    Given an ObservationSnapshot, return:
      {
        "overall": "healthy"|"degraded"|"critical",
        "findings": [ {id, domain, cause, action, severity, check}, ... ],
        "summary": "One-line human summary",
      }
    """
    findings = []

    for domain_name, ds in snapshot.domains.items():
        # Build a check-name → status lookup
        check_map = {c.get("check"): c.get("status") for c in ds.checks}

        for rule in _RULES:
            if rule["domain"] != domain_name:
                continue

            target_check = rule["check"]
            match_statuses = rule["match_status"]

            if target_check is None:
                # Any check in this domain with the bad status
                matched = [
                    c for c in ds.checks
                    if c.get("status") in match_statuses
                ]
                for c in matched:
                    findings.append({
                        "id": rule["id"],
                        "domain": domain_name,
                        "check": c.get("check"),
                        "cause": rule["cause"],
                        "action": rule["action"],
                        "severity": rule["severity"],
                    })
            elif target_check.startswith("model:"):
                # Wildcard: match any check starting with 'model:'
                for name, status in check_map.items():
                    if name and name.startswith("model:") and status in match_statuses:
                        findings.append({
                            "id": rule["id"],
                            "domain": domain_name,
                            "check": name,
                            "cause": f"Required model missing: {name}",
                            "action": rule["action"],
                            "severity": rule["severity"],
                        })
            else:
                status = check_map.get(target_check)
                if status and status in match_statuses:
                    findings.append({
                        "id": rule["id"],
                        "domain": domain_name,
                        "check": target_check,
                        "cause": rule["cause"],
                        "action": rule["action"],
                        "severity": rule["severity"],
                    })

    # Overall severity
    severities = {f["severity"] for f in findings}
    if "critical" in severities:
        overall = "critical"
    elif "high" in severities or "medium" in severities:
        overall = "degraded"
    elif "low" in severities:
        overall = "advisory"
    else:
        overall = "healthy"

    # Summary line
    if not findings:
        summary = "All systems nominal."
    else:
        top = sorted(findings, key=lambda f: ["critical","high","medium","low"].index(f["severity"]))[0]
        summary = f"{len(findings)} issue(s) detected — top: {top['cause']}"

    return {
        "overall": overall,
        "findings": findings,
        "summary": summary,
        "wall_time": snapshot.wall_time,
    }
