from __future__ import annotations

from typing import Dict

PROFILES: Dict[str, Dict[str, float]] = {
    "conservative": {
        "auto_threshold": 0.85,
        "review_threshold": 0.65,
        "block_threshold": 0.40,
    },
    "balanced": {
        "auto_threshold": 0.75,
        "review_threshold": 0.50,
        "block_threshold": 0.30,
    },
    "aggressive": {
        "auto_threshold": 0.60,
        "review_threshold": 0.40,
        "block_threshold": 0.20,
    },
}

DEFAULT_PROFILE = "balanced"
