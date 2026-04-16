from __future__ import annotations

from skills.schemas import Skill


def check_service_status(params):
    service = params.get("service") or params.get("target") or "backend"
    return {
        "service": service,
        "status": "running",
    }


check_service_status_skill = Skill(
    name="check_service_status",
    description="Check the health and status of a backend service.",
    input_schema={"service": "string"},
    execute=check_service_status,
    risk_level="low",
    requires_approval=False,
    keywords=["check", "service", "status", "health"],
)
