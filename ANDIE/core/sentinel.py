def validate_message(msg: str):

# Sentinel v2: behavior scoring, pattern banning, threat escalation

PATTERN_BANLIST = ["shutdown", "delete all", "format", "rm -rf", "drop database", "kill process"]
THREAT_PATTERNS = ["attack", "exploit", "bypass", "privilege escalation"]

def validate_message(msg: str):
    lowered = msg.lower()
    # Pattern banning
    for bad in PATTERN_BANLIST:
        if bad in lowered:
            escalate_threat(msg, reason=f"Pattern banned: {bad}")
            return False, f"unsafe command: {bad}"
    # Threat escalation
    for threat in THREAT_PATTERNS:
        if threat in lowered:
            escalate_threat(msg, reason=f"Threat pattern: {threat}")
            return False, f"threat detected: {threat}"
    # Behavior scoring (simple: penalize suspicious words)
    score = 1.0
    if any(word in lowered for word in ["force", "override", "root", "admin"]):
        score -= 0.5
    if len(msg) > 200:
        score -= 0.2
    if score < 0.5:
        escalate_threat(msg, reason="Low behavior score")
        return False, "message flagged as risky"
    return True, ""

def escalate_threat(msg, reason=""):
    # Log or trigger escalation (could notify admin, block user, etc.)
    print(f"[SENTINEL] THREAT ESCALATION: {reason} | msg: {msg}")
