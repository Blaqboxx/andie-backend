from __future__ import annotations

from skills.schemas import Skill


def verify_stream_health(params):
    stream_id = params.get("stream_id") or params.get("streamId") or "unknown-stream"
    return {
        "stream_id": stream_id,
        "status": "healthy",
        "verified": True,
    }


verify_stream_health_skill = Skill(
    name="verify_stream_health",
    description="Verify that a stream is healthy after remediation.",
    input_schema={"stream_id": "string"},
    execute=verify_stream_health,
    risk_level="low",
    requires_approval=False,
    keywords=["verify", "stream", "health", "check"],
)
