from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Literal

IntentType = Literal[
    "ARTIFACT_BUILD",
    "SYSTEM_ACTION",
    "WORKFLOW_EXECUTION",
    "CODE_GENERATION",
    "ANALYSIS",
    "CHAT_RESPONSE",
    "DIAGNOSTIC",
]

_BUILD_PATTERNS = re.compile(
    r"\b(build|create|generate|make|scaffold|write|produce|output|deploy|"
    r"spin up|set up|initialize|init|bootstrap)\b.{0,60}"
    r"\b(app|script|file|project|tool|widget|dashboard|plugin|extension|"
    r"userscript|tampermonkey|bot|service|api|server|cli|website|page|component|"
    r"module|package|repo|workspace|artifact)\b",
    re.IGNORECASE,
)

_SYSTEM_PATTERNS = re.compile(
    r"\b(restart|kill|stop|start|run|exec|execute|launch|open|close|"
    r"install|uninstall|update|upgrade|reboot|shutdown|ping|ssh|deploy)\b",
    re.IGNORECASE,
)


_DIAGNOSTIC_PATTERNS = re.compile(
    r"\b(why is|is .* (up|down|running|healthy|reachable|broken|slow)|"
    r"check|diagnose|status of|ping|can you reach|are .* (running|online)|"
    r"what'?s wrong|show (me )?logs|health check|service status|"
    r"container status|disk space|storage|gpu status|model status|"
    r"is ollama|is redis|is .* working|network status|connectivity)\b",
    re.IGNORECASE,
)

_CODE_PATTERNS = re.compile(
    r"\b(function|class|method|snippet|code|implement|refactor|fix|debug|"
    r"write code|show me|give me|example of)\b",
    re.IGNORECASE,
)


@dataclass
class Intent:
    type: IntentType
    confidence: float
    target: str
    action: str
    raw: str


def classify(message: str) -> Intent:
    msg = message.strip()
    if _BUILD_PATTERNS.search(msg):
        m = re.search(
            r"\b(?:build|create|generate|make|write|produce)\b\s+(?:me\s+)?(?:a\s+|an\s+)?(.+?)(?:\s+for|\s+that|\s+with|\s+using|$)",
            msg, re.IGNORECASE
        )
        target = m.group(1).strip() if m else msg
        return Intent(type="ARTIFACT_BUILD", confidence=0.92, target=target, action="generate_artifact", raw=msg)
    if _SYSTEM_PATTERNS.search(msg):
        return Intent(type="SYSTEM_ACTION", confidence=0.85, target=msg, action="system_execute", raw=msg)
    if _DIAGNOSTIC_PATTERNS.search(msg):
        # Extract domain hint if present
        domain = "all"
        for word in ("container", "network", "gpu", "storage", "model", "disk", "ollama", "redis"):
            if word in msg.lower():
                domain = word
                break
        return Intent(type="DIAGNOSTIC", confidence=0.88, target=domain, action="run_diagnostics", raw=msg)
    if _CODE_PATTERNS.search(msg):
        return Intent(type="CODE_GENERATION", confidence=0.80, target=msg, action="generate_code", raw=msg)
    return Intent(type="CHAT_RESPONSE", confidence=0.70, target=msg, action="chat", raw=msg)
