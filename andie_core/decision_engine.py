# andie_core/decision_engine.py

def compute_health_score(cpu, memory, llm_active):
    score = 100
    if cpu > 80:
        score -= 30
    elif cpu > 50:
        score -= 10
    if memory > 80:
        score -= 20
    if not llm_active:
        score -= 50
    return score

def classify(score):
    if score >= 80:
        return "healthy"
    elif score >= 50:
        return "degraded"
    else:
        return "critical"
