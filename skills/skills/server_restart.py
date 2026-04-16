from __future__ import annotations

from skills.schemas import Skill


def propose_server_restart(params):
    service = params.get("service") or params.get("target") or "backend"
    return {
        "service": service,
        "action": "restart_requested",
        "message": f"Restart approved for {service}.",
    }


server_restart_skill = Skill(
    name="restart_server",
    description="Restart a backend or service process.",
    input_schema={"service": "string"},
    execute=propose_server_restart,
    risk_level="high",
    requires_approval=True,
    keywords=["restart", "server", "backend", "service", "reboot"],
    depends_on=["check_service_status"],
)
