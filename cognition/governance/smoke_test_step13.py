#!/usr/bin/env python3
"""
STEP 13 Smoke Tests — Autonomous Governance Layer
==================================================
Tests: 12 total covering all four engines + memory integration

Run from the workspace root:
    python andie_backend/cognition/governance/smoke_test_step13.py
"""
import sys, os
sys.path.insert(0, "/home/jamai-jamison/valhalla/andie_backend")

from cognition.governance import (
    PolicyAction, AutonomyLevel, ApprovalStatus, GovernanceEventType,
    GovernancePolicy, PolicyMatch, AutonomyDecision,
    ApprovalRequest, ApprovalOutcome, GovernanceEvent,
    BlockedAttempt, PolicyViolation,
    PolicyEngine, RiskGatekeeper, AutonomyController, ApprovalSystem,
)
from cognition.memory import MemoryRetriever
from cognition.prediction import RiskEngine

pass_count = 0
fail_count = 0


def ok(name: str) -> None:
    global pass_count
    pass_count += 1
    print(f"  PASS  {name}")


def fail(name: str, msg: str) -> None:
    global fail_count
    fail_count += 1
    print(f"  FAIL  {name}: {msg}")


def assert_eq(name, actual, expected):
    if actual == expected:
        ok(name)
    else:
        fail(name, f"got {actual!r}, want {expected!r}")


def assert_true(name, cond, note=""):
    if cond:
        ok(name)
    else:
        fail(name, note or "condition was False")


# ─────────────────────────────────────────────────────────────────────────────
print("\n── Test 1: PolicyAction severity ordering ──────────────────────────")
assert_true("allow < block severity",
            PolicyAction.ALLOW.severity < PolicyAction.BLOCK.severity)
assert_true("consensus < human",
            PolicyAction.REQUIRE_CONSENSUS.severity < PolicyAction.REQUIRE_HUMAN.severity)
assert_eq("block severity is 3", PolicyAction.BLOCK.severity, 3)

# ─────────────────────────────────────────────────────────────────────────────
print("\n── Test 2: AutonomyLevel conversions ───────────────────────────────")
assert_eq("from_policy ALLOW",
          AutonomyLevel.from_policy_action(PolicyAction.ALLOW),
          AutonomyLevel.AUTONOMOUS)
assert_eq("from_policy BLOCK",
          AutonomyLevel.from_policy_action(PolicyAction.BLOCK),
          AutonomyLevel.BLOCKED)
assert_eq("from_risk low",
          AutonomyLevel.from_risk(0.20),
          AutonomyLevel.AUTONOMOUS)
assert_eq("from_risk high",
          AutonomyLevel.from_risk(0.65),
          AutonomyLevel.HUMAN_APPROVAL)
assert_eq("from_risk critical",
          AutonomyLevel.from_risk(0.95),
          AutonomyLevel.BLOCKED)

# ─────────────────────────────────────────────────────────────────────────────
print("\n── Test 3: GovernancePolicy.matches() ──────────────────────────────")
p_low = GovernancePolicy(
    name="low_risk",
    risk_threshold=0.0,
    max_allowed_risk=0.40,
    action=PolicyAction.ALLOW,
)
p_tag = GovernancePolicy(
    name="prod_gate",
    match_tags=["prod"],
    risk_threshold=0.0,
    max_allowed_risk=1.0,
    action=PolicyAction.REQUIRE_HUMAN,
)
p_task = GovernancePolicy(
    name="deploy_prod_gate",
    match_tasks=["deploy_prod"],
    risk_threshold=0.0,
    max_allowed_risk=1.0,
    action=PolicyAction.REQUIRE_HUMAN,
    priority=10,
)

assert_true("low_risk matches risk=0.30", p_low.matches("any", [], 0.30))
assert_true("low_risk no match risk=0.50", not p_low.matches("any", [], 0.50))
assert_true("prod_gate matches prod tag",  p_tag.matches("x", ["prod"], 0.70))
assert_true("prod_gate no match without tag", not p_tag.matches("x", ["dev"], 0.70))
assert_true("task policy matches deploy_prod", p_task.matches("deploy_prod", [], 0.50))
assert_true("task policy no match for other", not p_task.matches("check_health", [], 0.50))

# ─────────────────────────────────────────────────────────────────────────────
print("\n── Test 4: PolicyEngine default policies ───────────────────────────")
engine = PolicyEngine(include_defaults=True)
assert_true("has 4 default policies", len(engine) == 4)
m_allow = engine.evaluate("anything", [], 0.30)
assert_eq("low risk → allow", m_allow.action, PolicyAction.ALLOW)

m_cons = engine.evaluate("anything", [], 0.65)
assert_eq("moderate risk → consensus", m_cons.action, PolicyAction.REQUIRE_CONSENSUS)

m_human = engine.evaluate("anything", [], 0.85)
assert_eq("high risk → human", m_human.action, PolicyAction.REQUIRE_HUMAN)

m_block = engine.evaluate("anything", [], 0.95)
assert_eq("critical risk → block", m_block.action, PolicyAction.BLOCK)

# ─────────────────────────────────────────────────────────────────────────────
print("\n── Test 5: PolicyEngine most-restrictive-wins ──────────────────────")
engine2 = PolicyEngine(include_defaults=False)
engine2.register(GovernancePolicy(
    name="allow_low", risk_threshold=0.0, max_allowed_risk=1.0,
    action=PolicyAction.ALLOW, priority=0,
))
engine2.register(GovernancePolicy(
    name="block_override", risk_threshold=0.0, max_allowed_risk=1.0,
    action=PolicyAction.BLOCK, priority=5,
))
m = engine2.evaluate("x", [], 0.50)
assert_eq("block wins over allow", m.action, PolicyAction.BLOCK)
assert_eq("block_override is winning policy", m.policy_name, "block_override")

# ─────────────────────────────────────────────────────────────────────────────
print("\n── Test 6: PolicyEngine destructive auto-tag ───────────────────────")
engine3 = PolicyEngine(include_defaults=False)
engine3.register(GovernancePolicy(
    name="no_destructive",
    match_tags=["destructive"],
    risk_threshold=0.0,
    max_allowed_risk=1.0,
    action=PolicyAction.BLOCK,
    priority=20,
))
# task name contains "delete" → should be auto-tagged as destructive
m_dest = engine3.evaluate("delete_database", [], 0.20)
assert_eq("delete task auto-tagged → block", m_dest.action, PolicyAction.BLOCK)

# ─────────────────────────────────────────────────────────────────────────────
print("\n── Test 7: AutonomyController trust score mechanics ────────────────")
mem7 = MemoryRetriever.ephemeral()
ctrl = AutonomyController(mem7, initial_trust=0.85)
assert_true("initial level is AUTONOMOUS", ctrl.current_level() == AutonomyLevel.AUTONOMOUS)
assert_true("trust score near 0.85", abs(ctrl.trust_score() - 0.85) < 0.01)

# Penalise
ctrl.record_block("deploy_prod", "high risk")
ctrl.record_violation("blocked_operations triggered")
score_after = ctrl.trust_score()
assert_true("trust decreased after block+violation",
            score_after < 0.75, f"score={score_after}")

# Reward
ctrl.record_success("check_health")
ctrl.record_success("check_health")
ctrl.record_success("check_health")

# ─────────────────────────────────────────────────────────────────────────────
print("\n── Test 8: AutonomyController tighten / relax ──────────────────────")
mem8 = MemoryRetriever.ephemeral()
ctrl8 = AutonomyController(mem8, initial_trust=0.85)
ctrl8.tighten("sentinel alert: suspicious process")
score_after_tighten = ctrl8.trust_score()
assert_true("tighten reduces trust", score_after_tighten < 0.85,
            f"score={score_after_tighten}")

ctrl8.relax("engineer cleared the alert")
score_after_relax = ctrl8.trust_score()
assert_true("relax increases trust", score_after_relax > score_after_tighten,
            f"score={score_after_relax}")

history = ctrl8.history()
assert_true("autonomy events recorded", len(history) >= 2)

# ─────────────────────────────────────────────────────────────────────────────
print("\n── Test 9: ApprovalSystem request + consensus simulate ─────────────")
mem9 = MemoryRetriever.ephemeral()
approvals = ApprovalSystem(mem9, auto_approve_threshold=0.70)

req = approvals.request(
    task_id="t9",
    task="deploy_api",
    autonomy_level=AutonomyLevel.CONSENSUS,
    risk_probability=0.55,
    reason="moderate risk requires consensus",
)
assert_true("request has an id", req.request_id.startswith("req-"))

outcome = approvals.simulate_consensus(req, participant_count=5)
assert_eq("consensus approved (risk<threshold)", outcome.status, ApprovalStatus.APPROVED)
assert_true("approved_by contains consensus info", "consensus" in (outcome.approved_by or ""))

# ─────────────────────────────────────────────────────────────────────────────
print("\n── Test 10: ApprovalSystem human rejection path ────────────────────")
mem10 = MemoryRetriever.ephemeral()
approvals10 = ApprovalSystem(mem10, auto_approve_threshold=0.70)
req10 = approvals10.request(
    task_id="t10",
    task="destroy_cluster",
    autonomy_level=AutonomyLevel.HUMAN_APPROVAL,
    risk_probability=0.88,
)
outcome10 = approvals10.simulate_human_approval(req10)
assert_eq("high risk auto-rejected", outcome10.status, ApprovalStatus.REJECTED)

# Manual resolve as APPROVED
req10b = approvals10.request(
    task_id="t10b",
    task="deploy_api",
    autonomy_level=AutonomyLevel.HUMAN_APPROVAL,
    risk_probability=0.60,
)
outcome10b = approvals10.resolve(req10b.request_id,
                                  ApprovalStatus.APPROVED,
                                  approved_by="ops_lead",
                                  reason="manually verified")
assert_eq("manual approve", outcome10b.status, ApprovalStatus.APPROVED)
assert_true("approved_by set", outcome10b.approved_by == "ops_lead")

# ─────────────────────────────────────────────────────────────────────────────
print("\n── Test 11: ApprovalSystem history + approval_rate ─────────────────")
mem11 = MemoryRetriever.ephemeral()
ap11 = ApprovalSystem(mem11, auto_approve_threshold=0.70)

for i in range(4):
    r = ap11.request(f"t11-{i}", "deploy_api",
                     AutonomyLevel.CONSENSUS, 0.55)
    ap11.simulate_consensus(r)

for i in range(2):
    r = ap11.request(f"t11-rej-{i}", "deploy_api",
                     AutonomyLevel.CONSENSUS, 0.80)
    ap11.simulate_consensus(r)

hist = ap11.approval_history("deploy_api")
assert_true("history has 6 entries", len(hist) == 6, f"got {len(hist)}")

rate = ap11.approval_rate("deploy_api")
assert_true("approval rate is 4/6 ≈ 0.667",
            abs(rate - round(4/6, 3)) < 0.01, f"rate={rate}")

# ─────────────────────────────────────────────────────────────────────────────
print("\n── Test 12: RiskGatekeeper with ephemeral memory ───────────────────")
mem12 = MemoryRetriever.ephemeral()
risk12 = RiskEngine(mem12)
engine12 = PolicyEngine(include_defaults=True)
gk = RiskGatekeeper(engine12, risk12, mem12)

# Low risk task — should be allowed
decision_allow = gk.gate("check_health", context_tags=["monitoring"],
                          task_id="t12-allow")
assert_true("check_health is not blocked", not decision_allow.blocked)

# Manually inject a high-risk policy
engine12.register(GovernancePolicy(
    name="always_block_wipe",
    match_tasks=["wipe_database"],
    risk_threshold=0.0,
    max_allowed_risk=1.0,
    action=PolicyAction.BLOCK,
    priority=100,
))
decision_block = gk.gate("wipe_database", context_tags=["destructive"],
                          task_id="t12-block")
assert_true("wipe_database is blocked", decision_block.blocked)
assert_true("block_reason is set", len(decision_block.block_reason) > 0)

# Blocked attempt should be persisted
attempts = gk.blocked_attempts(task="wipe_database")
assert_true("blocked attempt persisted", len(attempts) == 1, f"got {len(attempts)}")

# Governance events should be recorded
events = gk.governance_events()
assert_true("governance events recorded", len(events) >= 2, f"got {len(events)}")

# ─────────────────────────────────────────────────────────────────────────────
print("\n── Test 13: AutonomyDecision.is_executable() ───────────────────────")
dec_ok = AutonomyDecision(
    task_id="x", task="test",
    risk_probability=0.2,
    autonomy_level=AutonomyLevel.AUTONOMOUS,
    policy_action=PolicyAction.ALLOW,
    approved=True, blocked=False,
)
dec_blocked = AutonomyDecision(
    task_id="y", task="test",
    risk_probability=0.95,
    autonomy_level=AutonomyLevel.BLOCKED,
    policy_action=PolicyAction.BLOCK,
    approved=False, blocked=True, block_reason="test block",
)
assert_true("approved decision is_executable", dec_ok.is_executable())
assert_true("blocked decision not is_executable", not dec_blocked.is_executable())

# ─────────────────────────────────────────────────────────────────────────────
print("\n── Test 14: GovernancePolicy disabled flag ─────────────────────────")
eng14 = PolicyEngine(include_defaults=False)
eng14.register(GovernancePolicy(
    name="disabled_block",
    risk_threshold=0.0, max_allowed_risk=1.0,
    action=PolicyAction.BLOCK,
    enabled=False,
))
# Should not match because policy is disabled
m14 = eng14.evaluate("anything", [], 0.95)
assert_eq("disabled policy does not fire (fallback allow)",
          m14.action, PolicyAction.ALLOW)

# ─────────────────────────────────────────────────────────────────────────────
# Summary
print(f"\n{'─'*55}")
total = pass_count + fail_count
print(f"STEP 13 RESULTS: {pass_count}/{total} passed  ({fail_count} failed)")
if fail_count:
    print("  ← STEP 13 NOT COMPLETE — fix failures above")
else:
    print("  ← STEP 13 COMPLETE ✓")
print(f"{'─'*55}\n")
sys.exit(0 if fail_count == 0 else 1)
