from __future__ import annotations

from skills.registry import registry
from skills.skills.audio_fix import audio_fix_skill
from skills.skills.check_service_status import check_service_status_skill
from skills.skills.server_restart import server_restart_skill
from skills.skills.video_analysis import video_analysis_skill
from skills.skills.verify_stream_health import verify_stream_health_skill


def register_builtin_skills() -> None:
    audio_fix_skill.depends_on = ["analyze_video"]
    for skill in [
        audio_fix_skill,
        video_analysis_skill,
        verify_stream_health_skill,
        check_service_status_skill,
        server_restart_skill,
    ]:
        registry.register(skill)


register_builtin_skills()
