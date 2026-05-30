from __future__ import annotations

from andie_backend.skills import registry as _registry_module
from andie_backend.skills.skills.audio_fix import audio_fix_skill
from andie_backend.skills.skills.check_service_status import check_service_status_skill
from andie_backend.skills.skills.image_analysis import image_analysis_skill
from andie_backend.skills.skills.server_restart import server_restart_skill
from andie_backend.skills.skills.video_analysis import video_analysis_skill
from andie_backend.skills.skills.verify_stream_health import verify_stream_health_skill

# Compatibility: depending on import order/path shims, this can be either
# the registry object itself or the registry module exposing `registry`.
_registry = getattr(_registry_module, "registry", _registry_module)


def register_builtin_skills() -> None:
    audio_fix_skill.depends_on = ["analyze_video"]
    for skill in [
        audio_fix_skill,
        image_analysis_skill,
        video_analysis_skill,
        verify_stream_health_skill,
        check_service_status_skill,
        server_restart_skill,
    ]:
        if hasattr(_registry, "register"):
            _registry.register(skill)


register_builtin_skills()
