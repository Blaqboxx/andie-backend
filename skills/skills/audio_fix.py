from __future__ import annotations

from skills.schemas import Skill


def fix_audio_sync(params):
    stream_id = params.get("stream_id") or params.get("streamId") or "unknown-stream"
    return f"Audio resynced for stream {stream_id}"


audio_fix_skill = Skill(
    name="resync_audio",
    description="Fix audio and video desync in a stream.",
    input_schema={"stream_id": "string"},
    execute=fix_audio_sync,
    risk_level="low",
    requires_approval=False,
    keywords=["audio", "desync", "sync", "stream", "resync"],
    depends_on=["analyze_video"],
)
