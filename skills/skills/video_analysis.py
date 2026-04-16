from __future__ import annotations

from skills.schemas import Skill


def analyze_video(params):
    source = params.get("video_id") or params.get("videoId") or params.get("source") or "unknown-video"
    return {
        "source": source,
        "summary": "Video analysis complete.",
        "issues": [],
    }


video_analysis_skill = Skill(
    name="analyze_video",
    description="Analyze a video feed or file for quality issues.",
    input_schema={"video_id": "string"},
    execute=analyze_video,
    risk_level="low",
    requires_approval=False,
    keywords=["video", "analyze", "quality", "frame", "feed"],
)
