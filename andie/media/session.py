"""
andie/media/session.py
In-memory media session registry — tracks active camera/screen/audio sessions
and acts as the source-of-truth for the media router.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class MediaSession:
    session_id: str
    source: str          # "camera" | "screen" | "audio"
    started_at: float = field(default_factory=time.monotonic)
    frame_count: int = 0
    last_frame_at: Optional[float] = None
    transcript: str = ""
    active: bool = True

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "source": self.source,
            "active": self.active,
            "frame_count": self.frame_count,
            "elapsed_s": round(time.monotonic() - self.started_at, 1),
            "transcript_len": len(self.transcript),
        }

# Module-level session store
_sessions: dict[str, MediaSession] = {}

def start_session(source: str) -> MediaSession:
    sid = f"{source}-{int(time.time()*1000)}"
    sess = MediaSession(session_id=sid, source=source)
    _sessions[sid] = sess
    return sess

def get_session(sid: str) -> Optional[MediaSession]:
    return _sessions.get(sid)

def stop_session(sid: str) -> bool:
    if sid in _sessions:
        _sessions[sid].active = False
        return True
    return False

def get_active(source: Optional[str] = None) -> list[MediaSession]:
    return [s for s in _sessions.values() if s.active and (source is None or s.source == source)]

def all_sessions() -> list[MediaSession]:
    return list(_sessions.values())
